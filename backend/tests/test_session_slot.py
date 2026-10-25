"""时段容量归还（取消 / 关闭异常单不占名额）。

只归还「本次下单确实占到」的额度：截单与满员时下的单没有自增容量，取消时也不能减。
历史问题：取消时无条件 `used -= 1`，把别人占着的额度还掉了（实测已用数从 5 掉到 2），
紧接着放进来的新单就是超卖。
"""

from __future__ import annotations

import itertools
from datetime import date, datetime, time

from sqlalchemy import select

from app.models.order import Order
from app.models.queue_session import QueueSession
from app.models.uploaded_file import UploadedFile
from app.services import session_service
from app.services.session_service import release_session_slot
from app.utils.security import decode_token

_TODAY = date.today()
_seq = itertools.count(1)


def _order_row(db_session, *, moment: datetime, took_slot: bool) -> Order:
    """造一张订单；took_slot=True 时把「占到名额」的凭证写进库。"""
    index = next(_seq)
    order = Order(
        order_no=f"0999-{index:03d}",
        pickup_code=f"0999-{index:03d}-A",
        user_id=1,
        status="pending_pay",
        page_count=1,
        print_side=1,
        color_mode=0,
        copies=1,
        paper_size="A4",
        amount_cents=15,
        created_at=moment,
    )
    db_session.add(order)
    db_session.commit()
    if took_slot:
        assert session_service.mark_session_slot_taken(db_session, order.id) is True
        db_session.commit()
    return order


def _session_row(db_session, name: str, used: int) -> QueueSession:
    row = session_service.ensure_session(db_session, _TODAY, name)
    row.used = used
    row.capacity = max(used, 1)
    db_session.commit()
    db_session.refresh(row)
    return row


def _used_now(db_session, row: QueueSession) -> int:
    db_session.expire_all()
    return int(db_session.get(QueueSession, row.id).used)


def _at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(_TODAY, time(hour, minute))


def test_占到名额的订单取消归还一个(db_session):
    row = _session_row(db_session, "pm", used=5)
    order = _order_row(db_session, moment=_at(14), took_slot=True)
    assert release_session_slot(db_session, order) is True
    db_session.commit()
    assert _used_now(db_session, row) == 4


def test_已归零不再减(db_session):
    row = _session_row(db_session, "pm", used=0)
    order = _order_row(db_session, moment=_at(14), took_slot=True)
    assert release_session_slot(db_session, order) is False
    assert _used_now(db_session, row) == 0


def test_时段行不存在时安全跳过(db_session):
    order = _order_row(db_session, moment=_at(14), took_slot=True)
    assert release_session_slot(db_session, order) is False


def test_非收单时段不动容量(db_session):
    order = _order_row(db_session, moment=_at(23), took_slot=True)
    assert release_session_slot(db_session, order) is False
    assert list(db_session.execute(select(QueueSession)).scalars()) == []


def test_上午订单归还到上午时段(db_session):
    row = _session_row(db_session, "am", used=2)
    order = _order_row(db_session, moment=_at(10), took_slot=True)
    assert release_session_slot(db_session, order) is True
    db_session.commit()
    assert _used_now(db_session, row) == 1


def test_同一单不会归还两次(db_session):
    """归还成功就把凭证清掉：重复走到这里不会再减一次容量。"""
    row = _session_row(db_session, "pm", used=3)
    order = _order_row(db_session, moment=_at(14), took_slot=True)

    assert release_session_slot(db_session, order) is True
    db_session.commit()
    assert _used_now(db_session, row) == 2

    assert release_session_slot(db_session, order) is False
    db_session.commit()
    assert _used_now(db_session, row) == 2


# ---------------------------------------------------------------- 真实数据库：容量归还与读优先


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


# ---------------------------------------------------------------- 只归还「确实占到」的名额


def test_满员下的订单取消不会把别人的名额还掉(client, db_session, monkeypatch):
    """满员时下的单没有自增容量，取消它绝不能减 used。

    历史问题：截单/满员的订单取消时无条件 `used -= 1`，把别人占着的额度还掉了，
    实测已用数从 5 掉到 2；接着又放进新单，就是超卖。
    """
    monkeypatch.setattr(session_service, "resolve_session_name", lambda now: "pm")
    row = _pm_row(db_session, capacity=1)
    token = _student_token(client, db_session)

    first = _create_order(client, token)  # 占到名额
    assert _used(db_session, row) == 1
    later = [_create_order(client, token) for _ in range(2)]  # 满员，没占到名额
    assert _used(db_session, row) == 1, "满员后的订单不该再加容量"

    for order in later:
        body = client.post(
            f"/api/order/{order['id']}/cancel", json={"reason": "不打了"}, headers=_auth(token)
        ).json()
        assert body["code"] == 0, body
    assert _used(db_session, row) == 1, "取消没占到名额的单，已用数不能减"

    # 真正占到名额的那单取消才归还
    body = client.post(
        f"/api/order/{first['id']}/cancel", json={"reason": "不打了"}, headers=_auth(token)
    ).json()
    assert body["code"] == 0, body
    assert _used(db_session, row) == 0


def test_容量5时取消满员期3单已用数仍是5(client, db_session, monkeypatch):
    """验收实测的场景：容量 5 占满后又下 3 单，取消这 3 单后已用数必须还是 5。"""
    monkeypatch.setattr(session_service, "resolve_session_name", lambda now: "pm")
    row = _pm_row(db_session, capacity=5)
    token = _student_token(client, db_session)

    holders = [_create_order(client, token) for _ in range(5)]
    assert _used(db_session, row) == 5

    extra = [_create_order(client, token) for _ in range(3)]
    assert _used(db_session, row) == 5

    for order in extra:
        client.post(f"/api/order/{order['id']}/cancel", headers=_auth(token))
    assert _used(db_session, row) == 5, "取消满员期的订单不能动别人占着的名额"

    # 占到名额的单取消，才正常减 1
    client.post(f"/api/order/{holders[0]['id']}/cancel", headers=_auth(token))
    assert _used(db_session, row) == 4


def test_占到名额的标记落在订单上(db_session):
    """凭证落在 orders.slot_taken：占到=1，没占到=0，取消归还后清回 0。"""
    from sqlalchemy import text

    from app.models.order import Order

    row = _pm_row(db_session, capacity=1)
    row.used = 1  # 这单下单时自增过的那一格
    db_session.commit()
    order = Order(
        order_no="0999-901",
        pickup_code="0999-901-A",
        user_id=1,
        status="pending_pay",
        page_count=1,
        print_side=1,
        color_mode=0,
        copies=1,
        paper_size="A4",
        amount_cents=15,
    )
    db_session.add(order)
    db_session.commit()

    def slot_taken() -> int:
        db_session.rollback()
        return int(
            db_session.execute(
                text("SELECT slot_taken FROM `orders` WHERE id = :id"), {"id": order.id}
            ).scalar_one()
        )

    assert slot_taken() == 0, "默认不占名额"

    assert session_service.mark_session_slot_taken(db_session, order.id) is True
    db_session.commit()
    assert slot_taken() == 1

    db_session.rollback()
    assert session_service.release_session_slot(db_session, order) is True
    db_session.commit()
    assert _used(db_session, row) == 0
    assert slot_taken() == 0, "归还过一次就清标记，重复归还不会减第二次"


def test_没占到名额的订单不归还容量(db_session):
    from datetime import datetime

    from app.models.order import Order

    row = _pm_row(db_session, capacity=3)
    row.used = 2
    db_session.commit()

    order = Order(
        order_no="0999-902",
        pickup_code="0999-902-A",
        user_id=1,
        status="pending_pay",
        page_count=1,
        print_side=1,
        color_mode=0,
        copies=1,
        paper_size="A4",
        amount_cents=15,
        created_at=datetime.combine(_TODAY, time(14, 0)),
    )
    db_session.add(order)
    db_session.commit()

    assert session_service.release_session_slot(db_session, order) is False
    db_session.commit()
    assert _used(db_session, row) == 2, "没占到名额就不该动 used"
