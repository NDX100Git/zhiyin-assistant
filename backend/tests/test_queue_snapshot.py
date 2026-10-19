"""排队快照缓存的测试。

覆盖：缓存命中与失效（下单、支付、取消、换文件确认、开始打印、完成打印）、
换文件后仍排到队尾、同一秒的排序口径不变、空队列与单队列边界、有效期兜底。
"""

from __future__ import annotations

import itertools
import threading
import time
from datetime import date, datetime

import pytest
from sqlalchemy import update

from app.config import settings
from app.models.order import Order
from app.services import queue_service, queue_snapshot
from app.services.queue_snapshot import QueueSnapshot

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
    """统计「读整队」那条 SQL 被调了几次，用来区分命中与重建。"""
    calls: list[int] = []
    original = queue_service._read_active_rows

    def counted(db):
        calls.append(1)
        return original(db)

    monkeypatch.setattr(queue_service, "_read_active_rows", counted)
    return calls


def student_headers(client, device_id: str = "queue-snapshot-student") -> dict[str, str]:
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


def create_order(client, headers: dict[str, str], page_count: int = 10) -> dict:
    body = client.post(
        "/api/order/create", json=_order_payload(page_count), headers=headers
    ).json()
    assert body["code"] == 0, body
    return body["data"]


def pay_order(client, headers: dict[str, str], order_id: int) -> dict:
    body = client.post(f"/api/order/{order_id}/pay", headers=headers).json()
    assert body["code"] == 0, body
    return body["data"]


def queued_order(client, headers: dict[str, str], page_count: int = 10) -> dict:
    order = create_order(client, headers, page_count)
    pay_order(client, headers, order["id"])
    return order


def replace_file(client, headers: dict[str, str], order_id: int, page_count: int) -> dict:
    body = client.post(
        f"/api/order/{order_id}/replace-file",
        json=_replace_payload(page_count),
        headers=headers,
    ).json()
    assert body["code"] == 0, body
    return body["data"]


def confirm_change(client, headers: dict[str, str], order_id: int) -> dict:
    body = client.post(
        f"/api/admin/order/{order_id}/confirm-file-change",
        json={"approve": True},
        headers=headers,
    ).json()
    assert body["code"] == 0, body
    return body["data"]


def list_items(client, headers: dict[str, str]) -> dict[int, dict]:
    """列表接口按订单 id 取回排队信息。"""
    body = client.get("/api/order/list", headers=headers).json()
    assert body["code"] == 0, body
    return {item["id"]: item for item in body["data"]["items"]}


# ---------------------------------------------------------------- 缓存命中与失效


def test_连续列表请求复用同一份队列快照(client, queue_reads):
    headers = student_headers(client, "queue-cache-hit")
    for _ in range(3):
        queued_order(client, headers)

    first = list_items(client, headers)
    second = list_items(client, headers)

    assert len(queue_reads) == 1, "有效期内的第二次列表请求不应再读整队"
    assert sorted(item["queue_position"] for item in first.values()) == [0, 1, 2]
    assert sorted(item["queue_position"] for item in second.values()) == [0, 1, 2]


def test_支付后快照立即失效(client, queue_reads):
    headers = student_headers(client, "queue-cache-pay")
    first = queued_order(client, headers)
    pending = create_order(client, headers)

    before = list_items(client, headers)
    assert before[pending["id"]]["queue_position"] == 0
    assert client.get("/api/home/summary", headers=headers).json()["data"]["waiting_count"] == 1

    pay_order(client, headers, pending["id"])
    after = list_items(client, headers)

    # 支付入队后不等有效期，位置立刻反映新队列
    assert after[first["id"]]["queue_position"] == 0
    assert after[pending["id"]]["queue_position"] == 1
    assert client.get("/api/home/summary", headers=headers).json()["data"]["waiting_count"] == 2
    assert len(queue_reads) == 2


def test_取消后快照立即失效(client):
    headers = student_headers(client, "queue-cache-cancel")
    head = queued_order(client, headers)
    tail = queued_order(client, headers)

    before = list_items(client, headers)
    assert before[tail["id"]]["queue_position"] == 1

    body = client.post(f"/api/order/{head['id']}/cancel", json={}, headers=headers).json()
    assert body["code"] == 0, body

    after = list_items(client, headers)
    assert after[tail["id"]]["queue_position"] == 0
    assert client.get("/api/home/summary", headers=headers).json()["data"]["waiting_count"] == 1


def test_开始打印与完成打印后快照立即失效(client):
    admin = admin_headers(client)
    headers = student_headers(client, "queue-cache-print")
    head = queued_order(client, headers)
    tail = queued_order(client, headers)

    before = list_items(client, headers)
    assert (before[head["id"]]["queue_position"], before[tail["id"]]["queue_position"]) == (0, 1)

    # 开始打印：仍在队列里，位置不变，但快照必须重建（printing 也算在途）
    body = client.post(
        f"/api/admin/order/{head['id']}/start", json={}, headers=admin
    ).json()
    assert body["code"] == 0, body
    during = list_items(client, headers)
    assert (during[head["id"]]["queue_position"], during[tail["id"]]["queue_position"]) == (0, 1)

    # 完成打印：离开在途队列，后面的单立刻前进
    body = client.post(
        f"/api/admin/order/{head['id']}/complete", json={}, headers=admin
    ).json()
    assert body["code"] == 0, body
    after = list_items(client, headers)
    assert after[tail["id"]]["queue_position"] == 0
    assert client.get("/api/home/summary", headers=headers).json()["data"]["waiting_count"] == 1


def test_换文件确认后仍排到队尾(client):
    admin = admin_headers(client)
    headers = student_headers(client, "queue-cache-replace")
    first = queued_order(client, headers)
    second = queued_order(client, headers)
    third = queued_order(client, headers)

    before = list_items(client, headers)
    assert before[first["id"]]["queue_position"] == 0

    replaced = replace_file(client, headers, first["id"], page_count=30)
    assert replaced["applied"] is False
    confirm_change(client, admin, first["id"])

    after = list_items(client, headers)
    # 换文件确认后重新排队：原来排第一的那单落到队尾，其余各前进一位
    assert after[second["id"]]["queue_position"] == 0
    assert after[third["id"]]["queue_position"] == 1
    assert after[first["id"]]["queue_position"] == 2
    assert client.get("/api/home/summary", headers=headers).json()["data"]["waiting_count"] == 3
    # 页数也按新文件重算（等 30 页，不是原来的 10 页）
    assert after[first["id"]]["page_count"] == 30


def test_同一秒的排序口径不变(client, db_session):
    """换过文件的单与未换过的单落在同一秒时，仍是未换过的在前。"""
    headers = student_headers(client, "queue-cache-tiebreak")
    plain = queued_order(client, headers)
    requeued = queued_order(client, headers)

    same_second = datetime(2026, 10, 19, 12, 0, 0)
    # 直接改库模拟「两单落在同一秒」：排序只能靠「是否重新排队」与订单号分先后
    db_session.execute(
        update(Order).where(Order.id.in_([plain["id"], requeued["id"]])).values(
            created_at=same_second, requeue_at=same_second
        )
    )
    db_session.commit()
    queue_snapshot.invalidate()

    items = list_items(client, headers)
    assert items[plain["id"]]["queue_position"] == 0
    assert items[requeued["id"]]["queue_position"] == 1


# ------------------------------------------------------------------ 边界与兜底


def test_空队列的学生列表与首页(client):
    headers = student_headers(client, "queue-cache-empty")

    body = client.get("/api/order/list", headers=headers).json()
    assert body["code"] == 0, body
    assert body["data"]["items"] == []

    summary = client.get("/api/home/summary", headers=headers).json()
    assert summary["code"] == 0, summary
    assert summary["data"]["waiting_count"] == 0
    assert summary["data"]["estimated_minutes"] == 0


def test_单队列的位置与预计等待(client):
    headers = student_headers(client, "queue-cache-single")
    only = queued_order(client, headers, page_count=12)

    items = list_items(client, headers)
    assert items[only["id"]]["queue_position"] == 0
    # 没有历史耗时数据时按配置的单均 60 秒估算，12 页也算 1 分钟起
    assert items[only["id"]]["estimated_minutes"] >= 1

    summary = client.get("/api/home/summary", headers=headers).json()["data"]
    assert summary["waiting_count"] == 1
    assert summary["estimated_minutes"] >= 1


def test_有效期到期后重新读队(client, queue_reads, monkeypatch):
    headers = student_headers(client, "queue-cache-ttl")
    queued_order(client, headers)

    monkeypatch.setattr(queue_snapshot, "ttl_seconds", lambda: 0.0)
    list_items(client, headers)
    list_items(client, headers)
    assert len(queue_reads) == 2, "有效期为 0 时每个请求都应重新读队"


def test_有效期内的快照被复用(client, queue_reads, monkeypatch):
    headers = student_headers(client, "queue-cache-ttl-hit")
    queued_order(client, headers)

    monkeypatch.setattr(queue_snapshot, "ttl_seconds", lambda: 60.0)
    list_items(client, headers)
    list_items(client, headers)
    assert len(queue_reads) == 1


def test_有效期到期时并发重建只扫一遍全队(monkeypatch):
    """有效期一到，多个请求同时进来，只应该有一个去扫全队。"""
    queue_snapshot.reset()
    reads: list[int] = []
    barrier = threading.Barrier(8)

    def slow_read(db):
        reads.append(1)
        time.sleep(0.05)
        return [(1, 10)]

    monkeypatch.setattr(queue_service, "_read_active_rows", slow_read)
    monkeypatch.setattr(queue_service, "per_page_seconds", lambda db: None)

    def worker() -> None:
        barrier.wait()
        queue_service.queue_state(object())

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)

    assert len(reads) == 1


def test_构建期间发生失效的快照不会被写回():
    """读库期间队列变了：这份旧快照必须丢掉，不能在有效期里继续被读到。"""
    queue_snapshot.reset()
    snapshot = QueueSnapshot(
        positions={1: 0},
        prefix_pages=(0, 10),
        total=1,
        seconds_per_page=None,
        built_at=time.monotonic(),
    )

    built_generation = queue_snapshot.generation()
    queue_snapshot.invalidate()

    assert queue_snapshot.store(snapshot, built_generation) is False
    assert queue_snapshot.current() is None
