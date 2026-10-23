"""订单接口测试：下单、支付、排队、取消、首页汇总。"""

from __future__ import annotations

import re
from datetime import date

import pytest
from sqlalchemy import select

from app.config import settings
from app.models.notification import Notification
from app.models.order import Order
from app.models.payment_log import PaymentLog
from app.models.print_log import PrintLog
from app.models.uploaded_file import UploadedFile
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


def test_排队中可取消_打印完成后不能取消(client, open_session, db_session):
    token = login(client)
    order_id = client.post(
        "/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)
    ).json()["data"]["id"]
    client.post(f"/api/order/{order_id}/pay", headers=auth(token))

    canceled = client.post(
        f"/api/order/{order_id}/cancel", json={"reason": "临时不需要"}, headers=auth(token)
    ).json()
    assert canceled["code"] == 0
    assert canceled["data"]["status"] == "cancelled"

    # 另一个订单推进到待取件后不允许取消
    other_id = client.post(
        "/api/order/create", json=ORDER_PAYLOAD, headers=auth(token)
    ).json()["data"]["id"]
    client.post(f"/api/order/{other_id}/pay", headers=auth(token))
    order = db_session.get(Order, other_id)
    order.status = "ready"
    db_session.commit()

    refused = client.post(f"/api/order/{other_id}/cancel", headers=auth(token)).json()
    assert refused["code"] == 40901


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
