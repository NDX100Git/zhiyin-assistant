"""订单接口测试：下单、支付、排队、取消、首页汇总。"""

from __future__ import annotations

import re
import threading
from datetime import date

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.models.notification import Notification
from app.models.order import Order
from app.models.payment_log import PaymentLog
from app.models.print_log import PrintLog
from app.models.uploaded_file import UploadedFile
from app.models.user import User
from app.services import order_service
from app.utils.exceptions import BusinessError
from app.utils.security import decode_token

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"

# 下单要求每份文件都有本人的上传记录；本模块的 file_key 是造的，登录后统一登记
_SESSION = None

ORDER_PAYLOAD = {
    "file_key": "files/2026/10/abcdef0123456789abcdef0123456789.pdf",
    "file_name": "毕业论文.pdf",
    "page_count": 42,
    "print_side": 2,
    "color_mode": 0,
    "copies": 1,
    "paper_size": "A4",
}


def _patch_session(monkeypatch, info: dict) -> None:
    """同时替换下单流程与提示接口用到的时段查询，测试不依赖真实时间。"""
    monkeypatch.setattr("app.services.order_service.current_session_info", lambda db: info)
    monkeypatch.setattr(
        "app.services.session_service.current_session_info", lambda db, now=None: info
    )


@pytest.fixture
def open_session(monkeypatch):
    """测试不依赖真实时间，强制当前时段可收单。"""
    _patch_session(
        monkeypatch,
        {
            "date": date.today().isoformat(),
            "session": "pm",
            "label": "下午",
            "is_open": True,
            "switch_open": True,
            "capacity": 30,
            "used": 0,
            "can_order": True,
            "deferred": False,
            "hint": "",
        },
    )


@pytest.fixture
def closed_session(monkeypatch):
    """截单（老师关闭或容量已满）：仍可下单，只是排到下一个打印时段。"""
    _patch_session(
        monkeypatch,
        {
            "date": date.today().isoformat(),
            "session": "pm",
            "label": "下午",
            "is_open": False,
            "switch_open": False,
            "capacity": 30,
            "used": 30,
            "can_order": True,
            "deferred": True,
            "hint": "下午已截单，现在下单会排在下一个打印时段（次日上午 10:00 后取件）",
        },
    )


@pytest.fixture
def outside_window(monkeypatch):
    """不在任何收单时段内（例如深夜）：同样允许下单。"""
    _patch_session(
        monkeypatch,
        {
            "date": date.today().isoformat(),
            "session": None,
            "label": "",
            "is_open": False,
            "switch_open": False,
            "capacity": 0,
            "used": 0,
            "can_order": True,
            "deferred": True,
            "hint": "当前不在收单时段，现在下单会排在下一个打印时段（次日上午 10:00 后取件）",
        },
    )


@pytest.fixture(autouse=True)
def capture_session(db_session):
    """把测试会话存到模块里，供 register_order_file 使用。"""
    global _SESSION
    _SESSION = db_session
    yield
    _SESSION = None


def register_order_file(user_id: int) -> None:
    """把 ORDER_PAYLOAD 里的文件登记成该用户的上传记录。"""
    key = ORDER_PAYLOAD["file_key"]
    existing = _SESSION.execute(
        select(UploadedFile).where(UploadedFile.file_key == key)
    ).scalar_one_or_none()
    if existing is not None:
        return
    _SESSION.add(
        UploadedFile(
            user_id=user_id,
            file_key=key,
            file_name=ORDER_PAYLOAD["file_name"],
            size=0,
            content_type="application/pdf",
        )
    )
    _SESSION.commit()


def login(client, device_id: str = "order-device-0001") -> str:
    body = client.post("/api/auth/login", json={"device_id": device_id}).json()
    assert body["code"] == 0, body
    token = body["data"]["token"]
    register_order_file(int(decode_token(token)["sub"]))
    return token


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_下单需要登录(client):
    assert client.post("/api/order/create", json=ORDER_PAYLOAD).json()["code"] == 40100


def test_下单返回服务端生成的订单号与取件码(client, open_session):
    token = login(client)
    body = client.post("/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert re.fullmatch(r"\d{6}-\d{3}", data["order_no"])
    assert re.fullmatch(r"\d{6}-[0-9A-Z]-[0-9A-Z]{2}", data["pickup_code"])
    assert data["status"] == "pending_pay"
    assert data["status_label"] == "待支付"
    # 42 页 × 15 分 × 1 份 = 630 分
    assert data["amount_cents"] == 630


def test_下单页数与实际文件不一致被拒(client, open_session, monkeypatch):
    token = login(client)
    monkeypatch.setattr(
        "app.services.order_service._actual_page_count",
        lambda db, file_key: 7,
    )

    body = client.post(
        "/api/order/create",
        json=ORDER_PAYLOAD,
        headers=auth(token),
    ).json()
    assert body["code"] == 40054
    assert "实际为 7 页" in body["message"]


def test_报价同样校验页数不一致(client, open_session, monkeypatch):
    """报价与下单必须用同一份真实页数。

    校验只在 create_order 里做时，客户端把 page_count 改小可以先拿到偏低的
    估价，与随后实际扣款分叉，违背「报价即实付」。
    """
    token = login(client)
    monkeypatch.setattr(
        "app.services.order_service._actual_page_count",
        lambda db, file_key: 7,
    )

    body = client.post("/api/order/quote", json=ORDER_PAYLOAD, headers=auth(token)).json()
    assert body["code"] == 40054
    assert "实际为 7 页" in body["message"]


def test_截单后仍可下单并提示排到下一个时段(client, closed_session):
    token = login(client)
    body = client.post("/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)).json()
    assert body["code"] == 0, body
    notice = body["data"]["session_notice"]
    assert notice["deferred"] is True
    assert "下一个打印时段" in notice["hint"]
    assert body["data"]["status"] == "pending_pay"


def test_非收单时段同样可以下单(client, outside_window):
    token = login(client)
    body = client.post("/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)).json()
    assert body["code"] == 0, body
    assert body["data"]["session_notice"]["deferred"] is True
    assert "不在收单时段" in body["data"]["session_notice"]["hint"]


def test_非法参数被拒绝(client, open_session):
    token = login(client)
    bad = dict(ORDER_PAYLOAD, print_side=3)
    assert client.post("/api/order/create", json=bad, headers=auth(token)).json()["code"] == 40042
    bad_paper = dict(ORDER_PAYLOAD, paper_size="A0")
    assert (
        client.post("/api/order/create", json=bad_paper, headers=auth(token)).json()["code"]
        == 40046
    )


def test_支付成功后进入队列并写入流水日志(client, open_session, db_session):
    token = login(client)
    created = client.post("/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)).json()
    order_id = created["data"]["id"]

    paid = client.post(f"/api/order/{order_id}/pay", headers=auth(token)).json()
    assert paid["code"] == 0, paid
    assert paid["data"]["status"] == "queuing"
    assert paid["data"]["paid"] == 1
    assert paid["data"]["payment"]["provider"] == "mock"

    order = db_session.get(Order, order_id)
    assert order.paid == 1
    assert order.pay_method == "mock"
    assert order.paid_at is not None

    payments = list(
        db_session.execute(select(PaymentLog).where(PaymentLog.order_id == order_id)).scalars()
    )
    assert len(payments) == 1 and payments[0].amount_cents == 630

    logs = list(
        db_session.execute(select(PrintLog).where(PrintLog.order_id == order_id)).scalars()
    )
    transitions = {(log.from_status, log.to_status) for log in logs}
    assert (None, "pending_pay") in transitions
    assert ("pending_pay", "queuing") in transitions

    events = list(
        db_session.execute(
            select(Notification).where(Notification.order_id == order_id)
        ).scalars()
    )
    assert {"order_created", "order_queuing"} <= {event.event for event in events}


def test_重复支付被拒绝(client, open_session):
    token = login(client)
    order_id = client.post(
        "/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)
    ).json()["data"]["id"]
    client.post(f"/api/order/{order_id}/pay", headers=auth(token))
    again = client.post(f"/api/order/{order_id}/pay", headers=auth(token)).json()
    assert again["code"] == 40902


def _pending_order(client, token: str) -> tuple[int, int]:
    """造一张待支付单，返回 (订单 id, 下单学生 id)。"""
    body = client.post("/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)).json()
    assert body["code"] == 0, body
    return body["data"]["id"], int(decode_token(token)["sub"])


def _race_pay(session_factory, order_id: int, user_id: int) -> list:
    """两个独立会话同时支付同一单，返回各自的结果（成功字典或业务错误码）。

    两个线程各自拿自己的 Session，不共用一个连接：只有真并发且真在数据库里
    排队，才能暴露「先判断后扣款」的问题。barrier 保证两边同时进入 pay_order。
    """
    barrier = threading.Barrier(2)
    collected: list = []
    collected_lock = threading.Lock()

    def worker() -> None:
        session = session_factory()
        try:
            user = session.get(User, user_id)
            assert user is not None
            barrier.wait(timeout=30)
            try:
                outcome = order_service.pay_order(session, user, order_id)
            except BusinessError as exc:
                session.rollback()
                outcome = exc.code
            except Exception as exc:  # 线程里的异常不能静默吞掉
                session.rollback()
                outcome = exc
            with collected_lock:
                collected.append(outcome)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads), "并发支付线程未在超时内结束"
    return collected


@pytest.mark.parametrize("round_index", range(20))
def test_并发支付同一单只成功一次(client, session_factory, open_session, round_index):
    """两个请求同时付同一单：恰好一次成功，另一个是 40902（无 500、无双扣）。"""
    token = login(client)
    order_id, user_id = _pending_order(client, token)

    results = _race_pay(session_factory, order_id, user_id)

    succeeded = [item for item in results if isinstance(item, dict)]
    refused = [item for item in results if not isinstance(item, dict)]
    assert len(succeeded) == 1, f"第 {round_index + 1} 轮出现 {len(succeeded)} 次成功：{results}"
    assert succeeded[0]["status"] == "queuing"
    # 第二个请求拿到的必须是「状态已变」，不是靠幂等键抽到的 40905：
    # 说明行锁把两个请求串行化了，第二个根本没进支付通道
    assert refused == [40902], f"第 {round_index + 1} 轮：{results}"


def test_并发支付只落一条流水且状态只推进一次(client, session_factory, open_session):
    """并发支付后的账：一条流水、一条状态日志、一条入队通知。"""
    token = login(client)
    order_id, user_id = _pending_order(client, token)

    results = _race_pay(session_factory, order_id, user_id)
    assert len([item for item in results if isinstance(item, dict)]) == 1, results

    session = session_factory()
    try:
        payments = list(
            session.execute(
                select(PaymentLog).where(PaymentLog.order_id == order_id)
            ).scalars()
        )
        assert len(payments) == 1
        assert payments[0].amount_cents == 630
        assert payments[0].idempotency_key == f"pay:{order_id}"

        order = session.get(Order, order_id)
        assert order.status == "queuing"
        assert order.paid == 1

        transitions = [
            (log.from_status, log.to_status)
            for log in session.execute(
                select(PrintLog).where(PrintLog.order_id == order_id)
            ).scalars()
        ]
        assert transitions.count(("pending_pay", "queuing")) == 1

        events = list(
            session.execute(
                select(Notification.event).where(Notification.order_id == order_id)
            ).scalars()
        )
        assert events.count("order_queuing") == 1
    finally:
        session.close()


def test_支付流水同一订单同一幂等键唯一(client, session_factory, open_session):
    """数据库层的防重约束：重复写同一笔支付流水直接被唯一索引拒绝。"""
    token = login(client)
    order_id, _ = _pending_order(client, token)
    paid = client.post(f"/api/order/{order_id}/pay", headers=auth(token)).json()
    assert paid["code"] == 0, paid

    session = session_factory()
    try:
        session.add(
            PaymentLog(
                order_id=order_id,
                amount_cents=630,
                method="mock",
                operator="student",
                idempotency_key=f"pay:{order_id}",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()
        assert len(
            list(
                session.execute(
                    select(PaymentLog).where(PaymentLog.order_id == order_id)
                ).scalars()
            )
        ) == 1
    finally:
        session.close()


def test_已付款订单学生不能自助取消(client, open_session, db_session):
    """已付款的排队单学生取消要被拒，并给出「找老师退款」的明确提示。"""
    token = login(client)
    order_id = client.post(
        "/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)
    ).json()["data"]["id"]
    client.post(f"/api/order/{order_id}/pay", headers=auth(token))

    refused = client.post(
        f"/api/order/{order_id}/cancel", json={"reason": "临时不需要"}, headers=auth(token)
    ).json()

    assert refused["code"] == 40905
    assert "退款" in refused["message"]
    db_session.expire_all()
    assert db_session.get(Order, order_id).status == "queuing"
    assert db_session.get(Order, order_id).cancel_reason is None


def test_待支付订单学生仍可自助取消(client, open_session, db_session):
    """待支付单还没有钱往来，学生取消照旧。"""
    token = login(client)
    order_id = client.post(
        "/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)
    ).json()["data"]["id"]

    canceled = client.post(
        f"/api/order/{order_id}/cancel", json={"reason": "改主意了"}, headers=auth(token)
    ).json()

    assert canceled["code"] == 0, canceled
    assert canceled["data"]["status"] == "cancelled"
    db_session.expire_all()
    assert db_session.get(Order, order_id).status == "cancelled"


def test_打印完成后不能取消(client, open_session, db_session):
    """已付款的待取件单：提示联系老师退款；未付款的异常状态单：给状态错误。"""
    token = login(client)
    order_id = client.post(
        "/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)
    ).json()["data"]["id"]
    client.post(f"/api/order/{order_id}/pay", headers=auth(token))
    order = db_session.get(Order, order_id)
    order.status = "ready"
    db_session.commit()

    refused = client.post(f"/api/order/{order_id}/cancel", headers=auth(token)).json()
    assert refused["code"] == 40905  # 已付款判定在前：提示联系老师退款
    assert "退款" in refused["message"]

    # 没有钱往来的非可取消状态：仍然返回状态错误码，不落成含混的提示
    unpaid_id = client.post(
        "/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)
    ).json()["data"]["id"]
    unpaid = db_session.get(Order, unpaid_id)
    unpaid.status = "ready"
    db_session.commit()

    refused_unpaid = client.post(
        f"/api/order/{unpaid_id}/cancel", headers=auth(token)
    ).json()
    assert refused_unpaid["code"] == 40901
    assert "不能取消" in refused_unpaid["message"]


def test_订单详情与列表包含排队信息(client, open_session):
    token = login(client)
    first = client.post("/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)).json()["data"]
    second = client.post("/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)).json()["data"]
    client.post(f"/api/order/{first['id']}/pay", headers=auth(token))
    client.post(f"/api/order/{second['id']}/pay", headers=auth(token))

    detail = client.get(f"/api/order/{second['id']}", headers=auth(token)).json()["data"]
    assert detail["queue_position"] == 1
    assert detail["estimated_minutes"] >= 1

    listed = client.get("/api/order/list", headers=auth(token)).json()["data"]
    assert listed["total"] == 2
    assert listed["items"][0]["id"] == second["id"]

    queuing_only = client.get(
        "/api/order/list", params={"status": "queuing"}, headers=auth(token)
    ).json()["data"]
    assert queuing_only["total"] == 2


def test_首页汇总返回队列与收单状态(client, open_session):
    token = login(client)
    order_id = client.post(
        "/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)
    ).json()["data"]["id"]
    client.post(f"/api/order/{order_id}/pay", headers=auth(token))

    body = client.get("/api/home/summary", headers=auth(token)).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["waiting_count"] == 1
    assert data["mine"]["queuing"] == 1
    assert data["order_session"]["can_order"] is True


def test_首页在截单时仍可下单并给出取件提示(client, closed_session):
    token = login(client)
    data = client.get("/api/home/summary", headers=auth(token)).json()["data"]
    assert data["order_session"]["can_order"] is True
    assert data["order_session"]["deferred"] is True
    assert "下一个打印时段" in data["order_session"]["hint"]


def test_上传超过宽限期下单被拒(client, open_session, monkeypatch) -> None:
    """订单引用的文件过期后不该还能下单，否则打印时才发现文件没了。"""
    from datetime import datetime, timedelta

    from app.services.storage.minio_store import MinioStorage

    stale = datetime.now() - timedelta(hours=settings.order_file_grace_hours + 1)
    monkeypatch.setattr(MinioStorage, "stat_modified", lambda self, key: stale)

    token = login(client)
    body = client.post("/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)).json()

    assert body["code"] == 40052
    assert "重新上传" in body["message"]


def test_文件已不存在时下单给出人话提示(client, open_session, monkeypatch) -> None:
    from app.services.storage.minio_store import MinioStorage

    monkeypatch.setattr(MinioStorage, "stat_modified", lambda self, key: None)

    token = login(client)
    body = client.post("/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)).json()

    assert body["code"] == 40053
    assert "重新上传" in body["message"]
