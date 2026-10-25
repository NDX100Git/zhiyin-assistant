"""老师端接口测试：登录、队列、打印推进、核销、异常、收单开关、统计、公告。"""

from __future__ import annotations

import itertools
from datetime import date

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.notice import Notice
from app.models.order import Order
from app.models.payment_log import PaymentLog
from app.models.print_log import PrintLog
from app.models.user import User

_seq = itertools.count(1)


def _full_code(tail: str, day: date | None = None) -> str:
    """取件码是日期前缀加当日短码，如 261017-A-01。"""
    return f"{(day or date.today()):%y%m%d}-{tail}"


def _fullwidth_digits(text: str) -> str:
    """把半角数字换成手机输入法打出的全角数字（０ 至 ９）。"""
    return text.translate({ord(str(i)): chr(0xFF10 + i) for i in range(10)})


def _with_dash(text: str, dash: str) -> str:
    """把取件码里的半角短横换成指定样式的连接符。"""
    return text.replace("-", dash)


def _make_order(db_session, status: str = "queuing", pickup_code: str | None = None,
                paid: int = 0, **kwargs) -> Order:
    index = next(_seq)
    # 取件码现在有唯一索引，不传就按序号造一个不重复的短码
    tail = pickup_code or f"{chr(65 + index % 26)}-{index % 99 + 1:02d}"
    user = User(openid=f"admin-test-{index}", auth_provider="device")
    db_session.add(user)
    db_session.flush()
    order = Order(
        order_no=f"0101-{index:03d}",
        pickup_code=_full_code(tail),
        user_id=user.id,
        status=status,
        page_count=42,
        print_side=2,
        color_mode=0,
        copies=1,
        paper_size="A4",
        amount_cents=630,
        paid=paid,
        file_key=f"files/2026/10/{index:032x}.pdf",
        file_name="毕业论文.pdf",
        **kwargs,
    )
    db_session.add(order)
    db_session.commit()
    return order


def admin_token(client) -> str:
    body = client.post(
        "/api/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    ).json()
    assert body["code"] == 0, body
    return body["data"]["token"]


def student_token(client) -> str:
    body = client.post("/api/auth/login", json={"device_id": "admin-test-device"}).json()
    return body["data"]["token"]


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_管理员登录成功与失败(client):
    ok_body = client.post(
        "/api/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    ).json()
    assert ok_body["code"] == 0
    assert ok_body["data"]["admin"]["role"] == "admin"

    bad = client.post("/api/admin/login", json={"username": "admin", "password": "wrong"}).json()
    assert bad["code"] == 40102
    assert bad["message"] == "账号或密码不正确"


def test_学生token不能访问管理接口(client):
    body = client.get("/api/admin/queue", headers=auth(student_token(client))).json()
    assert body["code"] == 40300


def test_未登录访问管理接口被拒(client):
    assert client.get("/api/admin/queue").json()["code"] == 40100


def test_队列按订单号排序且支持筛选与搜索(client, db_session):
    token = admin_token(client)
    _make_order(db_session, status="queuing", pickup_code="A-01")
    _make_order(db_session, status="ready", pickup_code="B-12")

    all_orders = client.get("/api/admin/queue", headers=auth(token)).json()["data"]
    assert all_orders["total"] == 2
    assert [item["order_no"] for item in all_orders["items"]] == sorted(
        item["order_no"] for item in all_orders["items"]
    )

    queuing = client.get(
        "/api/admin/queue", params={"status": "queuing"}, headers=auth(token)
    ).json()["data"]
    assert queuing["total"] == 1
    assert queuing["items"][0]["status"] == "queuing"

    searched = client.get(
        "/api/admin/queue", params={"keyword": "B-12"}, headers=auth(token)
    ).json()["data"]
    assert searched["total"] == 1
    assert searched["items"][0]["pickup_code"] == _full_code("B-12")


def test_开始打印然后完成打印(client, db_session, test_storage):
    token = admin_token(client)
    order = _make_order(db_session, status="queuing")

    started = client.post(f"/api/admin/order/{order.id}/start", headers=auth(token)).json()
    assert started["code"] == 0, started
    assert started["data"]["order"]["status"] == "printing"
    assert started["data"]["print_job"]["provider"] == "manual"
    assert started["data"]["print_job"]["file_url"].startswith("http")

    done = client.post(f"/api/admin/order/{order.id}/complete", headers=auth(token)).json()
    assert done["code"] == 0, done
    assert done["data"]["order"]["status"] == "ready"
    assert done["data"]["system_amount_cents"] == 630
    assert done["data"]["price_changed"] is False

    logs = list(
        db_session.execute(select(PrintLog).where(PrintLog.order_id == order.id)).scalars()
    )
    transitions = {(log.from_status, log.to_status) for log in logs}
    assert ("queuing", "printing") in transitions
    assert ("printing", "ready") in transitions
    assert all(log.operator for log in logs)


def test_老师改价写入操作日志(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="printing")

    body = client.post(
        f"/api/admin/order/{order.id}/complete",
        json={"amount_cents": 700},
        headers=auth(token),
    ).json()
    assert body["code"] == 0, body
    assert body["data"]["amount_cents"] == 700
    assert body["data"]["price_changed"] is True

    db_session.expire_all()
    refreshed = db_session.get(Order, order.id)
    assert refreshed.amount_cents == 700

    logs = list(
        db_session.execute(select(PrintLog).where(PrintLog.order_id == order.id)).scalars()
    )
    assert any("老师改价" in (log.note or "") for log in logs)
    assert any("630" in (log.note or "") and "700" in (log.note or "") for log in logs)


def test_不传金额时沿用成交价不被系统价覆盖(client, db_session):
    """换文件流程里老师确认的价格，不能被系统重算价无声抹掉。"""
    token = admin_token(client)
    order = _make_order(db_session, status="printing")
    # 模拟换文件确认后落库的成交价：下单 630，老师确认改到 800
    order.amount_cents = 800
    db_session.commit()

    body = client.post(
        f"/api/admin/order/{order.id}/complete", headers=auth(token)
    ).json()

    assert body["code"] == 0, body
    assert body["data"]["system_amount_cents"] == 630  # 系统价照常返回，只供参考
    assert body["data"]["amount_cents"] == 800  # 实收沿用成交价
    assert body["data"]["price_changed"] is True

    db_session.expire_all()
    assert db_session.get(Order, order.id).amount_cents == 800


def test_批量完成沿用成交价(client, db_session):
    """批量完成没有传金额的入口，必须自己沿用成交价。"""
    token = admin_token(client)
    order = _make_order(db_session, status="printing")
    order.amount_cents = 800
    db_session.commit()

    body = client.post(
        "/api/admin/order/batch-complete",
        json={"order_ids": [order.id]},
        headers=auth(token),
    ).json()

    assert body["code"] == 0, body
    assert body["data"]["succeeded"][0]["amount_cents"] == 800


def test_批量标记完成返回成功与失败明细(client, db_session):
    token = admin_token(client)
    first = _make_order(db_session, status="printing")
    second = _make_order(db_session, status="printing")
    third = _make_order(db_session, status="queuing")  # 状态不对，应失败

    body = client.post(
        "/api/admin/order/batch-complete",
        json={"order_ids": [first.id, second.id, third.id]},
        headers=auth(token),
    ).json()
    assert body["code"] == 0
    assert len(body["data"]["succeeded"]) == 2
    assert body["data"]["failed"][0]["order_id"] == third.id
    assert "不能变更" in body["data"]["failed"][0]["message"]


def test_核销取件并现场收款(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="ready", paid=0, pickup_code="C-07")

    body = client.post(f"/api/admin/order/{order.id}/verify", headers=auth(token)).json()
    assert body["code"] == 0, body
    assert body["data"]["order"]["status"] == "completed"
    assert body["data"]["collected_onsite"] is True

    db_session.expire_all()
    refreshed = db_session.get(Order, order.id)
    assert refreshed.paid == 1
    assert refreshed.pay_method == "onsite"
    assert refreshed.picked_at is not None

    payments = list(
        db_session.execute(select(PaymentLog).where(PaymentLog.order_id == order.id)).scalars()
    )
    assert len(payments) == 1
    assert payments[0].method == "onsite"
    assert payments[0].amount_cents == 630


def test_已在线支付订单核销时不重复记流水(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="ready", paid=1, pickup_code="D-08")
    # 已支付订单应当有一条足额支付流水（核销只补差额，不重复记账）
    db_session.add(PaymentLog(order_id=order.id, amount_cents=630, method="mock", operator="student"))
    db_session.commit()

    body = client.post(f"/api/admin/order/{order.id}/verify", headers=auth(token)).json()
    assert body["code"] == 0
    assert body["data"]["collected_onsite"] is False
    payments = list(
        db_session.execute(select(PaymentLog).where(PaymentLog.order_id == order.id)).scalars()
    )
    assert len(payments) == 1
    assert payments[0].amount_cents == 630


def test_按取件码核销(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="ready", pickup_code="E-42")

    body = client.post(
        "/api/admin/order/verify-code", json={"pickup_code": "e-42"}, headers=auth(token)
    ).json()
    assert body["code"] == 0, body
    assert body["data"]["order"]["id"] == order.id
    assert body["data"]["order"]["status"] == "completed"

    missing = client.post(
        "/api/admin/order/verify-code", json={"pickup_code": "Z-99"}, headers=auth(token)
    ).json()
    assert missing["code"] == 40401


def _verify_by_code(client, token: str, pickup_code: str) -> dict:
    return client.post(
        "/api/admin/order/verify-code",
        json={"pickup_code": pickup_code},
        headers=auth(token),
    ).json()


def test_半角完整码核销_回归(client, db_session):
    """回归：学生端展示与扫码用的半角完整码仍可核销。"""
    token = admin_token(client)
    order = _make_order(db_session, status="ready", pickup_code="M-31")
    code = _full_code("M-31")

    body = _verify_by_code(client, token, code)
    assert body["code"] == 0, body
    assert body["data"]["order"]["id"] == order.id
    assert body["data"]["order"]["status"] == "completed"


def test_全角短横的完整码能核销(client, db_session):
    """真机问题：手机输入法给出全角短横「－」（U+FF0D）。"""
    token = admin_token(client)
    order = _make_order(db_session, status="ready", pickup_code="0-32")
    typed = _with_dash(_full_code("0-32"), "－")
    assert typed != _full_code("0-32")

    body = _verify_by_code(client, token, typed)
    assert body["code"] == 0, body
    assert body["data"]["order"]["id"] == order.id

    # 归一化只作用于输入，库里的取件码保持不变
    db_session.expire_all()
    assert db_session.get(Order, order.id).pickup_code == _full_code("0-32")


def test_全角数字的完整码能核销(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="ready", pickup_code="H-07")
    typed = _fullwidth_digits(_full_code("H-07"))

    body = _verify_by_code(client, token, typed)
    assert body["code"] == 0, body
    assert body["data"]["order"]["id"] == order.id


def test_中文破折号的完整码能核销(client, db_session):
    """中文习惯的破折号样连接符也要归成半角短横。"""
    token = admin_token(client)
    order = _make_order(db_session, status="ready", pickup_code="N-58")
    typed = _with_dash(_fullwidth_digits(_full_code("N-58")), "—")

    body = _verify_by_code(client, token, typed)
    assert body["code"] == 0, body
    assert body["data"]["order"]["id"] == order.id


def test_全角空格包裹的当天短码能核销(client, db_session):
    """只输当天短码时按服务器当天补全日期，前后全角空格、全角数字与全角短横都要认。"""
    token = admin_token(client)
    order = _make_order(db_session, status="ready", pickup_code="F-05")
    typed = "　" + _with_dash(_fullwidth_digits("f-05"), "－") + "　"

    body = _verify_by_code(client, token, typed)
    assert body["code"] == 0, body
    assert body["data"]["order"]["id"] == order.id
    assert body["data"]["order"]["pickup_code"] == _full_code("F-05")


def test_不存在的码仍返回40401并回显原始输入(client, db_session):
    """核销判定不放宽：查不到还是 40401，提示里带的是老师输入的那一份。"""
    token = admin_token(client)
    _make_order(db_session, status="ready", pickup_code="P-11")
    typed = _with_dash(_fullwidth_digits("Z-99"), "－")

    body = _verify_by_code(client, token, typed)
    assert body["code"] == 40401
    assert body["message"] == f"没有找到取件码 {typed} 对应的待取件订单"
    assert "Z-99" not in body["message"]


def test_异常_恢复_关闭三种处置(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="queuing")

    marked = client.post(
        f"/api/admin/order/{order.id}/abnormal",
        json={"reason": "卡纸"},
        headers=auth(token),
    ).json()
    assert marked["data"]["status"] == "abnormal"

    recovered = client.post(f"/api/admin/order/{order.id}/recover", headers=auth(token)).json()
    assert recovered["data"]["status"] == "queuing"

    client.post(
        f"/api/admin/order/{order.id}/abnormal", json={"reason": "文件损坏"}, headers=auth(token)
    )
    closed = client.post(
        f"/api/admin/order/{order.id}/cancel",
        json={"reason": "文件损坏，联系学生重传"},
        headers=auth(token),
    ).json()
    assert closed["code"] == 0, closed
    assert closed["data"]["status"] == "cancelled"
    assert closed["data"]["cancel_reason"] == "文件损坏，联系学生重传"

    # 正常订单不能被老师关闭
    normal = _make_order(db_session, status="queuing")
    refused = client.post(f"/api/admin/order/{normal.id}/cancel", headers=auth(token)).json()
    assert refused["code"] == 40903

    logs = list(
        db_session.execute(select(PrintLog).where(PrintLog.order_id == order.id)).scalars()
    )
    assert any(log.action == "abnormal" and "文件损坏" in (log.note or "") for log in logs)


def test_收单开关影响学生端提示(client, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.session_service.resolve_session_name", lambda now: "pm"
    )
    admin = admin_token(client)
    student = student_token(client)

    closed = client.post(
        "/api/admin/session/close",
        json={"date": date.today().isoformat(), "session": "pm"},
        headers=auth(admin),
    ).json()
    assert closed["code"] == 0, closed
    assert closed["data"]["is_open"] is False

    summary = client.get("/api/home/summary", headers=auth(student)).json()["data"]
    assert summary["order_session"]["can_order"] is True
    assert summary["order_session"]["deferred"] is True
    assert "下一个打印时段" in summary["order_session"]["hint"]

    opened = client.post(
        "/api/admin/session/open",
        json={"date": date.today().isoformat(), "session": "pm"},
        headers=auth(admin),
    ).json()
    assert opened["data"]["is_open"] is True


def test_今日数据统计(client, db_session):
    token = admin_token(client)
    _make_order(db_session, status="queuing")
    _make_order(db_session, status="ready")
    _make_order(db_session, status="abnormal")

    body = client.get("/api/admin/summary", headers=auth(token)).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["order_count"] == 3
    assert data["ready_count"] == 1
    assert data["abnormal_count"] == 1
    assert data["queuing_count"] == 1
    assert data["income_cents"] == 0


def test_发布公告后学生端可见(client, db_session):
    admin = admin_token(client)
    student = student_token(client)

    published = client.post(
        "/api/admin/notice",
        json={"content": "上午设备检修，打印时间可能顺延"},
        headers=auth(admin),
    ).json()
    assert published["code"] == 0, published
    assert published["data"]["created_by"] == settings.admin_username

    notices = client.get("/api/notice/list", headers=auth(student)).json()["data"]["items"]
    assert any("设备检修" in item["content"] for item in notices)

    rows = list(db_session.execute(select(Notice)).scalars())
    assert len(rows) == 1


def test_系统报价供完成弹窗预填(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="printing")

    body = client.get(f"/api/admin/order/{order.id}/quote", headers=auth(token)).json()
    assert body["code"] == 0
    assert body["data"]["system_amount_cents"] == 630
    assert body["data"]["current_amount_cents"] == 630


def test_收单时段总览返回上午下午(client):
    token = admin_token(client)
    body = client.get(
        "/api/admin/sessions", params={"date": date.today().isoformat()}, headers=auth(token)
    ).json()

    assert body["code"] == 0
    assert [item["session"] for item in body["data"]["items"]] == ["am", "pm"]
    assert body["data"]["items"][0]["label"] == "上午"


def test_收款流水按日期汇总(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="completed", paid=1)
    db_session.add(PaymentLog(order_id=order.id, amount_cents=630, method="mock", operator="student"))
    db_session.add(PaymentLog(order_id=order.id, amount_cents=100, method="onsite", operator="admin"))
    db_session.commit()

    body = client.get(
        "/api/admin/transactions",
        params={"date": date.today().isoformat()},
        headers=auth(token),
    ).json()["data"]

    assert body["count"] == 2
    assert body["total_cents"] == 730
    assert {item["method"] for item in body["items"]} == {"mock", "onsite"}


def test_改价上调后核销只补收差额(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="printing", paid=1)
    db_session.add(PaymentLog(order_id=order.id, amount_cents=75, method="mock", operator="student"))
    db_session.commit()

    completed = client.post(
        f"/api/admin/order/{order.id}/complete",
        json={"amount_cents": 800},
        headers=auth(token),
    ).json()
    assert completed["code"] == 0
    assert completed["data"]["order"]["paid"] == 0  # 已付金额不足，标记需补款

    db_session.expire_all()
    logs = list(
        db_session.execute(select(PrintLog).where(PrintLog.order_id == order.id)).scalars()
    )
    assert any("需补收 725 分" in (log.note or "") for log in logs)

    verified = client.post(f"/api/admin/order/{order.id}/verify", headers=auth(token)).json()
    assert verified["data"]["collected_onsite"] is True
    assert verified["data"]["collected_cents"] == 725

    db_session.expire_all()
    payments = list(
        db_session.execute(select(PaymentLog).where(PaymentLog.order_id == order.id)).scalars()
    )
    assert sorted(p.amount_cents for p in payments) == [75, 725]
    assert db_session.get(Order, order.id).paid == 1


# ---------------------------------------------------------------- 退款（关闭已付款异常单）


def _paid_order(db_session, amount_cents: int = 630, **kwargs) -> Order:
    """造一张已在线支付的订单，并落一条对应的支付流水。"""
    order = _make_order(db_session, status="queuing", paid=1, **kwargs)
    db_session.add(
        PaymentLog(
            order_id=order.id,
            amount_cents=amount_cents,
            method="mock",
            operator="student",
            idempotency_key=f"pay:{order.id}",
        )
    )
    db_session.commit()
    return order


def test_关闭已付款异常单登记退款并冲减当日收入(client, db_session):
    """已收过钱的单关单必须登记退款，当日收入要把退款排除。"""
    token = admin_token(client)
    order = _paid_order(db_session, pickup_code="R-01")

    before = client.get("/api/admin/summary", headers=auth(token)).json()["data"]
    assert before["income_cents"] == 630

    client.post(
        f"/api/admin/order/{order.id}/abnormal", json={"reason": "缺墨"}, headers=auth(token)
    )
    closed = client.post(
        f"/api/admin/order/{order.id}/cancel",
        json={"reason": "缺墨，无法打印"},
        headers=auth(token),
    ).json()
    assert closed["code"] == 0, closed
    assert closed["data"]["status"] == "cancelled"
    assert closed["data"]["refund_cents"] == 630  # 不传金额时按成交价全额退
    assert closed["data"]["paid_total_cents"] == 630
    assert "退款 630 分" in closed["data"]["cancel_reason"]

    db_session.expire_all()
    payments = list(
        db_session.execute(select(PaymentLog).where(PaymentLog.order_id == order.id)).scalars()
    )
    assert sorted(p.amount_cents for p in payments) == [-630, 630]
    refund = next(p for p in payments if p.method == "refund")
    assert refund.idempotency_key == f"refund:{order.id}"
    assert refund.operator == settings.admin_username

    after = client.get("/api/admin/summary", headers=auth(token)).json()["data"]
    assert after["income_cents"] == 0  # 退款已从当日收入里排除

    transactions = client.get(
        "/api/admin/transactions",
        params={"date": date.today().isoformat()},
        headers=auth(token),
    ).json()["data"]
    assert transactions["total_cents"] == 0
    assert {item["method"] for item in transactions["items"]} == {"mock", "refund"}

    logs = list(
        db_session.execute(select(PrintLog).where(PrintLog.order_id == order.id)).scalars()
    )
    assert any(log.action == "refund" and "630" in (log.note or "") for log in logs)


def test_可以指定部分退款金额(client, db_session):
    """老师按实际情况退一部分：退款金额可传，当日收入只冲掉退掉的那部分。"""
    from app.services import admin_service

    token = admin_token(client)
    order = _paid_order(db_session, pickup_code="R-02")
    admin_service.mark_abnormal(db_session, order, "打印质量有问题", settings.admin_username)

    result = admin_service.cancel_abnormal(
        db_session, order, "打印质量有问题", settings.admin_username, refund_cents=200
    )

    assert result["refund_cents"] == 200
    assert result["status"] == "cancelled"
    summary = client.get("/api/admin/summary", headers=auth(token)).json()["data"]
    assert summary["income_cents"] == 430


def test_退款金额不能超过已收金额(client, db_session):
    """退款上限是实际收到的钱：多退会把当日收入冲成负数。"""
    from app.services import admin_service
    from app.utils.exceptions import BusinessError

    order = _paid_order(db_session, pickup_code="R-03")
    admin_service.mark_abnormal(db_session, order, "学生有异议", settings.admin_username)

    with pytest.raises(BusinessError) as exc:
        admin_service.cancel_abnormal(
            db_session, order, "学生有异议", settings.admin_username, refund_cents=700
        )
    assert exc.value.code == 40062
    assert "630" in exc.value.message
    db_session.rollback()

    db_session.expire_all()
    assert db_session.get(Order, order.id).status == "abnormal"
    payments = list(
        db_session.execute(select(PaymentLog).where(PaymentLog.order_id == order.id)).scalars()
    )
    assert [p.amount_cents for p in payments] == [630]  # 没有退款行落地


def test_未付款的异常单关单不产生退款流水(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="abnormal", paid=0, pickup_code="R-04")

    closed = client.post(
        f"/api/admin/order/{order.id}/cancel", json={"reason": "学生不要了"}, headers=auth(token)
    ).json()

    assert closed["code"] == 0, closed
    assert closed["data"]["refund_cents"] == 0
    assert list(
        db_session.execute(select(PaymentLog).where(PaymentLog.order_id == order.id)).scalars()
    ) == []


# ---------------------------------------------------------------- 异常单重打回路


def test_已打印的异常单恢复后不回打印队列(client, db_session, test_storage):
    """已出纸的单只能恢复成待取件：不能再进打印队列，printed_at 也不能被覆盖。"""
    token = admin_token(client)
    order = _make_order(db_session, status="queuing", paid=1, pickup_code="Q-21")

    client.post(f"/api/admin/order/{order.id}/start", headers=auth(token))
    done = client.post(f"/api/admin/order/{order.id}/complete", headers=auth(token)).json()
    printed_at = done["data"]["order"]["printed_at"]
    assert printed_at

    client.post(
        f"/api/admin/order/{order.id}/abnormal", json={"reason": "学生有异议"}, headers=auth(token)
    )
    recovered = client.post(f"/api/admin/order/{order.id}/recover", headers=auth(token)).json()

    assert recovered["code"] == 0, recovered
    assert recovered["data"]["status"] == "ready"
    assert recovered["data"]["printed_at"] == printed_at  # 原始打印时间保留
    assert recovered["data"]["queue_position"] == 0

    queuing = client.get(
        "/api/admin/queue", params={"status": "queuing"}, headers=auth(token)
    ).json()["data"]
    assert queuing["total"] == 0  # 没有回到打印队列

    again = client.post(f"/api/admin/order/{order.id}/start", headers=auth(token)).json()
    assert again["code"] == 40900  # 状态机挡住重复打印
    assert "不能变更" in again["message"]


def test_未打印的异常单恢复后回到队列(client, db_session):
    token = admin_token(client)
    order = _make_order(db_session, status="queuing", pickup_code="Q-22")

    client.post(
        f"/api/admin/order/{order.id}/abnormal", json={"reason": "卡纸"}, headers=auth(token)
    )
    recovered = client.post(f"/api/admin/order/{order.id}/recover", headers=auth(token)).json()

    assert recovered["data"]["status"] == "queuing"
    assert recovered["data"]["printed_at"] is None


# ---------------------------------------------------------------- 批量完成


def test_批量完成中途非业务异常不中断整批(client, db_session, monkeypatch):
    """一单抛运行时错误（断连、锁超时）时，其余单调照样完成，明细里只有那一单失败。"""
    from app.services import admin_service

    token = admin_token(client)
    first = _make_order(db_session, status="printing", pickup_code="B-01")
    broken = _make_order(db_session, status="printing", pickup_code="B-02")
    last = _make_order(db_session, status="printing", pickup_code="B-03")

    original = admin_service.complete_print

    def flaky(db, order, operator, amount_cents=None):
        if order.id == broken.id:
            raise RuntimeError("模拟数据库连接断开")
        return original(db, order, operator, amount_cents)

    monkeypatch.setattr(admin_service, "complete_print", flaky)

    body = client.post(
        "/api/admin/order/batch-complete",
        json={"order_ids": [first.id, broken.id, last.id]},
        headers=auth(token),
    ).json()

    assert body["code"] == 0, body
    assert [item["order_id"] for item in body["data"]["succeeded"]] == [first.id, last.id]
    assert [item["order_id"] for item in body["data"]["failed"]] == [broken.id]
    assert "请稍后重试" in body["data"]["failed"][0]["message"]

    db_session.expire_all()
    assert db_session.get(Order, first.id).status == "ready"
    assert db_session.get(Order, last.id).status == "ready"
    assert db_session.get(Order, broken.id).status == "printing"


def test_批量完成重复订单号只处理一次(client, db_session):
    """同一单传两次只处理一次，明细里不出现「第二次撞状态机」的假失败。"""
    token = admin_token(client)
    order = _make_order(db_session, status="printing", pickup_code="B-04")

    body = client.post(
        "/api/admin/order/batch-complete",
        json={"order_ids": [order.id, order.id]},
        headers=auth(token),
    ).json()

    assert body["code"] == 0, body
    assert len(body["data"]["succeeded"]) == 1
    assert body["data"]["failed"] == []
    assert body["data"]["succeeded"][0]["order_id"] == order.id


# ---------------------------------------------------------------- 队列搜索的通配符


def test_队列搜索把下划线与百分号当普通字符(client, db_session):
    """关键字里的 _ 与 % 不是通配需求：不转义会退化成全表匹配。"""
    token = admin_token(client)

    def with_name(tail: str, name: str) -> Order:
        order = _make_order(db_session, status="queuing", pickup_code=tail)
        order.file_name = name
        db_session.commit()
        return order

    underscored = with_name("K-01", "论文_终稿.pdf")
    with_name("K-02", "论文A终稿.pdf")
    percent = with_name("K-03", "折扣100%.pdf")
    with_name("K-04", "折扣1000.pdf")

    by_underscore = client.get(
        "/api/admin/queue", params={"keyword": "论文_终稿"}, headers=auth(token)
    ).json()["data"]
    assert by_underscore["total"] == 1
    assert by_underscore["items"][0]["id"] == underscored.id

    by_percent = client.get(
        "/api/admin/queue", params={"keyword": "100%"}, headers=auth(token)
    ).json()["data"]
    assert by_percent["total"] == 1
    assert by_percent["items"][0]["id"] == percent.id


# ---------------------------------------------------------------- 全角字母


def test_全角字母的完整码能核销(client, db_session):
    """全角字母与全角数字一起出现时同样归一化成半角。"""
    token = admin_token(client)
    order = _make_order(db_session, status="ready", pickup_code="A-09")
    typed = _with_dash(
        _fullwidth_digits(_full_code("A-09")).translate(
            {ord(ch): chr(0xFF21 + i) for i, ch in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ")}
        ),
        "－",
    )
    assert typed != _full_code("A-09")

    body = _verify_by_code(client, token, typed)

    assert body["code"] == 0, body
    assert body["data"]["order"]["id"] == order.id
    db_session.expire_all()
    assert db_session.get(Order, order.id).pickup_code == _full_code("A-09")
