"""老师端接口测试：登录、队列、打印推进、核销、异常、收单开关、统计、公告。"""

from __future__ import annotations

import itertools
from datetime import date

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
