"""换文件接口与状态口径测试。

覆盖：待支付直接换、排队中申请与通过、驳回、异常单恢复、非法状态拒绝、
重复申请拒绝、老师改价、队尾重排、待确认状态下订单不变、迁移回填。
"""

from __future__ import annotations

import itertools
import re
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select

from app.config import settings
from app.migrations import run_migrations
from app.models.notification import Notification
from app.models.order import Order
from app.models.order_file_change import (
    CHANGE_APPROVED,
    CHANGE_CANCELLED,
    CHANGE_PENDING,
    CHANGE_REJECTED,
    OrderFileChange,
)
from app.models.order_file_version import OrderFileVersion
from app.models.payment_log import PaymentLog
from app.models.print_log import PrintLog
from app.models.uploaded_file import UploadedFile
from app.utils.security import decode_token

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
_key_seq = itertools.count(1)

# 下单要求每份文件都有本人的上传记录；用例里造出来的 file_key 统一在这里登记
_SESSION = None


def _file_key(name: str = "file") -> str:
    index = next(_key_seq)
    return f"files/2026/10/{index:032x}-{name}.pdf"


def _order_payload(page_count: int = 10, file_key: str | None = None,
                   file_name: str = "原稿.pdf") -> dict:
    return {
        "file_key": file_key or _file_key("order"),
        "file_name": file_name,
        "page_count": page_count,
        "print_side": 2,
        "color_mode": 0,
        "copies": 1,
        "paper_size": "A4",
    }


def _replace_payload(page_count: int = 20, file_key: str | None = None,
                     file_name: str = "新稿.pdf", reason: str | None = "传错版本") -> dict:
    return {
        "file_key": file_key or _file_key("replace"),
        "file_name": file_name,
        "page_count": page_count,
        "reason": reason,
    }


def _session_info() -> dict:
    return {
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
    }


@pytest.fixture(autouse=True)
def open_session(monkeypatch):
    """换文件用例不依赖真实收单时段。"""
    monkeypatch.setattr("app.services.order_service.current_session_info", lambda db: _session_info())
    monkeypatch.setattr(
        "app.services.session_service.current_session_info", lambda db, now=None: _session_info()
    )


def student_headers(client, device_id: str = "file-change-student") -> dict[str, str]:
    body = client.post("/api/auth/login", json={"device_id": device_id}).json()
    assert body["code"] == 0, body
    return {"Authorization": f"Bearer {body['data']['token']}"}


def admin_headers(client) -> dict[str, str]:
    body = client.post(
        "/api/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    ).json()
    assert body["code"] == 0, body
    return {"Authorization": f"Bearer {body['data']['token']}"}


@pytest.fixture(autouse=True)
def capture_session(db_session):
    """把测试会话存到模块里，供下面的小工具登记上传记录。"""
    global _SESSION
    _SESSION = db_session
    yield
    _SESSION = None


def register_upload(file_key: str, file_name: str | None, user_id: int) -> None:
    """把用例造出来的文件标识登记成该用户的真实上传记录。"""
    session = _SESSION
    existing = session.execute(
        select(UploadedFile).where(UploadedFile.file_key == file_key)
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(
        UploadedFile(
            user_id=user_id,
            file_key=file_key,
            file_name=file_name,
            size=0,
            content_type="application/pdf",
        )
    )
    session.commit()


def create_order(client, headers: dict[str, str], page_count: int = 10,
                 file_key: str | None = None, file_name: str = "原稿.pdf") -> dict:
    payload = _order_payload(page_count, file_key, file_name)
    owner_id = int(decode_token(headers["Authorization"].split(" ", 1)[1])["sub"])
    register_upload(payload["file_key"], payload["file_name"], owner_id)
    body = client.post("/api/order/create", json=payload, headers=headers).json()
    assert body["code"] == 0, body
    return body["data"]


def pay_order(client, headers: dict[str, str], order_id: int) -> dict:
    body = client.post(f"/api/order/{order_id}/pay", headers=headers).json()
    assert body["code"] == 0, body
    return body["data"]


def replace_file(client, headers: dict[str, str], order_id: int, page_count: int,
                 **kwargs) -> dict:
    should_register = kwargs.pop("register_upload", True)
    payload = _replace_payload(page_count, **kwargs)
    if should_register:
        owner_id = int(decode_token(headers["Authorization"].split(" ", 1)[1])["sub"])
        register_upload(payload["file_key"], payload["file_name"], owner_id)
    body = client.post(
        f"/api/order/{order_id}/replace-file",
        json=payload,
        headers=headers,
    ).json()
    return body


def test_换文件拒绝使用他人的上传文件(client):
    owner_headers = student_headers(client, device_id="file-owner")
    order = create_order(client, owner_headers)

    other_headers = student_headers(client, device_id="file-other")
    other_owner_id = int(
        decode_token(other_headers["Authorization"].split(" ", 1)[1])["sub"]
    )
    other_key = _file_key("other-user")
    register_upload(other_key, "别人的文件.pdf", other_owner_id)

    body = replace_file(
        client,
        owner_headers,
        order["id"],
        page_count=20,
        file_key=other_key,
        register_upload=False,
    )
    assert body["code"] == 40404
    assert "不属于当前用户" in body["message"]


def test_换文件页数与实际文件不一致被拒(client, monkeypatch):
    headers = student_headers(client)
    order = create_order(client, headers, page_count=10)
    monkeypatch.setattr(
        "app.services.order_service._actual_page_count",
        lambda db, file_key: 17,
    )

    body = replace_file(client, headers, order["id"], page_count=20)
    assert body["code"] == 40054
    assert "实际为 17 页" in body["message"]


def confirm(client, headers: dict[str, str], order_id: int, approve: bool,
            amount_cents: int | None = None) -> dict:
    payload: dict = {"approve": approve}
    if amount_cents is not None:
        payload["amount_cents"] = amount_cents
    return client.post(
        f"/api/admin/order/{order_id}/confirm-file-change",
        json=payload,
        headers=headers,
    ).json()


def paid_order(client, headers: dict[str, str], page_count: int = 10) -> dict:
    order = create_order(client, headers, page_count)
    pay_order(client, headers, order["id"])
    return order


def test_待支付换文件立即生效且订单号与取件码不变(client, db_session):
    headers = student_headers(client)
    order = create_order(client, headers, page_count=10)
    assert order["amount_cents"] == 150

    body = replace_file(client, headers, order["id"], page_count=20)
    assert body["code"] == 0, body
    data = body["data"]
    assert data["applied"] is True
    assert data["status"] == "pending_pay"
    assert data["page_count"] == 20
    assert data["amount_cents"] == 300
    assert data["file_name"] == "新稿.pdf"
    assert data["file_change"] is None
    assert data["order_no"] == order["order_no"]
    assert data["pickup_code"] == order["pickup_code"]
    assert data["created_at"] == order["created_at"]

    versions = list(
        db_session.execute(
            select(OrderFileVersion)
            .where(OrderFileVersion.order_id == order["id"])
            .order_by(OrderFileVersion.version_no)
        ).scalars()
    )
    assert [(v.version_no, v.page_count, v.is_current) for v in versions] == [
        (1, 10, 0),
        (2, 20, 1),
    ]
    assert versions[1].amount_cents == 300

    logs = list(
        db_session.execute(select(PrintLog).where(PrintLog.order_id == order["id"])).scalars()
    )
    assert "file_replace" in {log.action for log in logs}
    events = db_session.execute(
        select(Notification.event).where(Notification.order_id == order["id"])
    ).scalars()
    assert "order_file_replaced" in set(events)


def test_排队中换文件只创建待确认请求_订单保持原样(client, db_session):
    headers = student_headers(client)
    admin = admin_headers(client)
    order = paid_order(client, headers, page_count=10)

    body = replace_file(client, headers, order["id"], page_count=30)
    assert body["code"] == 0, body
    data = body["data"]
    assert data["applied"] is False
    assert data["status"] == "queuing"
    assert data["page_count"] == 10
    assert data["amount_cents"] == 150

    change = data["file_change"]
    assert change is not None
    assert change["change_id"] == change["id"]
    assert change["new_page_count"] == 30
    assert change["new_amount_cents"] == 450
    assert change["old_page_count"] == 10
    assert change["old_amount_cents"] == 150
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T.+", change["submitted_at"])

    detail = client.get(f"/api/order/{order['id']}", headers=headers).json()["data"]
    assert detail["file_change"]["change_id"] == change["change_id"]

    queue = client.get("/api/admin/queue", headers=admin).json()["data"]
    item = next(row for row in queue["items"] if row["id"] == order["id"])
    assert item["file_change"]["new_page_count"] == 30

    stored = db_session.execute(
        select(OrderFileChange).where(OrderFileChange.order_id == order["id"])
    ).scalar_one()
    assert stored.status == CHANGE_PENDING
    assert stored.new_amount_cents == 450


def test_重复提交待确认请求被拒(client):
    headers = student_headers(client)
    order = paid_order(client, headers)

    assert replace_file(client, headers, order["id"], page_count=20)["code"] == 0
    second = replace_file(client, headers, order["id"], page_count=30)
    assert second["code"] == 40910
    assert "待老师确认" in second["message"]


def test_老师确认后替换文件_按确认价落库并排到队尾(client, db_session):
    headers = student_headers(client)
    admin = admin_headers(client)

    first = paid_order(client, headers, page_count=10)
    second = paid_order(client, headers, page_count=8)
    third = paid_order(client, headers, page_count=6)

    assert replace_file(client, headers, first["id"], page_count=30)["code"] == 0
    body = confirm(client, admin, first["id"], approve=True, amount_cents=500)
    assert body["code"] == 0, body
    data = body["data"]
    assert data["approved"] is True
    assert data["file_change"] is None
    assert data["amount_cents"] == 500
    order_view = data["order"]
    assert order_view["page_count"] == 30
    assert order_view["file_name"] == "新稿.pdf"
    assert order_view["requeue_at"] is not None
    assert order_view["status"] == "queuing"

    # 三张单都在排队，换文件的那张应排到最后
    assert order_view["queue_position"] == 2
    assert order_view["estimated_minutes"] >= 1

    queue = client.get(
        "/api/admin/queue", params={"status": "queuing"}, headers=admin
    ).json()["data"]
    assert [row["id"] for row in queue["items"]][-1] == first["id"]
    assert queue["items"][-1]["id"] == first["id"]
    assert {row["id"] for row in queue["items"]} == {first["id"], second["id"], third["id"]}

    versions = list(
        db_session.execute(
            select(OrderFileVersion)
            .where(OrderFileVersion.order_id == first["id"])
            .order_by(OrderFileVersion.version_no)
        ).scalars()
    )
    assert [(v.version_no, v.page_count, v.is_current) for v in versions] == [
        (1, 10, 0),
        (2, 30, 1),
    ]
    assert versions[-1].amount_cents == 500

    change = db_session.execute(
        select(OrderFileChange).where(OrderFileChange.order_id == first["id"])
    ).scalar_one()
    assert change.status == CHANGE_APPROVED
    assert change.final_amount_cents == 500
    assert change.reviewed_by

    logs = db_session.execute(
        select(PrintLog.action).where(PrintLog.order_id == first["id"])
    ).scalars()
    assert "file_change_approve" in set(logs)
    events = db_session.execute(
        select(Notification.event).where(Notification.order_id == first["id"])
    ).scalars()
    assert "order_file_change_approved" in set(events)


def test_重新排队后预计等待按新页数重算(client, db_session):
    headers = student_headers(client)
    admin = admin_headers(client)
    order = paid_order(client, headers, page_count=10)

    # 造两单历史样本：每单 10 页、耗时 600 秒，得到 60 秒/页
    now = datetime.now()
    for index in range(2):
        db_session.add(
            Order(
                order_no=f"0101-8{index:02d}",
                pickup_code=f"0101-Y-{index:02d}",
                user_id=order["user_id"] if "user_id" in order else 1,
                status="ready",
                page_count=10,
                print_side=2,
                color_mode=0,
                copies=1,
                paper_size="A4",
                amount_cents=150,
                paid=1,
                file_key=_file_key("history"),
                created_at=now - timedelta(seconds=1200),
                printed_at=now - timedelta(seconds=600),
            )
        )
    db_session.commit()

    before = client.get(f"/api/order/{order['id']}", headers=headers).json()["data"]
    assert before["queue_position"] == 0
    assert before["estimated_minutes"] == 10

    assert replace_file(client, headers, order["id"], page_count=30)["code"] == 0
    assert confirm(client, admin, order["id"], approve=True)["code"] == 0

    after = client.get(f"/api/order/{order['id']}", headers=headers).json()["data"]
    assert after["page_count"] == 30
    # 同样的 60 秒/页，页数从 10 变 30，预计等待从 10 分钟变 30 分钟
    assert after["estimated_minutes"] == 30


def test_老师驳回后保留原文件且可以再次申请(client, db_session):
    headers = student_headers(client)
    admin = admin_headers(client)
    order = paid_order(client, headers, page_count=10)
    original_key = order["file_key"]

    assert replace_file(client, headers, order["id"], page_count=30)["code"] == 0
    body = confirm(client, admin, order["id"], approve=False)
    assert body["code"] == 0, body
    data = body["data"]
    assert data["approved"] is False
    assert data["order"]["file_key"] == original_key
    assert data["order"]["page_count"] == 10
    assert data["order"]["amount_cents"] == 150
    assert data["order"]["file_change"] is None

    change = db_session.execute(
        select(OrderFileChange).where(OrderFileChange.order_id == order["id"])
    ).scalar_one()
    assert change.status == CHANGE_REJECTED
    assert change.pending_flag is None

    # 驳回后可以重新提交
    again = replace_file(client, headers, order["id"], page_count=25)
    assert again["code"] == 0
    assert again["data"]["file_change"]["new_page_count"] == 25


def test_异常单换文件通过后恢复排队(client, db_session):
    headers = student_headers(client)
    admin = admin_headers(client)
    order = paid_order(client, headers, page_count=10)

    marked = client.post(
        f"/api/admin/order/{order['id']}/abnormal",
        json={"reason": "文件打不开"},
        headers=admin,
    ).json()
    assert marked["code"] == 0
    assert marked["data"]["status"] == "abnormal"

    assert replace_file(client, headers, order["id"], page_count=40)["code"] == 0
    body = confirm(client, admin, order["id"], approve=True)
    assert body["code"] == 0, body
    data = body["data"]
    assert data["approved"] is True
    assert data["order"]["status"] == "queuing"
    assert data["order"]["page_count"] == 40
    assert data["order"]["requeue_at"] is not None
    assert data["order"]["queue_position"] == 0


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("printing", "正在打印"),
        ("ready", "打印完成"),
        ("completed", "已完成"),
        ("cancelled", "已取消"),
    ],
)
def test_非法状态拒绝换文件并给出中文提示(client, status, expected):
    headers = student_headers(client, device_id=f"file-change-{status}")
    admin = admin_headers(client)
    order = create_order(client, headers, page_count=10)

    if status == "cancelled":
        cancelled = client.post(f"/api/order/{order['id']}/cancel", headers=headers).json()
        assert cancelled["code"] == 0
    else:
        pay_order(client, headers, order["id"])
        started = client.post(f"/api/admin/order/{order['id']}/start", headers=admin).json()
        assert started["code"] == 0
        if status in {"ready", "completed"}:
            done = client.post(f"/api/admin/order/{order['id']}/complete", headers=admin).json()
            assert done["code"] == 0
        if status == "completed":
            verified = client.post(
                "/api/admin/order/verify-code",
                json={"pickup_code": order["pickup_code"]},
                headers=admin,
            ).json()
            assert verified["code"] == 0

    body = replace_file(client, headers, order["id"], page_count=20)
    assert body["code"] != 0
    assert expected in body["message"]
    assert "不能" in body["message"]

    detail = client.get(f"/api/order/{order['id']}", headers=headers).json()["data"]
    assert detail["status"] == status
    assert detail["page_count"] == 10


def test_新价更高时差额在核销时现场补收(client, db_session):
    headers = student_headers(client)
    admin = admin_headers(client)
    order = paid_order(client, headers, page_count=10)  # 已付 150 分

    assert replace_file(client, headers, order["id"], page_count=30)["code"] == 0
    approved = confirm(client, admin, order["id"], approve=True)
    assert approved["code"] == 0, approved
    assert approved["data"]["amount_cents"] == 450
    assert approved["data"]["supplement_cents"] == 300

    # 打印完成时系统按新价 450 计价，已付 150，核销时补 300
    assert client.post(f"/api/admin/order/{order['id']}/start", headers=admin).json()["code"] == 0
    completed = client.post(
        f"/api/admin/order/{order['id']}/complete", headers=admin
    ).json()
    assert completed["code"] == 0, completed
    verified = client.post(
        "/api/admin/order/verify-code",
        json={"pickup_code": order["pickup_code"]},
        headers=admin,
    ).json()
    assert verified["code"] == 0, verified
    assert verified["data"]["collected_onsite"] is True
    assert verified["data"]["collected_cents"] == 300

    total_paid = db_session.execute(
        select(PaymentLog.amount_cents).where(PaymentLog.order_id == order["id"])
    ).scalars()
    assert sum(total_paid) == 450


def test_新价更低时多付部分不退(client, db_session):
    headers = student_headers(client)
    admin = admin_headers(client)
    order = paid_order(client, headers, page_count=10)  # 已付 150 分

    assert replace_file(client, headers, order["id"], page_count=5)["code"] == 0
    approved = confirm(client, admin, order["id"], approve=True)
    assert approved["code"] == 0, approved
    assert approved["data"]["amount_cents"] == 75
    assert approved["data"]["supplement_cents"] == 0

    assert client.post(f"/api/admin/order/{order['id']}/start", headers=admin).json()["code"] == 0
    assert client.post(f"/api/admin/order/{order['id']}/complete", headers=admin).json()["code"] == 0
    verified = client.post(
        "/api/admin/order/verify-code",
        json={"pickup_code": order["pickup_code"]},
        headers=admin,
    ).json()
    assert verified["code"] == 0
    assert verified["data"]["collected_onsite"] is False
    assert verified["data"]["collected_cents"] == 0

    total_paid = db_session.execute(
        select(PaymentLog.amount_cents).where(PaymentLog.order_id == order["id"])
    ).scalars()
    # 不建退款通道：多付的 75 分不退
    assert sum(total_paid) == 150


def test_没有待确认请求时确认接口返回明确提示(client):
    headers = student_headers(client)
    admin = admin_headers(client)
    order = paid_order(client, headers)

    body = confirm(client, admin, order["id"], approve=True)
    assert body["code"] == 40403
    assert "没有待确认" in body["message"]


def test_学生不能调用老师确认接口(client):
    headers = student_headers(client)
    order = paid_order(client, headers)
    assert replace_file(client, headers, order["id"], page_count=20)["code"] == 0

    body = client.post(
        f"/api/admin/order/{order['id']}/confirm-file-change",
        json={"approve": True},
        headers=headers,
    ).json()
    assert body["code"] == 40300


def test_开始打印会让待确认请求失效(client):
    headers = student_headers(client)
    admin = admin_headers(client)
    order = paid_order(client, headers)

    assert replace_file(client, headers, order["id"], page_count=20)["code"] == 0
    started = client.post(f"/api/admin/order/{order['id']}/start", headers=admin).json()
    assert started["code"] == 0
    assert started["data"]["order"]["file_change"] is None

    change = client.get("/api/admin/queue", headers=admin).json()["data"]["items"][0]
    assert change["file_change"] is None

    body = confirm(client, admin, order["id"], approve=True)
    assert body["code"] == 40403

    stored = client.get(f"/api/order/{order['id']}", headers=headers).json()["data"]
    assert stored["file_change"] is None


def test_取消订单会让待确认请求失效(client, db_session):
    headers = student_headers(client)
    order = paid_order(client, headers)
    assert replace_file(client, headers, order["id"], page_count=20)["code"] == 0

    assert client.post(f"/api/order/{order['id']}/cancel", headers=headers).json()["code"] == 0
    change = db_session.execute(
        select(OrderFileChange).where(OrderFileChange.order_id == order["id"])
    ).scalar_one()
    assert change.status == CHANGE_CANCELLED


def test_迁移可重复执行并为老订单回填版本(db_session, engine):
    order = Order(
        order_no="0101-901",
        pickup_code="0101-Z-ZZ",
        user_id=1,
        status="queuing",
        file_key=_file_key("legacy"),
        file_name="老订单.pdf",
        page_count=12,
        amount_cents=180,
    )
    db_session.add(order)
    db_session.commit()

    run_migrations(engine)
    db_session.expire_all()
    versions = list(
        db_session.execute(
            select(OrderFileVersion).where(OrderFileVersion.order_id == order.id)
        ).scalars()
    )
    assert [(v.version_no, v.page_count, v.is_current) for v in versions] == [(1, 12, 1)]
    assert versions[0].operator == "system"

    # 再跑一次不会重复回填，也不会报错
    run_migrations(engine)
    db_session.expire_all()
    again = list(
        db_session.execute(
            select(OrderFileVersion).where(OrderFileVersion.order_id == order.id)
        ).scalars()
    )
    assert len(again) == 1
