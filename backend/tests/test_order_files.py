"""多文件订单、逐文件换文件、撤回上传文件与老师确认标记的接口测试。

单文件旧行为由 test_order_api.py 与 test_file_change.py 继续覆盖；本文件只测
「一单多文件」新增的数据与接口，以及老订单迁移回填。
"""

from __future__ import annotations

import itertools
from datetime import date

import pytest
from sqlalchemy import func, select

from app.config import settings
from app.migrations import run_migrations
from app.models.order import Order
from app.models.order_file import OrderFile
from app.models.order_file_change import CHANGE_APPROVED, OrderFileChange
from app.models.order_file_version import OrderFileVersion
from app.models.uploaded_file import UploadedFile
from app.utils.security import decode_token

PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
_key_seq = itertools.count(1)

# 下单现在要求每份文件都有本人的上传记录；用例里造出来的 file_key 统一在这里登记
_SESSION = None


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


@pytest.fixture(autouse=True)
def capture_session(db_session):
    """把测试会话存到模块里，供下面的小工具登记上传记录。"""
    global _SESSION
    _SESSION = db_session
    yield
    _SESSION = None


def _user_id(headers: dict[str, str]) -> int:
    return int(decode_token(headers["Authorization"].split(" ", 1)[1])["sub"])


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


def create_order(client, headers, files: list[dict], **overrides) -> dict:
    owner_id = _user_id(headers)
    for item in files:
        register_upload(item["file_key"], item.get("file_name"), owner_id)
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
    register_upload(key, "单文件.pdf", _user_id(headers))
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
    # 单文件旧写法不传逐份参数，四个参数全部继承订单级
    assert data["files"][0]["print_pages"] == 42
    assert data["files"][0]["print_side"] == 2
    assert data["files"][0]["color_mode"] == 0
    assert data["files"][0]["paper_size"] == "A4"
    assert data["files"][0]["copies"] == 1
    assert data["print_page_count"] == 42


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
    assert body["message"] == "文件不存在或不属于当前用户，请重新上传"
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


def test_下单拒绝没有上传记录的文件(client, db_session):
    """没有上传记录就无法确认归属，一律拒绝；提示与错误码与「别人的文件」完全一致。"""
    headers = student_headers(client, "order-legacy-file")
    legacy_key = _file_key("legacy")
    assert db_session.execute(
        select(UploadedFile).where(UploadedFile.file_key == legacy_key)
    ).scalar_one_or_none() is None

    # 直接调接口，绕开用例工具里的上传登记，才能真的造出「没有记录」的场景
    body = client.post(
        "/api/order/create",
        json=_create_payload([_file_item("历史文件.pdf", 2, legacy_key)]),
        headers=headers,
    ).json()

    assert body["code"] == 40404
    assert body["message"] == "文件不存在或不属于当前用户，请重新上传"
    assert db_session.execute(select(Order)).first() is None


def test_多文件下单时任意一份没有记录整单被拒(client, db_session):
    """逐个文件校验：第二份没有上传记录时，整单不能落地。"""
    headers = student_headers(client, "order-partial-owner")
    owned = _file_item("正文.pdf", 2)
    stray_key = _file_key("stray")
    stray = _file_item("外来的附录.pdf", 3, stray_key)
    register_upload(owned["file_key"], owned["file_name"], _user_id(headers))

    body = client.post(
        "/api/order/create",
        json=_create_payload([owned, stray]),
        headers=headers,
    ).json()

    assert body["code"] == 40404
    assert body["message"] == "文件不存在或不属于当前用户，请重新上传"
    assert db_session.execute(select(Order)).first() is None
    assert db_session.execute(select(UploadedFile).where(
        UploadedFile.file_key == stray_key
    )).scalar_one_or_none() is None


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


# ---------------------------------------------------------------- 逐份打印参数


def _params(name: str, pages: int, **params) -> dict:
    item = _file_item(name, pages)
    item.update(params)
    return item


def test_逐份参数不同时各自算行金额与打印页数(client):
    """三份文件参数各不相同：行金额、整单金额、打印页数都按各自参数算。"""
    headers = student_headers(client, "multi-per-file-params")
    files = [
        # 2 页 × 单面黑白 15 分 × 3 份 = 90 分，打印 6 页
        _params("正文.pdf", 2, print_side=1, color_mode=0, paper_size="A4", copies=3),
        # 3 页 × 双面彩色 80 分 × 2 份 = 480 分，打印 6 页
        _params("图册.pdf", 3, print_side=2, color_mode=1, paper_size="A3", copies=2),
        # 不传逐份参数：继承订单级（双面黑白 A4 × 1 份）= 5 × 15 = 75 分
        _file_item("附录.pdf", 5),
    ]
    order = create_order(client, headers, files)

    assert [item["amount_cents"] for item in order["files"]] == [90, 480, 75]
    assert order["amount_cents"] == 645
    assert [item["print_pages"] for item in order["files"]] == [6, 6, 5]
    assert order["print_page_count"] == 17
    # 订单 page_count 仍只是文档页数之和
    assert order["page_count"] == 10
    # 逐份参数原样回显，学生端与老师端拿到的是同一套键
    assert [
        (item["print_side"], item["color_mode"], item["paper_size"], item["copies"])
        for item in order["files"]
    ] == [(1, 0, "A4", 3), (2, 1, "A3", 2), (2, 0, "A4", 1)]


def test_文档页数与打印页数口径分开(client, db_session):
    """一份文件 4 页 5 份：文档页数 4，打印页数 20，金额按 4 × 5 份算。"""
    headers = student_headers(client, "multi-print-pages")
    order = create_order(
        client, headers, [_params("讲义.pdf", 4, copies=5)]
    )

    assert order["page_count"] == 4
    assert order["print_page_count"] == 20
    assert order["files"][0]["page_count"] == 4
    assert order["files"][0]["print_pages"] == 20
    assert order["files"][0]["copies"] == 5
    assert order["amount_cents"] == 300

    stored = db_session.execute(
        select(OrderFile).where(OrderFile.order_id == order["id"])
    ).scalar_one()
    assert (stored.page_count, stored.print_pages, stored.copies) == (4, 20, 5)
    assert db_session.get(Order, order["id"]).print_page_count == 20


def test_不传逐份参数时继承订单级值(client):
    """订单级份数/纸张/单双面/颜色被逐份继承；传了只覆盖那一份。"""
    headers = student_headers(client, "multi-inherit")
    files = [_file_item("甲.pdf", 2), _params("乙.pdf", 3, copies=2)]
    order = create_order(
        client, headers, files, print_side=1, color_mode=1, paper_size="B5", copies=4
    )

    first, second = order["files"]
    assert (first["print_side"], first["color_mode"], first["paper_size"], first["copies"]) == (
        1,
        1,
        "B5",
        4,
    )
    # 只覆盖份数，其余三项继续继承订单级
    assert (second["print_side"], second["color_mode"], second["paper_size"]) == (1, 1, "B5")
    assert second["copies"] == 2
    # 彩色单面 80 分：2 × 4 份 = 640 分；3 × 2 份 = 480 分
    assert [item["amount_cents"] for item in order["files"]] == [640, 480]
    assert order["amount_cents"] == 1120
    assert order["print_page_count"] == 8 + 6


def test_逐份参数越界用订单级错误码(client):
    headers = student_headers(client, "multi-bad-params")

    def create(items: list[dict]) -> dict:
        return client.post(
            "/api/order/create", json=_create_payload(items), headers=headers
        ).json()

    assert create([_params("a.pdf", 2, print_side=3)])["code"] == 40042
    assert create([_params("a.pdf", 2, color_mode=2)])["code"] == 40043
    assert create([_params("a.pdf", 2, copies=0)])["code"] == 40044
    assert create([_params("a.pdf", 2, copies=51)])["code"] == 40044
    assert create([_params("a.pdf", 2, paper_size="A0")])["code"] == 40046


def test_换第二份只改第二份的参数与行金额(client, db_session):
    """换文件时四参数与份数沿用该行原值，其余行的参数与金额一动不动。"""
    headers = student_headers(client, "multi-replace-per-file")
    admin = admin_headers(client)
    files = [
        _params("正文.pdf", 2, print_side=1, color_mode=0, paper_size="A4", copies=3),
        _params("图册.pdf", 3, print_side=2, color_mode=1, paper_size="A3", copies=2),
        _params("附录.pdf", 5, print_side=2, color_mode=0, paper_size="B5", copies=1),
    ]
    order = create_order(client, headers, files)
    assert [item["amount_cents"] for item in order["files"]] == [90, 480, 75]
    assert order["amount_cents"] == 645
    assert order["print_page_count"] == 17
    pay_order(client, headers, order["id"])

    pending = replace_file(
        client, headers, order["id"], page_count=4, file_index=2, file_name="新图册.pdf"
    )
    assert pending["code"] == 0, pending
    change = pending["data"]["file_change"]
    assert change["file_index"] == 2
    assert change["old_file_page_count"] == 3
    assert change["new_file_page_count"] == 4
    assert (change["old_file_print_pages"], change["new_file_print_pages"]) == (6, 8)
    assert change["old_file_amount_cents"] == 480
    assert change["new_file_amount_cents"] == 640
    # 整单：90 + 640 + 75
    assert change["new_amount_cents"] == 805

    approved = confirm(client, admin, order["id"])
    assert approved["code"] == 0, approved
    view = approved["data"]["order"]
    first, second, third = view["files"]
    # 第 1、3 份的参数、份数、行金额保持不变
    assert (first["print_side"], first["color_mode"], first["paper_size"], first["copies"]) == (
        1,
        0,
        "A4",
        3,
    )
    assert first["amount_cents"] == 90
    assert first["page_count"] == 2
    assert first["print_pages"] == 6
    assert (third["print_side"], third["color_mode"], third["paper_size"], third["copies"]) == (
        2,
        0,
        "B5",
        1,
    )
    assert third["amount_cents"] == 75
    assert third["print_pages"] == 5
    # 第 2 份只有页数变了，参数继续沿用原值
    assert second["page_count"] == 4
    assert second["amount_cents"] == 640
    assert second["print_pages"] == 8
    assert (second["print_side"], second["color_mode"], second["paper_size"], second["copies"]) == (
        2,
        1,
        "A3",
        2,
    )
    assert view["page_count"] == 11
    assert view["print_page_count"] == 6 + 8 + 5
    assert view["amount_cents"] == 805

    order_row = db_session.get(Order, order["id"])
    assert (order_row.page_count, order_row.print_page_count) == (11, 19)
    assert order_row.amount_cents == 805
    stored = {
        row.file_index: row
        for row in db_session.execute(
            select(OrderFile).where(OrderFile.order_id == order["id"])
        ).scalars()
    }
    assert (stored[1].copies, stored[1].print_pages, stored[1].amount_cents) == (3, 6, 90)
    assert (stored[2].copies, stored[2].print_pages, stored[2].amount_cents) == (2, 8, 640)
    assert (stored[3].copies, stored[3].print_pages, stored[3].amount_cents) == (1, 5, 75)


def test_老师端报价与队列回显逐份参数(client):
    """队列、详情、报价用的都是逐份参数累加出来的整单系统价。"""
    headers = student_headers(client, "multi-admin-quote")
    admin = admin_headers(client)
    files = [
        _params("正文.pdf", 2, print_side=1, color_mode=0, copies=2),
        _params("图册.pdf", 3, print_side=2, color_mode=1, copies=1),
    ]
    order = create_order(client, headers, files)
    pay_order(client, headers, order["id"])

    queue = client.get("/api/admin/queue", headers=admin).json()["data"]
    row = next(item for item in queue["items"] if item["id"] == order["id"])
    assert [item["print_pages"] for item in row["files"]] == [4, 3]
    assert [item["copies"] for item in row["files"]] == [2, 1]
    assert row["print_page_count"] == 7
    assert row["amount_cents"] == 60 + 240

    quote = client.get(f"/api/admin/order/{order['id']}/quote", headers=admin).json()["data"]
    assert quote["system_amount_cents"] == 300


def test_迁移补齐逐份参数与打印页数且可重复执行(db_session, engine):
    """老库启动补齐四列与打印页数：按订单级值回填，跑两遍不报错也不改坏数据。"""
    # 老库的写法：订单上有参数，清单行还没有逐份参数与 print_pages
    order = Order(
        order_no="0101-903",
        pickup_code="0101-Z-ZX",
        user_id=1,
        status="queuing",
        file_key=_file_key("migrate-params"),
        file_name="老订单主文件.pdf",
        page_count=12,
        print_side=1,
        color_mode=1,
        paper_size="A3",
        copies=3,
        amount_cents=1080,
    )
    db_session.add(order)
    db_session.commit()
    old_files = [
        OrderFile(
            order_id=order.id,
            file_index=index,
            file_key=_file_key(f"migrate-{index}"),
            file_name=f"老文件{index}.pdf",
            page_count=pages,
            amount_cents=pages * 240 * 3,
        )
        for index, pages in ((1, 2), (2, 3), (3, 5))
    ]
    db_session.add_all(old_files)
    db_session.commit()
    assert db_session.get(Order, order.id).print_page_count == 0

    run_migrations(engine)
    db_session.commit()  # 结束旧快照，才能读到另一个连接里刚跑完的迁移
    db_session.expire_all()

    rows = list(
        db_session.execute(
            select(OrderFile)
            .where(OrderFile.order_id == order.id)
            .order_by(OrderFile.file_index)
        ).scalars()
    )
    # 四列按订单级值回填
    assert [
        (row.print_side, row.color_mode, row.paper_size, row.copies) for row in rows
    ] == [(1, 1, "A3", 3)] * 3
    # 该份打印页数 = page_count × copies
    assert [row.print_pages for row in rows] == [6, 9, 15]
    refreshed = db_session.get(Order, order.id)
    assert refreshed.page_count == 12
    assert refreshed.print_page_count == 30

    # 第二遍：不报错、不改数据
    run_migrations(engine)
    db_session.commit()
    db_session.expire_all()
    again = list(
        db_session.execute(
            select(OrderFile)
            .where(OrderFile.order_id == order.id)
            .order_by(OrderFile.file_index)
        ).scalars()
    )
    assert [(row.page_count, row.copies, row.print_pages) for row in again] == [
        (2, 3, 6),
        (3, 3, 9),
        (5, 3, 15),
    ]
    assert db_session.get(Order, order.id).print_page_count == 30


def test_对外文件项统一同一套键(client, test_storage):
    """学生端与老师端拿到的每个文件项都是同一套键（契约第 8 条）。"""
    from app.services.order_file_service import FILE_VIEW_KEYS

    headers = student_headers(client, "multi-view-keys")
    admin = admin_headers(client)
    order = create_order(
        client,
        headers,
        [_params("正文.pdf", 2, copies=2), _file_item("附录.pdf", 3)],
    )
    pay_order(client, headers, order["id"])
    expected = set(FILE_VIEW_KEYS)

    detail = client.get(f"/api/order/{order['id']}", headers=headers).json()["data"]
    assert [set(item) for item in detail["files"]] == [expected, expected]

    listing = client.get("/api/order/list", headers=headers).json()["data"]
    row = next(item for item in listing["items"] if item["id"] == order["id"])
    assert [set(item) for item in row["files"]] == [expected, expected]

    queue = client.get("/api/admin/queue", headers=admin).json()["data"]
    queued = next(item for item in queue["items"] if item["id"] == order["id"])
    assert [set(item) for item in queued["files"]] == [expected, expected]

    links = client.get(
        f"/api/admin/order/{order['id']}/download", headers=admin
    ).json()["data"]
    assert all(set(item) == expected | {"signed_url"} for item in links["files"])

    started = client.post(f"/api/admin/order/{order['id']}/start", headers=admin).json()
    assert started["code"] == 0, started
    job_files = started["data"]["print_job"]["files"]
    assert all(set(item) == expected | {"signed_url"} for item in job_files)
    view_files = started["data"]["order"]["files"]
    assert [set(item) for item in view_files] == [expected, expected]


# ---------------------------------------------------------------- 服务端报价接口


def quote(client, headers, files: list[dict], **overrides) -> dict:
    """调报价接口：文件项与下单完全同一份写法，只算不落库。"""
    return client.post(
        "/api/order/quote", json=_create_payload(files, **overrides), headers=headers
    ).json()


def test_报价逐份行金额与三种合计(client):
    """两份参数不同：行金额、文档页数、打印页数、总额都由服务端算，且与下单一致。"""
    headers = student_headers(client, "quote-per-file")
    files = [
        # 2 页 × 单面黑白 15 分 × 3 份 = 90 分，打印 6 页
        _params("正文.pdf", 2, print_side=1, color_mode=0, paper_size="A4", copies=3),
        # 3 页 × 双面彩色 80 分 × 2 份 = 480 分，打印 6 页
        _params("图册.pdf", 3, print_side=2, color_mode=1, paper_size="B5", copies=2),
    ]
    for item in files:
        register_upload(item["file_key"], item.get("file_name"), _user_id(headers))

    body = quote(client, headers, files)
    assert body["code"] == 0, body
    data = body["data"]

    assert [item["file_index"] for item in data["files"]] == [1, 2]
    assert [item["amount_cents"] for item in data["files"]] == [90, 480]
    assert [item["print_pages"] for item in data["files"]] == [6, 6]
    assert [
        (item["page_count"], item["print_side"], item["color_mode"], item["paper_size"],
         item["copies"])
        for item in data["files"]
    ] == [(2, 1, 0, "A4", 3), (3, 2, 1, "B5", 2)]
    assert data["page_count"] == 5
    assert data["print_page_count"] == 12
    assert data["amount_cents"] == 570
    # 报价项与订单文件项是同一套键名，前端用一套归一化逻辑
    from app.services.order_file_service import FILE_VIEW_KEYS

    assert [set(item) for item in data["files"]] == [set(FILE_VIEW_KEYS)] * 2

    # 同一份参数下单，落库金额与报价逐项相等
    order = create_order(client, headers, files)
    assert [item["amount_cents"] for item in order["files"]] == [
        item["amount_cents"] for item in data["files"]
    ]
    assert order["print_page_count"] == data["print_page_count"]
    assert order["page_count"] == data["page_count"]
    assert order["amount_cents"] == data["amount_cents"]


def test_报价不传逐份参数继承订单级(client):
    """逐份参数为空时沿用订单级同名字段，只覆盖传了的那一项。"""
    headers = student_headers(client, "quote-inherit")
    files = [_file_item("甲.pdf", 2), _params("乙.pdf", 3, copies=2)]
    for item in files:
        register_upload(item["file_key"], item.get("file_name"), _user_id(headers))

    body = quote(
        client, headers, files, print_side=1, color_mode=1, paper_size="B5", copies=4
    )
    assert body["code"] == 0, body
    first, second = body["data"]["files"]
    assert (first["print_side"], first["color_mode"], first["paper_size"], first["copies"]) == (
        1,
        1,
        "B5",
        4,
    )
    assert (second["print_side"], second["color_mode"], second["paper_size"]) == (1, 1, "B5")
    assert second["copies"] == 2
    # 彩色单面 80 分：2 × 4 份 = 640 分；3 × 2 份 = 480 分
    assert [first["amount_cents"], second["amount_cents"]] == [640, 480]
    assert body["data"]["amount_cents"] == 1120
    assert body["data"]["print_page_count"] == 8 + 6


def test_报价越界参数报与下单相同的码(client):
    """报价与下单同一套校验：错误码与中文提示逐条相同。"""
    headers = student_headers(client, "quote-bad-params")

    def both(items: list[dict]) -> tuple[dict, dict]:
        for item in items:
            register_upload(item["file_key"], item.get("file_name"), _user_id(headers))
        return quote(client, headers, items), client.post(
            "/api/order/create", json=_create_payload(items), headers=headers
        ).json()

    for item in (
        _params("a.pdf", 2, print_side=3),
        _params("a.pdf", 2, color_mode=2),
        _params("a.pdf", 2, copies=0),
        _params("a.pdf", 2, copies=51),
        _params("a.pdf", 2, paper_size="A0"),
    ):
        quoted, created = both([item])
        assert quoted["code"] != 0
        assert quoted["code"] == created["code"]
        assert quoted["message"] == created["message"]

    too_many = [_file_item(f"many-{index}.pdf", 1) for index in range(21)]
    quoted, created = both(too_many)
    assert quoted["code"] == created["code"] == 40048


def test_报价别人的文件与没有记录的文件都是40404(client):
    """归属校验与下单一致：不存在与是别人的返回同一条提示与同一个码。"""
    headers = student_headers(client, "quote-owner")
    other = student_headers(client, "quote-owner-other")
    mine = _file_item("我的.pdf", 2)
    stolen = _file_item("别人的.pdf", 2)
    register_upload(mine["file_key"], mine["file_name"], _user_id(headers))
    register_upload(stolen["file_key"], stolen["file_name"], _user_id(other))

    quoted = quote(client, headers, [stolen])
    assert quoted["code"] == 40404
    created = client.post(
        "/api/order/create", json=_create_payload([stolen]), headers=headers
    ).json()
    assert created["code"] == 40404
    assert quoted["message"] == created["message"]

    unknown = quote(client, headers, [_file_item("没上传过.pdf", 2)])
    assert unknown["code"] == 40404
    assert unknown["message"] == quoted["message"]


def test_报价不落库(client, db_session):
    """报价只算不写：订单表与订单文件表条数都不变，队列容量也不动。"""
    from app.models.queue_session import QueueSession

    headers = student_headers(client, "quote-readonly")
    files = [
        _params("正文.pdf", 2, print_side=1, color_mode=0, copies=3),
        _params("图册.pdf", 3, print_side=2, color_mode=1, copies=2),
    ]
    for item in files:
        register_upload(item["file_key"], item.get("file_name"), _user_id(headers))

    def counts() -> tuple[int, int, int]:
        return (
            int(db_session.execute(select(func.count()).select_from(Order)).scalar_one()),
            int(
                db_session.execute(select(func.count()).select_from(OrderFile)).scalar_one()
            ),
            int(
                db_session.execute(select(func.count()).select_from(QueueSession)).scalar_one()
            ),
        )

    db_session.expire_all()
    before = counts()
    for _ in range(3):
        body = quote(client, headers, files)
        assert body["code"] == 0, body
    db_session.expire_all()
    assert counts() == before
    # 报价确实算出了数：不是“什么都没做所以没写”
    assert body["data"]["amount_cents"] == 570


def test_报价需要登录(client):
    body = client.post(
        "/api/order/quote", json=_create_payload([_file_item("a.pdf", 2)])
    ).json()
    assert body["code"] == 40100
