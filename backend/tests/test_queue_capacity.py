"""排队读取容量的守门测试：会改队列的动作一律让快照失效、并发只扫一遍、命中不重扫。

和 test_queue_snapshot.py 的分工：
- test_queue_snapshot.py 守业务口径（位置、队尾重排、等待时间、有效期兜底）；
- 本文件守容量口径（七类写动作的失效、单飞、命中时的 SQL 条数与排队长度无关）。
口径来自 scripts/perf_poll_capacity.py：一万单排队、单进程、并发 16 时每秒不低于 200 次。
"""

from __future__ import annotations

import itertools
import threading
import time
from datetime import date, datetime

import pytest
from sqlalchemy import event, insert, update

from app.config import settings
from app.models.order import Order
from app.services import queue_service, queue_snapshot

_key_seq = itertools.count(1)


def _file_key(name: str = "file") -> str:
    index = next(_key_seq)
    return f"files/2026/10/{index:032x}-{name}.pdf"


def _order_payload(page_count: int = 10) -> dict:
    return {
        "file_key": _file_key("order"),
        "file_name": "原稿.pdf",
        "page_count": page_count,
        "print_side": 2,
        "color_mode": 0,
        "copies": 1,
        "paper_size": "A4",
    }


def _replace_payload(page_count: int = 20) -> dict:
    return {
        "file_key": _file_key("replace"),
        "file_name": "新稿.pdf",
        "page_count": page_count,
        "reason": "传错版本",
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
    """队列用例不依赖真实收单时段。"""
    monkeypatch.setattr("app.services.order_service.current_session_info", lambda db: _session_info())
    monkeypatch.setattr(
        "app.services.session_service.current_session_info", lambda db, now=None: _session_info()
    )


@pytest.fixture
def queue_reads(monkeypatch) -> list[int]:
    """统计「读整队」被调了几次：命中缓存时这个数不应该涨。"""
    calls: list[int] = []
    original = queue_service._read_active_rows

    def counted(db):
        calls.append(1)
        return original(db)

    monkeypatch.setattr(queue_service, "_read_active_rows", counted)
    return calls


def student_headers(client, device_id: str) -> dict[str, str]:
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


def create_order(client, headers, page_count: int = 10) -> dict:
    body = client.post("/api/order/create", json=_order_payload(page_count), headers=headers).json()
    assert body["code"] == 0, body
    return body["data"]


def queued_order(client, headers, page_count: int = 10) -> dict:
    order = create_order(client, headers, page_count)
    body = client.post(f"/api/order/{order['id']}/pay", headers=headers).json()
    assert body["code"] == 0, body
    return order


def admin_post(client, admin, path: str, payload: dict | None = None) -> dict:
    body = client.post(f"/api/admin{path}", json=payload or {}, headers=admin).json()
    assert body["code"] == 0, body
    return body["data"]


def list_positions(client, headers) -> dict[int, int]:
    body = client.get("/api/order/list", headers=headers).json()
    assert body["code"] == 0, body
    return {item["id"]: item["queue_position"] for item in body["data"]["items"]}


def _queue_page(client, headers) -> dict[int, int]:
    body = client.get("/api/admin/queue", headers=headers).json()
    assert body["code"] == 0, body
    items = body["data"]["items"]
    return {item["id"]: item["queue_position"] for item in items}


# --------------------------------------------------- 七类写动作逐一让快照立即失效
#
# 清单来自任务口径：下单、支付、取消、换文件确认、开始打印、完成打印、异常单恢复排队。
# 每条都断言两件事：失效代数涨了（说明 invalidate 被调到），以及队列位置立刻是新的。


def _action_create(client, student, admin):
    """下单：待支付的单不入队，但队列口径要立刻作废（否则新单可能被算进旧快照）。"""
    order = create_order(client, student)
    return {"invisible": order["id"]}


def _action_pay(client, student, admin):
    """支付入队。"""
    order = create_order(client, student)
    body = client.post(f"/api/order/{order['id']}/pay", headers=student).json()
    assert body["code"] == 0, body
    return {"queued": order["id"]}


def _action_cancel(client, student, admin):
    """取消出队。"""
    order = queued_order(client, student)
    body = client.post(f"/api/order/{order['id']}/cancel", json={}, headers=student).json()
    assert body["code"] == 0, body
    return {"left": order["id"]}


def _action_confirm_file_change(client, student, admin):
    """换文件确认：确认后重排到队尾。"""
    head = queued_order(client, student)
    tail = queued_order(client, student)
    body = client.post(
        f"/api/order/{head['id']}/replace-file", json=_replace_payload(30), headers=student
    ).json()
    assert body["code"] == 0, body
    admin_post(client, admin, f"/order/{head['id']}/confirm-file-change", {"approve": True})
    return {"requeued": head["id"], "other": tail["id"]}


def _action_start(client, student, admin):
    """开始打印：仍在途，但状态变了、快照必须重建。"""
    order = queued_order(client, student)
    admin_post(client, admin, f"/order/{order['id']}/start")
    return {"printing": order["id"]}


def _action_complete(client, student, admin):
    """完成打印：离开在途队列。"""
    order = queued_order(client, student)
    admin_post(client, admin, f"/order/{order['id']}/start")
    admin_post(client, admin, f"/order/{order['id']}/complete")
    return {"done": order["id"]}


def _action_recover(client, student, admin):
    """异常单恢复排队：回到队尾。"""
    order = queued_order(client, student)
    admin_post(client, admin, f"/order/{order['id']}/abnormal", {"reason": "卡纸"})
    admin_post(client, admin, f"/order/{order['id']}/recover")
    return {"recovered": order["id"]}


ACTIONS = {
    "下单": _action_create,
    "支付": _action_pay,
    "取消": _action_cancel,
    "换文件确认": _action_confirm_file_change,
    "开始打印": _action_start,
    "完成打印": _action_complete,
    "异常单恢复排队": _action_recover,
}


@pytest.mark.parametrize("name", list(ACTIONS))
def test_七类写动作都让队列快照立即失效(client, name, queue_reads):
    student = student_headers(client, f"queue-capacity-{name}")
    admin = admin_headers(client)

    # 先让队列里有点东西，并让快照建立起来
    queued_order(client, student, page_count=10)
    list_positions(client, student)
    reads_before = len(queue_reads)
    generation_before = queue_snapshot.generation()

    ACTIONS[name](client, student, admin)

    assert queue_snapshot.generation() > generation_before, f"{name} 之后快照必须作废"

    # 下一个请求立刻重建，位置与等待时间都是新的
    positions = list_positions(client, student)
    assert len(queue_reads) == reads_before + 1, f"{name} 之后第一次读取应当重建一次"
    assert positions, "列表不该是空的"


def test_支付与完成打印后位置立刻前进(client, queue_reads):
    """两类最常被看到的写动作，位置变化要当场可见，不能等有效期。"""
    student = student_headers(client, "queue-capacity-move")
    admin = admin_headers(client)

    first = queued_order(client, student)
    second = queued_order(client, student)
    assert list_positions(client, student) == {first["id"]: 0, second["id"]: 1}

    admin_post(client, admin, f"/order/{first['id']}/start")
    admin_post(client, admin, f"/order/{first['id']}/complete")

    assert list_positions(client, student) == {second["id"]: 0, first["id"]: 0}

    third = queued_order(client, student)
    assert list_positions(client, student) == {second["id"]: 0, third["id"]: 1, first["id"]: 0}


def test_异常恢复排队回到原位置(client):
    """异常单恢复后回到它原来的位置（排队时间没变），快照必须当场重建。"""
    student = student_headers(client, "queue-capacity-recover")
    admin = admin_headers(client)

    head = queued_order(client, student)
    tail = queued_order(client, student)
    assert list_positions(client, student)[head["id"]] == 0

    generation_before = queue_snapshot.generation()
    admin_post(client, admin, f"/order/{head['id']}/abnormal", {"reason": "缺墨"})
    restored = admin_post(client, admin, f"/order/{head['id']}/recover")
    assert queue_snapshot.generation() > generation_before

    positions = list_positions(client, student)
    assert positions[head["id"]] == 0
    assert positions[tail["id"]] == 1
    assert restored["status"] == "queuing"


# ------------------------------------------------ 单飞：并发重建只让一个请求扫全队


def test_并发重建只扫一遍全队且共享同一份快照(monkeypatch):
    """有效期到期的瞬间，十几个请求同时进来，只允许一个去读整队，其余等它建好复用。"""
    queue_snapshot.reset()
    reads: list[int] = []
    built: list[object] = []
    barrier = threading.Barrier(16)

    def slow_read(db):
        reads.append(1)
        time.sleep(0.05)
        return [(index, 1) for index in range(1, 1001)]

    monkeypatch.setattr(queue_service, "_read_active_rows", slow_read)
    monkeypatch.setattr(queue_service, "per_page_seconds", lambda db: None)

    def worker() -> None:
        barrier.wait(timeout=10)
        built.append(queue_service.queue_state(object()))

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(reads) == 1, f"只应有一次整队扫描，实际 {len(reads)} 次"
    assert len(built) == 16
    assert len({id(snapshot) for snapshot in built}) == 1, "并发请求应当拿到同一份快照"


def _seed_queue(session, count: int, user_base: int = 900000) -> None:
    """直接往库里灌一批排队单，模拟「队伍很长」。"""
    rows = [
        {
            "order_no": f"CAP{user_base}{index:06d}",
            "pickup_code": f"CAP-{user_base}-{index:05d}",
            "user_id": user_base + index,
            "status": "queuing",
            "page_count": 1 + index % 5,
            "print_side": 2,
            "color_mode": 0,
            "copies": 1,
            "paper_size": "A4",
            "amount_cents": 100,
            "paid": 1,
            "created_at": datetime.now(),
        }
        for index in range(count)
    ]
    session.execute(insert(Order), rows)
    session.commit()


def test_命中缓存时并发读取不重扫全队(session_factory, queue_reads, monkeypatch):
    """两千单排队、有效期内的并发读取（各自独立 session）一次都不该再读整队。"""
    seed = session_factory()
    try:
        _seed_queue(seed, 2000)
    finally:
        seed.close()

    monkeypatch.setattr(queue_snapshot, "ttl_seconds", lambda: 60.0)
    db = session_factory()
    try:
        first = queue_service.queue_state(db)  # 先建一份
    finally:
        db.close()
    assert first.total == 2000
    assert len(queue_reads) == 1

    errors: list[BaseException] = []
    seen: list[object] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        session = session_factory()
        try:
            barrier.wait(timeout=10)
            for _ in range(50):
                seen.append(queue_service.queue_state(session))
        except BaseException as exc:  # 线程里的异常不冒泡到主线程，收集起来
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(seen) == 8 * 50
    assert len({id(snapshot) for snapshot in seen}) == 1, "并发命中应当拿到同一份快照"
    assert len(queue_reads) == 1, "命中缓存时不应再读整队"


# ------------------------------------- 命中时的 SQL 条数与排队长度无关（容量守门）


def _count_statements(db, action) -> int:
    """数一次 action 里发了几条 SQL。"""
    statements: list[str] = []
    bind = db.get_bind()

    def before(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(bind, "before_cursor_execute", before)
    try:
        action()
    finally:
        event.remove(bind, "before_cursor_execute", before)
    return len(statements)


def test_列表读取的SQL条数不随排队长度增长(client, db_session, queue_reads):
    """一单排队与两千单排队，一次列表请求的 SQL 条数必须一样多。

    队列位置来自共享快照，命中缓存时只会走「订单分页 + 待确认换文件 + 文件清单」这几条，
    不会因为队伍变长而多扫。这条守住的是容量：读取成本与排队长度解耦。
    """
    student = student_headers(client, "queue-capacity-sql")
    mine = queued_order(client, student, page_count=10)
    list_positions(client, student)

    small = _count_statements(db_session, lambda: list_positions(client, student))

    # 再灌 2000 单别人排队，队列长度涨 2000 倍
    _seed_queue(db_session, 2000)
    # 直接改库不会触发失效（真实写动作都会），这里显式重建一次来模拟有效期到期
    queue_snapshot.invalidate()
    list_positions(client, student)
    reads_after_big = len(queue_reads)

    big = _count_statements(db_session, lambda: list_positions(client, student))

    assert big == small, f"排队 1 单时 {small} 条 SQL，排队 2001 单时 {big} 条 SQL"
    assert big <= 5, f"命中缓存的一次列表请求不该超过 5 条 SQL，实际 {big} 条"
    assert len(queue_reads) == reads_after_big, "命中缓存时不应再读整队"
    assert mine["id"] in list_positions(client, student)


# ------------------------------------------ 快照顺序与队列口径一致（编译 SQL 不漂移）


def test_快照位置与队列排序键完全一致(client, db_session):
    """快照里的位置必须等于 queue_order_by() 排出来的次序，含同秒与换文件重排。"""
    student = student_headers(client, "queue-capacity-order")
    orders = [queued_order(client, student, page_count=index + 1) for index in range(5)]

    same_second = datetime(2026, 10, 19, 12, 0, 0)
    db_session.execute(
        update(Order).where(Order.id.in_([item["id"] for item in orders])).values(
            created_at=same_second
        )
    )
    db_session.execute(
        update(Order).where(Order.id == orders[4]["id"]).values(
            created_at=same_second, requeue_at=same_second
        )
    )
    db_session.commit()
    queue_snapshot.invalidate()

    snapshot = queue_service.queue_state(db_session)
    expected = [order.id for order in queue_service.list_active_orders(db_session)]

    assert [snapshot.position_of(order_id) for order_id in expected] == list(
        range(len(expected))
    )
    assert set(snapshot.positions) == set(expected)
    # 换过文件的那单落在同一秒里也要排最后
    assert expected[-1] == orders[4]["id"]
