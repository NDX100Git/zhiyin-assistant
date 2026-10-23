"""排队快照缓存的测试。

覆盖：缓存命中与失效（下单、支付、取消、换文件确认、开始打印、完成打印）、
换文件后仍排到队尾、同一秒的排序口径不变、空队列与单队列边界、有效期兜底。
"""

from __future__ import annotations

import itertools
import threading
import time
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import select, update

from app.config import settings
from app.models.order import Order
from app.models.uploaded_file import UploadedFile
from app.services import queue_service, queue_snapshot
from app.services.queue_snapshot import QueueSnapshot
from app.utils.security import decode_token

_key_seq = itertools.count(1)

# 下单要求每份文件都有本人的上传记录；用例里造出来的 file_key 在这里登记
_SESSION = None


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


@pytest.fixture(autouse=True)
def capture_session(db_session):
    """把测试会话存到模块里，供 register_upload 使用。"""
    global _SESSION
    _SESSION = db_session
    yield
    _SESSION = None


def register_upload(file_key: str, file_name: str | None, headers: dict[str, str]) -> None:
    """把用例造出来的文件标识登记成该用户的上传记录。"""
    user_id = int(decode_token(headers["Authorization"].split(" ", 1)[1])["sub"])
    existing = _SESSION.execute(
        select(UploadedFile).where(UploadedFile.file_key == file_key)
    ).scalar_one_or_none()
    if existing is not None:
        return
    _SESSION.add(
        UploadedFile(
            user_id=user_id,
            file_key=file_key,
            file_name=file_name,
            size=0,
            content_type="application/pdf",
        )
    )
    _SESSION.commit()


def create_order(client, headers: dict[str, str], page_count: int = 10) -> dict:
    payload = _order_payload(page_count)
    register_upload(payload["file_key"], payload["file_name"], headers)
    body = client.post("/api/order/create", json=payload, headers=headers).json()
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


def create_multi_order(client, headers: dict[str, str], files: list[dict]) -> dict:
    """多文件下单（每份可带自己的 copies 等参数），用于造出打印页数 != 文档页数的单。"""
    for item in files:
        register_upload(item["file_key"], item.get("file_name"), headers)
    body = client.post(
        "/api/order/create",
        json={"files": files, "print_side": 2, "color_mode": 0, "copies": 1, "paper_size": "A4"},
        headers=headers,
    ).json()
    assert body["code"] == 0, body
    return body["data"]


def replace_file(client, headers: dict[str, str], order_id: int, page_count: int) -> dict:
    payload = _replace_payload(page_count)
    register_upload(payload["file_key"], payload["file_name"], headers)
    body = client.post(
        f"/api/order/{order_id}/replace-file",
        json=payload,
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


# ------------------------------------------------ 排队等待按打印页数算（硬口径）


def test_前序页数与预计等待按打印页数算(client, db_session, monkeypatch):
    """两份文件 2 页×2 份、3 页×1 份：文档 5 页、打印 7 页；后一单的前序页数按 7 算。"""
    headers = student_headers(client, "queue-print-pages-basis")
    head = create_multi_order(
        client,
        headers,
        [
            {"file_key": _file_key("正文"), "file_name": "正文.pdf", "page_count": 2, "copies": 2},
            {"file_key": _file_key("附录"), "file_name": "附录.pdf", "page_count": 3, "copies": 1},
        ],
    )
    assert (head["page_count"], head["print_page_count"]) == (5, 7)
    pay_order(client, headers, head["id"])
    tail = queued_order(client, headers, page_count=1)
    assert (tail["page_count"], tail["print_page_count"]) == (1, 1)

    # 固定「一秒/打印页」，等待分钟数就等于打印页数，口径写错会立刻露出来
    monkeypatch.setattr(queue_service, "per_page_seconds", lambda db: 60.0)
    queue_snapshot.invalidate()

    snapshot = queue_service.queue_state(db_session)
    assert snapshot.total == 2
    # 前缀和是 0 -> 7 -> 8；按文档页数会是 0 -> 5 -> 6
    assert snapshot.prefix_pages == (0, 7, 8)
    assert snapshot.total_pages == 8

    items = list_items(client, headers)
    assert items[head["id"]]["queue_position"] == 0
    assert items[tail["id"]]["queue_position"] == 1
    assert items[head["id"]]["estimated_minutes"] == 7
    assert items[tail["id"]]["estimated_minutes"] == 8

    # 单条订单路径（order_wait_info 的 SUM）同一口径
    wait = queue_service.order_wait_info(db_session, db_session.get(Order, tail["id"]))
    assert (wait["queue_position"], wait["estimated_minutes"]) == (1, 8)

    # 对外字段口径不变：文档页数仍是 5
    assert items[head["id"]]["page_count"] == 5


def test_打印页数为0的老订单回落按文档页数算(client, db_session, monkeypatch):
    """老库回填前 print_page_count 是 0：排队页数回落 page_count，不报错。"""
    headers = student_headers(client, "queue-print-pages-legacy")
    order = queued_order(client, headers, page_count=10)

    db_session.execute(update(Order).where(Order.id == order["id"]).values(print_page_count=0))
    db_session.commit()
    queue_snapshot.invalidate()

    monkeypatch.setattr(queue_service, "per_page_seconds", lambda db: 60.0)
    snapshot = queue_service.queue_state(db_session)
    assert snapshot.prefix_pages == (0, 10)

    items = list_items(client, headers)
    assert items[order["id"]]["queue_position"] == 0
    assert items[order["id"]]["estimated_minutes"] == 10
    assert items[order["id"]]["page_count"] == 10
    assert items[order["id"]]["print_page_count"] == 0

    wait = queue_service.order_wait_info(db_session, db_session.get(Order, order["id"]))
    assert (wait["queue_position"], wait["estimated_minutes"]) == (0, 10)


def test_单均耗时按打印页数算(client, db_session):
    """2 文档页 × 2 份 = 4 打印页、240 秒，则「秒/打印页」是 60，不是 120。"""
    student_headers(client, "queue-print-pages-ratio")
    now = datetime.now()
    db_session.add(
        Order(
            order_no="261019-901",
            pickup_code="261019-9-Z1",
            user_id=1,
            status="completed",
            page_count=2,
            print_page_count=4,
            copies=2,
            paid=1,
            amount_cents=100,
            created_at=now - timedelta(seconds=240),
            printed_at=now,
        )
    )
    db_session.commit()

    assert queue_service.per_page_seconds(db_session) == pytest.approx(60.0)
    assert queue_service._minutes_for(60.0, 4, 1) == 4


def test_并发16下位置与预计等待单调且不出现负数(client, session_factory, monkeypatch):
    """16 个线程各拿独立 session 读队：位置与预计等待都要单调、不为负。"""
    headers = student_headers(client, "queue-print-pages-concurrent")
    orders = []
    for index in range(8):
        order = create_multi_order(
            client,
            headers,
            [
                {
                    "file_key": _file_key(f"并发{index}"),
                    "file_name": f"文档{index}.pdf",
                    "page_count": 1 + index % 3,
                    "copies": 1 + index % 2,
                }
            ],
        )
        pay_order(client, headers, order["id"])
        orders.append(order)

    print_pages = [int(order["print_page_count"]) for order in orders]
    assert print_pages != [int(order["page_count"]) for order in orders], "口径不同的数据才算验到"

    monkeypatch.setattr(queue_service, "per_page_seconds", lambda db: 60.0)
    monkeypatch.setattr(queue_snapshot, "ttl_seconds", lambda: 60.0)
    queue_snapshot.invalidate()

    errors: list[BaseException] = []
    seen: list[list[tuple[int, int]]] = []
    barrier = threading.Barrier(16)

    def worker() -> None:
        session = session_factory()
        try:
            barrier.wait(timeout=10)
            active = queue_service.list_active_orders(session)
            waits = queue_service.batch_wait_info(session, active)
            seen.append(
                [(waits[item.id]["queue_position"], waits[item.id]["estimated_minutes"])
                 for item in active]
            )
        except BaseException as exc:  # 线程里的异常不冒泡到主线程，收集起来
            errors.append(exc)
        finally:
            session.close()

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    assert not errors, errors
    assert len(seen) == 16
    for pairs in seen:
        positions = [position for position, _ in pairs]
        minutes = [value for _, value in pairs]
        assert positions == list(range(len(orders))), "排队位置必须连续单调"
        assert all(value >= 0 for value in minutes), "预计等待不允许出现负数"
        assert minutes == sorted(minutes), "预计等待必须随队列单调不减"
        # 一秒/打印页 -> 第 i 单的等待分钟数 = 到它为止的打印页数累计
        assert minutes == list(itertools.accumulate(print_pages))
