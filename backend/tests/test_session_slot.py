"""时段容量归还（取消 / 关闭异常单不占名额）。

用假会话验证分支，不连数据库：容量只增不减会让时段在取消几单后永久显示「已截单」，
老师手动重开也救不回来。
"""

from __future__ import annotations

from datetime import date, datetime, time
from types import SimpleNamespace
from unittest.mock import MagicMock

from app.services.session_service import release_session_slot

_TODAY = date.today()


def _fake_db(row):
    db = MagicMock()
    db.execute.return_value.scalar_one_or_none.return_value = row
    return db


def _at(hour: int) -> datetime:
    return datetime.combine(_TODAY, time(hour, 0))


def test_取消订单归还一个名额():
    row = SimpleNamespace(date=_TODAY, session="pm", used=5)
    assert release_session_slot(_fake_db(row), _at(14)) is True
    assert row.used == 4


def test_已归零不再减():
    row = SimpleNamespace(date=_TODAY, session="pm", used=0)
    assert release_session_slot(_fake_db(row), _at(14)) is False
    assert row.used == 0


def test_时段行不存在时安全跳过():
    assert release_session_slot(_fake_db(None), _at(14)) is False


def test_非收单时段不动容量():
    db = _fake_db(SimpleNamespace(date=_TODAY, session="pm", used=5))
    assert release_session_slot(db, _at(23)) is False
    assert db.execute.called is False


def test_上午订单归还到上午时段():
    row = SimpleNamespace(date=_TODAY, session="am", used=2)
    assert release_session_slot(_fake_db(row), _at(10)) is True
    assert row.used == 1


# ---------------------------------------------------------------- 真实数据库：容量归还与读优先


from sqlalchemy import select  # noqa: E402

from app.models.queue_session import QueueSession  # noqa: E402
from app.models.uploaded_file import UploadedFile  # noqa: E402
from app.services import session_service  # noqa: E402
from app.utils.security import decode_token  # noqa: E402

_FILE_KEY = "files/2026/10/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa.pdf"
_ORDER_PAYLOAD = {
    "file_key": _FILE_KEY,
    "file_name": "时段容量.pdf",
    "page_count": 10,
    "print_side": 2,
    "color_mode": 0,
    "copies": 1,
    "paper_size": "A4",
}


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _student_token(client, db_session, device_id: str = "session-slot-device") -> str:
    body = client.post("/api/auth/login", json={"device_id": device_id}).json()
    assert body["code"] == 0, body
    token = body["data"]["token"]
    db_session.add(
        UploadedFile(
            user_id=int(decode_token(token)["sub"]),
            file_key=_FILE_KEY,
            file_name=_ORDER_PAYLOAD["file_name"],
            size=0,
            content_type="application/pdf",
        )
    )
    db_session.commit()
    return token


def _admin_token(client) -> str:
    from app.config import settings

    body = client.post(
        "/api/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
    ).json()
    assert body["code"] == 0, body
    return body["data"]["token"]


def _pm_row(db_session, capacity: int = 1) -> QueueSession:
    """把今天的下午时段设成指定容量，测试不依赖默认 30。"""
    row = session_service.ensure_session(db_session, _TODAY, "pm")
    row.capacity = capacity
    db_session.commit()
    db_session.refresh(row)
    return row


def _used(db_session, row: QueueSession) -> int:
    db_session.expire_all()
    return int(db_session.get(QueueSession, row.id).used)


def _create_order(client, token: str) -> dict:
    body = client.post("/api/order/create", json=_ORDER_PAYLOAD, headers=_auth(token)).json()
    assert body["code"] == 0, body
    return body["data"]


def test_取消后立即能再下单(client, db_session, monkeypatch):
    """容量 1 的时段：下单占满 -> 学生取消归还 -> 立刻能再下一单，不会永久截单。"""
    monkeypatch.setattr(session_service, "resolve_session_name", lambda now: "pm")
    row = _pm_row(db_session, capacity=1)

    token = _student_token(client, db_session)
    first = _create_order(client, token)
    assert _used(db_session, row) == 1
    # 满员即截单：这一单之后新的下单会排到下一个时段
    assert session_service.current_session_info(db_session)["is_open"] is False

    canceled = client.post(
        f"/api/order/{first['id']}/cancel", json={"reason": "不打了"}, headers=_auth(token)
    ).json()
    assert canceled["code"] == 0, canceled
    assert _used(db_session, row) == 0
    # 容量回来了：满员截单的状态变成可收单（不归还的话 used 会一直等于 capacity）
    assert session_service.current_session_info(db_session)["is_open"] is True

    # 归还之后立刻能再下一单，且新单真的又占到了这个名额
    second = _create_order(client, token)
    assert second["status"] == "pending_pay"
    assert _used(db_session, row) == 1


def test_关闭异常单也归还容量(client, db_session, monkeypatch):
    monkeypatch.setattr(session_service, "resolve_session_name", lambda now: "pm")
    row = _pm_row(db_session, capacity=1)

    student = _student_token(client, db_session)
    order = _create_order(client, student)
    assert _used(db_session, row) == 1

    # 老师只能关异常单：先支付入队，再标异常，再关闭
    paid = client.post(f"/api/order/{order['id']}/pay", headers=_auth(student)).json()
    assert paid["code"] == 0, paid

    admin = _admin_token(client)
    marked = client.post(
        f"/api/admin/order/{order['id']}/abnormal",
        json={"reason": "缺墨"},
        headers=_auth(admin),
    ).json()
    assert marked["code"] == 0, marked
    closed = client.post(
        f"/api/admin/order/{order['id']}/cancel",
        json={"reason": "缺墨，重打"},
        headers=_auth(admin),
    ).json()
    assert closed["code"] == 0, closed
    assert _used(db_session, row) == 0


def test_容量归还不会减成负数(client, db_session, monkeypatch):
    monkeypatch.setattr(session_service, "resolve_session_name", lambda now: "pm")
    row = _pm_row(db_session, capacity=1)

    student = _student_token(client, db_session)
    order = _create_order(client, student)
    client.post(f"/api/order/{order['id']}/cancel", headers=_auth(student))

    # 再取消一次（同一单已取消，接口会拒绝，容量不能被减第二次）
    again = client.post(f"/api/order/{order['id']}/cancel", headers=_auth(student)).json()
    assert again["code"] != 0
    assert _used(db_session, row) == 0


def test_读路径不写时段行_缺失才创建(client, db_session):
    """get_session 只读不建行；ensure_session 缺失时才创建，且不再走锁定读。"""
    assert session_service.get_session(db_session, _TODAY, "am") is None
    assert list(db_session.execute(select(QueueSession)).scalars()) == []

    row = session_service.ensure_session(db_session, _TODAY, "am")
    db_session.commit()
    assert row.session == "am"
    assert int(row.used) == 0

    again = session_service.get_session(db_session, _TODAY, "am")
    assert again is not None and again.id == row.id
    assert len(list(db_session.execute(select(QueueSession)).scalars())) == 1


def test_时段行不存在时首页仍能正常返回(client, db_session, monkeypatch):
    """首页概览读到缺失时段时：能正常返回当天收单状态，并顺手建好那一行。"""
    frozen = datetime.combine(_TODAY, time(10, 30))  # 上午时段窗口内

    class _FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return frozen

    monkeypatch.setattr(session_service, "datetime", _FrozenDatetime)
    assert session_service.get_session(db_session, _TODAY, "am") is None

    token = _student_token(client, db_session)
    body = client.get("/api/home/summary", headers=_auth(token)).json()

    assert body["code"] == 0, body
    session = body["data"]["order_session"]
    assert session["session"] == "am"
    assert session["used"] == 0
    assert session["capacity"] == 30
    assert session["is_open"] is True
    assert session["can_order"] is True

    # 第二次读不再插行：行数仍是 1，容量消耗也未变
    again = client.get("/api/home/summary", headers=_auth(token)).json()
    assert again["code"] == 0
    db_session.expire_all()
    rows = list(db_session.execute(select(QueueSession)).scalars())
    assert len(rows) == 1
    assert int(rows[0].used) == 0
