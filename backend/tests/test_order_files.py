"""多文件订单、逐文件换文件、撤回上传文件与老师确认标记的接口测试。

单文件旧行为由 test_order_api.py 与 test_file_change.py 继续覆盖；本文件只测
「一单多文件」新增的数据与接口，以及老订单迁移回填。
"""

from __future__ import annotations

import itertools
from datetime import date

import pytest
from sqlalchemy import select

from app.config import settings
from app.migrations import run_migrations
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.order_file_change import CHANGE_APPROVED, OrderFileChange
from app.models.order_file_version import OrderFileVersion
from app.models.uploaded_file import UploadedFile

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
_key_seq = itertools.count(1)


def _file_key(name: str = "file") -> str:
    index = next(_key_seq)
    return f"files/2026/10/{index:032x}-{name}.pdf"


def _file_item(name: str, pages: int, key: str | None = None) -> dict:
    return {
        "file_key": key or _file_key(name),
        "file_name": name,
        "page_count": pages,
    }


def _create_payload(files: list[dict], **overrides) -> dict:
    payload = {
        "files": files,
        "print_side": 2,
        "color_mode": 0,
        "copies": 1,
        "paper_size": "A4",
    }
    payload.update(overrides)
    return payload


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
    monkeypatch.setattr("app.services.order_service.current_session_info", lambda db: _session_info())
    monkeypatch.setattr(
        "app.services.session_service.current_session_info", lambda db, now=None: _session_info()
    )


def student_headers(client, device_id: str = "multi-file-student") -> dict[str, str]:
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


def create_order(client, headers, files: list[dict], **overrides) -> dict:
    body = client.post(
        "/api/order/create", json=_create_payload(files, **overrides), headers=headers
    ).json()
    assert body["code"] == 0, body
    return body["data"]


def pay_order(client, headers, order_id: int) -> dict:
    body = client.post(f"/api/order/{order_id}/pay", headers=headers).json()
    assert body["code"] == 0, body
    return body["data"]


def replace_file(client, headers, order_id: int, page_count: int, **fields) -> dict:
    payload = {
        "file_key": _file_key("replace"),
        "file_name": "新文件.pdf",
        "page_count": page_count,
        "reason": "传错版本",
        **fields,
    }
    return client.post(
        f"/api/order/{order_id}/replace-file", json=payload, headers=headers
    ).json()


def confirm(client, headers, order_id: int, amount_cents: int | None = None) -> dict:
    payload: dict = {"approve": True}
    if amount_cents is not None:
        payload["amount_cents"] = amount_cents
    return client.post(
        f"/api/admin/order/{order_id}/confirm-file-change", json=payload, headers=headers
    ).json()


def test_单文件旧写法仍可用且返回一份文件(client):
    headers = student_headers(client, "multi-single-compat")
    key = _file_key("single")
    body = client.post(
        "/api/order/create",
        json={
            "file_key": key,
            "file_name": "单文件.pdf",
            "page_count": 42,
            "print_side": 2,
            "color_mode": 0,
            "copies": 1,
            "paper_size": "A4",
        },
        headers=headers,
    ).json()
    assert body["code"] == 0, body
    data = body["data"]
    assert data["page_count"] == 42
    assert data["amount_cents"] == 630
    assert len(data["files"]) == 1
    assert data["files"][0]["file_index"] == 1
    assert data["files"][0]["file_key"] == key
    assert data["files"][0]["page_count"] == 42


def test_多文件下单按总页数计费且一单一码(client, db_session):
    headers = student_headers(client, "multi-create")
    files = [_file_item("正文.pdf", 10), _file_item("附录.pdf", 20), _file_item("图册.pdf", 5)]
    order = create_order(client, headers, files)

    # 35 页 × 15 分 = 525 分；一次下单、一个取件码
    assert order["page_count"] == 35
    assert order["amount_cents"] == 525
    assert order["status"] == "pending_pay"
    assert order["pickup_code"].count("-") == 2
    assert [item["file_index"] for item in order["files"]] == [1, 2, 3]
    assert [item["page_count"] for item in order["files"]] == [10, 20, 5]
    assert [item["amount_cents"] for item in order["files"]] == [150, 300, 75]

    stored = list(
        db_session.execute(
            select(OrderFile)
            .where(OrderFile.order_id == order["id"])
            .order_by(OrderFile.file_index)
        ).scalars()
    )
    assert [(row.file_index, row.page_count) for row in stored] == [(1, 10), (2, 20), (3, 5)]

    versions = list(
        db_session.execute(
            select(OrderFileVersion)
            .where(OrderFileVersion.order_id == order["id"])
            .order_by(OrderFileVersion.version_no)
        ).scalars()
    )
    assert [(v.file_index, v.version_no, v.is_current) for v in versions] == [
        (1, 1, 1),
        (2, 2, 1),
        (3, 3, 1),
    ]


def test_多文件订单换文件必须指定文件序号(client):
    headers = student_headers(client, "multi-need-index")
    order = create_order(client, headers, [_file_item("正文.pdf", 10), _file_item("附录.pdf", 5)])
    pay_order(client, headers, order["id"])

    missing = replace_file(client, headers, order["id"], page_count=8)
    assert missing["code"] == 40048
    assert "多份文件" in missing["message"]

    wrong = replace_file(client, headers, order["id"], page_count=8, file_index=9)
    assert wrong["code"] == 40405


def test_逐文件换文件只改对应文件并重算整单(client, db_session):
    headers = student_headers(client, "multi-replace")
    admin = admin_headers(client)
    order = create_order(
        client,
        headers,
        [_file_item("正文.pdf", 2), _file_item("附录.pdf", 3), _file_item("图册.pdf", 5)],
    )
    assert order["amount_cents"] == 150
    pay_order(client, headers, order["id"])

    pending = replace_file(
        client, headers, order["id"], page_count=4,
        file_index=2, file_name="新附录.pdf",
    )
    assert pending["code"] == 0, pending
    data = pending["data"]
    assert data["applied"] is False
    # 待确认期间订单本身不变
    assert data["page_count"] == 10
    assert data["amount_cents"] == 150
    change = data["file_change"]
    assert change["file_index"] == 2
    assert change["old_file_page_count"] == 3
    assert change["new_file_page_count"] == 4
    assert change["old_page_count"] == 10
    assert change["new_page_count"] == 11
    assert change["new_amount_cents"] == 165

    approved = confirm(client, admin, order["id"])
    assert approved["code"] == 0, approved
    view = approved["data"]["order"]
    assert view["status"] == "queuing"
    assert view["page_count"] == 11
    assert view["amount_cents"] == 165
    assert view["requeue_at"] is not None
    assert view["files"][1]["file_index"] == 2
    assert view["files"][1]["file_name"] == "新附录.pdf"
    assert view["files"][1]["page_count"] == 4
    assert view["files"][0]["file_name"] == "正文.pdf"
    assert view["files"][2]["file_name"] == "图册.pdf"

    stored = list(
        db_session.execute(
            select(OrderFile)
            .where(OrderFile.order_id == order["id"])
            .order_by(OrderFile.file_index)
        ).scalars()
    )
    assert [(row.file_index, row.page_count) for row in stored] == [(1, 2), (2, 4), (3, 5)]

    versions = list(
        db_session.execute(
            select(OrderFileVersion)
            .where(OrderFileVersion.order_id == order["id"])
            .order_by(OrderFileVersion.version_no)
        ).scalars()
    )
    # 第 2 份文件多出一个版本，其余文件仍保持自己的当前版本
    assert [(v.file_index, v.version_no, v.is_current) for v in versions] == [
        (1, 1, 1),
        (2, 2, 0),
        (3, 3, 1),
        (2, 4, 1),
    ]

    change_row = db_session.execute(
        select(OrderFileChange).where(OrderFileChange.order_id == order["id"])
    ).scalar_one()
    assert change_row.status == CHANGE_APPROVED
    assert change_row.file_index == 2
    assert change_row.final_amount_cents == 165


def test_多文件订单老师队列显示文件清单与待确认文件(client):
    headers = student_headers(client, "multi-queue")
    admin = admin_headers(client)
    order = create_order(client, headers, [_file_item("正文.pdf", 2), _file_item("附录.pdf", 3)])
    pay_order(client, headers, order["id"])
    assert replace_file(client, headers, order["id"], page_count=4, file_index=2)["code"] == 0

    queue = client.get("/api/admin/queue", headers=admin).json()["data"]
    row = next(item for item in queue["items"] if item["id"] == order["id"])
    assert [item["file_index"] for item in row["files"]] == [1, 2]
    assert row["file_change"]["file_index"] == 2
    assert row["file_change"]["new_file_page_count"] == 4
    # 还没被老师确认过，不属于「文件已替换未确认」
    assert row["files_unconfirmed"] is False


def test_老师下载逐文件链接_可指定某一份(client, test_storage):
    headers = student_headers(client, "multi-download")
    admin = admin_headers(client)
    order = create_order(client, headers, [_file_item("正文.pdf", 2), _file_item("附录.pdf", 3)])

    all_files = client.get(
        f"/api/admin/order/{order['id']}/download", headers=admin
    ).json()
    assert all_files["code"] == 0, all_files
    data = all_files["data"]
    assert data["file_key"] == order["files"][0]["file_key"]
    assert [item["file_index"] for item in data["files"]] == [1, 2]
    assert all(item["signed_url"].startswith("http") for item in data["files"])

    second = client.get(
        f"/api/admin/order/{order['id']}/download",
        params={"file_index": 2},
        headers=admin,
    ).json()["data"]
    assert second["file_index"] == 2
    assert second["file_key"] == order["files"][1]["file_key"]
    assert len(second["files"]) == 2

    student = client.get(
        f"/api/admin/order/{order['id']}/download", headers=headers
    ).json()
    assert student["code"] == 40300


def test_文件替换后标记待确认_老师确认看到后清除(client):
    headers = student_headers(client, "multi-seen")
    admin = admin_headers(client)
    order = create_order(client, headers, [_file_item("正文.pdf", 2), _file_item("附录.pdf", 3)])
    pay_order(client, headers, order["id"])
    assert replace_file(client, headers, order["id"], page_count=4, file_index=2)["code"] == 0
    assert confirm(client, admin, order["id"])["code"] == 0

    detail = client.get(f"/api/order/{order['id']}", headers=headers).json()["data"]
    assert detail["file_changed_at"] is not None
    assert detail["files_seen_at"] is None
    assert detail["files_unconfirmed"] is True

    queue = client.get("/api/admin/queue", headers=admin).json()["data"]
    row = next(item for item in queue["items"] if item["id"] == order["id"])
    assert row["files_unconfirmed"] is True
    assert row["file_changed_at"] is not None

    seen = client.post(
        f"/api/admin/order/{order['id']}/confirm-files-seen", headers=admin
    ).json()
    assert seen["code"] == 0, seen
    assert seen["data"]["files_unconfirmed"] is False
    assert seen["data"]["files_seen_at"] is not None

    again = client.get("/api/admin/queue", headers=admin).json()["data"]
    row = next(item for item in again["items"] if item["id"] == order["id"])
    assert row["files_unconfirmed"] is False

    forbidden = client.post(
        f"/api/admin/order/{order['id']}/confirm-files-seen", headers=headers
    ).json()
    assert forbidden["code"] == 40300


def upload_pdf(client, headers, name: str = "待下单.pdf") -> dict:
    """走真实上传接口，让文件有上传记录（下单归属校验靠它）。"""
    body = client.post(
        "/api/file/upload", files={"file": (name, PDF_BYTES, "application/pdf")}, headers=headers
    ).json()
    assert body["code"] == 0, body
    return body["data"]


def test_下单拒绝别人的已记录文件(client, db_session, test_storage):
    """文件标识格式对但属于别人：下单要拦下，不能靠格式校验放行。"""
    owner = student_headers(client, "order-owner-file")
    other = student_headers(client, "order-other-file")
    uploaded = upload_pdf(client, owner, "别人的论文.pdf")

    body = client.post(
        "/api/order/create",
        json=_create_payload(
            [_file_item("别人的论文.pdf", 2, uploaded["file_key"])]
        ),
        headers=other,
    ).json()

    assert body["code"] == 40404
    assert "不属于当前用户" in body["message"]
    # 被拒的请求不能留下半个订单
    assert db_session.execute(select(Order)).first() is None


def test_下单放行自己的已记录文件(client, db_session, test_storage):
    headers = student_headers(client, "order-own-file")
    uploaded = upload_pdf(client, headers, "我的论文.pdf")

    order = create_order(
        client, headers, [_file_item("我的论文.pdf", 2, uploaded["file_key"])]
    )

    assert order["files"][0]["file_key"] == uploaded["file_key"]
    assert order["files"][0]["file_index"] == 1


def test_下单放行没有上传记录的历史文件(client, db_session):
    """迁移前的老文件没有上传记录，无法判断归属：继续放行，不能挡掉正常下单。"""
    headers = student_headers(client, "order-legacy-file")
    legacy_key = _file_key("legacy")
    assert db_session.execute(
        select(UploadedFile).where(UploadedFile.file_key == legacy_key)
    ).scalar_one_or_none() is None

    order = create_order(client, headers, [_file_item("历史文件.pdf", 2, legacy_key)])

    assert order["files"][0]["file_key"] == legacy_key


def test_撤回未下单文件删除对象与上传记录(client, db_session, test_storage):
    headers = student_headers(client, "withdraw-owner")
    uploaded = client.post(
        "/api/file/upload",
        files={"file": ("待撤回.pdf", PDF_BYTES, "application/pdf")},
        headers=headers,
    ).json()["data"]
    key = uploaded["file_key"]

    record = db_session.execute(
        select(UploadedFile).where(UploadedFile.file_key == key)
    ).scalar_one()
    assert record.user_id > 0
    assert key in {obj.key for obj in test_storage.list_objects("files/")}

    deleted = client.post(
        "/api/file/delete", json={"file_key": key}, headers=headers
    ).json()
    assert deleted["code"] == 0, deleted
    assert deleted["data"]["deleted"] is True
    assert key not in {obj.key for obj in test_storage.list_objects("files/")}
    assert db_session.execute(
        select(UploadedFile).where(UploadedFile.file_key == key)
    ).scalar_one_or_none() is None

    # 再删一次：记录已经没了，返回「不存在或不属于你」，不暴露别人的文件
    again = client.post("/api/file/delete", json={"file_key": key}, headers=headers).json()
    assert again["code"] == 40404


def test_别人不能撤回我的上传文件(client, db_session, test_storage):
    owner = student_headers(client, "withdraw-owner-2")
    other = student_headers(client, "withdraw-other")
    uploaded = client.post(
        "/api/file/upload",
        files={"file": ("我的.pdf", PDF_BYTES, "application/pdf")},
        headers=owner,
    ).json()["data"]

    body = client.post(
        "/api/file/delete", json={"file_key": uploaded["file_key"]}, headers=other
    ).json()
    assert body["code"] == 40404
    assert db_session.execute(
        select(UploadedFile).where(UploadedFile.file_key == uploaded["file_key"])
    ).scalar_one_or_none() is not None


def test_已被订单引用的文件拒绝撤回(client, db_session, test_storage):
    headers = student_headers(client, "withdraw-referenced")
    uploaded = client.post(
        "/api/file/upload",
        files={"file": ("已下单.pdf", PDF_BYTES, "application/pdf")},
        headers=headers,
    ).json()["data"]
    client.post(
        "/api/order/create",
        json={
            "file_key": uploaded["file_key"],
            "file_name": uploaded["file_name"],
            "page_count": 2,
            "print_side": 2,
            "color_mode": 0,
            "copies": 1,
            "paper_size": "A4",
        },
        headers=headers,
    )

    body = client.post(
        "/api/file/delete", json={"file_key": uploaded["file_key"]}, headers=headers
    ).json()
    assert body["code"] == 40920
    assert "已被订单引用" in body["message"]
    assert db_session.execute(
        select(UploadedFile).where(UploadedFile.file_key == uploaded["file_key"])
    ).scalar_one_or_none() is not None


def test_迁移为老订单回填清单行且可重复执行(db_session, engine):
    order = Order(
        order_no="0101-902",
        pickup_code="0101-Z-ZY",
        user_id=1,
        status="queuing",
        file_key=_file_key("legacy-order-file"),
        file_name="老订单.pdf",
        page_count=12,
        amount_cents=180,
    )
    db_session.add(order)
    db_session.commit()

    run_migrations(engine)
    db_session.expire_all()
    files = list(
        db_session.execute(
            select(OrderFile).where(OrderFile.order_id == order.id)
        ).scalars()
    )
    assert [(row.file_index, row.page_count) for row in files] == [(1, 12)]
    versions = list(
        db_session.execute(
            select(OrderFileVersion).where(OrderFileVersion.order_id == order.id)
        ).scalars()
    )
    assert [(v.file_index, v.version_no, v.is_current) for v in versions] == [(1, 1, 1)]

    run_migrations(engine)
    db_session.expire_all()
    assert len(
        list(
            db_session.execute(
                select(OrderFile).where(OrderFile.order_id == order.id)
            ).scalars()
        )
    ) == 1
    assert len(
        list(
            db_session.execute(
                select(OrderFileVersion).where(OrderFileVersion.order_id == order.id)
            ).scalars()
        )
    ) == 1
