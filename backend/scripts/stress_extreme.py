"""极端规模压测：先灌一万单，再看它在万人排队时的读写表现。

跑法（先按 RUNBOOK 第 13 节起一个指向 `zhiyin_stress` 库的实例）：

    DB_NAME=zhiyin_stress <venv>/bin/python scripts/stress_extreme.py \\
        --base-url http://127.0.0.1:8001 --total 10000 --target-rps 100

三个阶段：

1. 灌库：多线程不限速下单，每满一千单打一次分段吞吐，用来看队列表变大后写入是否变慢
2. 读数：一万单在库时，老师端队列首页、深页、关键词搜索、学生订单列表、订单详情的响应时间
3. 混合：同时跑「每秒一百单下单」与「两百个学生盯着列表刷新」，持续一段时间

脚本只肯清名字以 `_stress` 结尾的库。
"""

from __future__ import annotations

import argparse
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import settings  # noqa: E402

FILE_KEY = "files/2026/10/extreme.pdf"
ORDER_BODY = {
    "file_key": FILE_KEY,
    "file_name": "extreme.pdf",
    "page_count": 20,
    "print_side": 2,
    "color_mode": 0,
    "copies": 1,
    "paper_size": "A4",
}

_local = threading.local()


def _client(base_url: str) -> httpx.Client:
    client = getattr(_local, "client", None)
    if client is None:
        client = httpx.Client(base_url=base_url, timeout=120)
        _local.client = client
    return client


def _percentile(values: list[float], ratio: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = min(len(ordered) - 1, round(ratio * (len(ordered) - 1)))
    return ordered[index]


def _report(label: str, latencies: list[float]) -> None:
    if not latencies:
        print(f"  {label}：无样本")
        return
    print(
        f"  {label}：{len(latencies)} 次，p50 {_percentile(latencies, 0.5) * 1000:.0f} ms，"
        f"p95 {_percentile(latencies, 0.95) * 1000:.0f} ms，"
        f"p99 {_percentile(latencies, 0.99) * 1000:.0f} ms，"
        f"最大 {max(latencies) * 1000:.0f} ms"
    )


def _login(base_url: str, device_id: str) -> str:
    with httpx.Client(base_url=base_url, timeout=60) as client:
        return client.post("/api/auth/login", json={"device_id": device_id}).json()["data"]["token"]


def _create(base_url: str, token: str) -> tuple[float, int]:
    started = time.perf_counter()
    body = _client(base_url).post(
        "/api/order/create",
        headers={"Authorization": f"Bearer {token}"},
        json=ORDER_BODY,
    ).json()
    return time.perf_counter() - started, int(body.get("code", -1))


def _timed_get(base_url: str, path: str, headers: dict, params: dict | None = None) -> float:
    started = time.perf_counter()
    _client(base_url).get(path, headers=headers, params=params)
    return time.perf_counter() - started


def _bulk_fill(base_url: str, tokens: list[str], total: int, workers: int) -> tuple[int, list[float]]:
    """多线程不限速下单，每满一千单报一次分段吞吐。"""
    done = 0
    lock = threading.Lock()
    latencies: list[float] = []
    errors = 0
    lat_lock = threading.Lock()
    milestone = 1000
    phase_started = time.perf_counter()

    def worker(worker_id: int) -> None:
        nonlocal done, errors, milestone, phase_started
        index = 0
        while True:
            with lock:
                if done >= total:
                    return
                done += 1
                current = done
            token = tokens[(worker_id * 7 + index) % len(tokens)]
            latency, code = _create(base_url, token)
            index += 1
            with lat_lock:
                latencies.append(latency)
                if code != 0:
                    errors += 1
            if current % milestone == 0 or current == total:
                now = time.perf_counter()
                with lock:
                    if current % milestone == 0:
                        print(
                            f"    {current:>6} 单，本段用时 {now - phase_started:5.1f} s，"
                            f"约 {milestone / (now - phase_started):5.1f} 单/秒"
                        )
                        milestone += 1000
                        phase_started = now

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(worker, range(workers)))
    return errors, latencies


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--total", type=int, default=10000)
    parser.add_argument("--workers", type=int, default=16, help="灌库并发数")
    parser.add_argument("--target-rps", type=int, default=100, help="混合阶段的目标下单速率")
    parser.add_argument("--mixed-seconds", type=int, default=20)
    parser.add_argument("--readers", type=int, default=200, help="混合阶段同时盯列表的学生数")
    parser.add_argument("--skip-fill", action="store_true", help="不清库不灌数据，直接测读写")
    args = parser.parse_args()

    db_name = settings.db_name
    if not db_name.endswith("_stress"):
        print(f"目标库 {db_name} 不像压力测试库（要求以 _stress 结尾），拒绝清库")
        return 2

    print(f"目标服务：{args.base_url}    目标库：{db_name}")

    url = settings.database_url
    root = create_engine(make_url(url).set(database=None), pool_pre_ping=True)
    with root.begin() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    root.dispose()
    engine = create_engine(url, pool_pre_ping=True)
    if not args.skip_fill:
        with engine.begin() as conn:
            conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
            for table in (
                "orders", "print_logs", "payment_logs", "notifications",
                "ai_reports", "queue_sessions", "order_daily_seq", "users",
            ):
                try:
                    conn.execute(text(f"TRUNCATE TABLE `{table}`"))
                except Exception as exc:
                    print(f"  跳过 {table}：{exc}")
            conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    tokens = [_login(args.base_url, f"extreme-{i:04d}") for i in range(32)]
    admin_token = httpx.post(
        f"{args.base_url}/api/admin/login",
        json={"username": settings.admin_username, "password": settings.admin_password},
        timeout=60,
    ).json()["data"]["token"]
    admin_headers = {"Authorization": f"Bearer {admin_token}"}
    student_headers = {"Authorization": f"Bearer {tokens[0]}"}

    # ---------------- 阶段一：灌一万单 ------------------------------
    print(f"\n阶段一：灌 {args.total} 单（{args.workers} 个并发连接）")
    started = time.perf_counter()
    if args.skip_fill:
        errors, latencies, elapsed = 0, [], 0.0
        print("  按参数跳过灌数据")
    else:
        errors, latencies = _bulk_fill(args.base_url, tokens, args.total, args.workers)
        elapsed = time.perf_counter() - started
    if elapsed > 0:
        print(
            f"  完成 {args.total} 单，用时 {elapsed:.1f} s，全程均速 "
            f"{args.total / elapsed:.1f} 单/秒，失败 {errors} 单"
        )
    _report("下单耗时（全程）", latencies)

    with engine.connect() as conn:
        orders = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar_one()
        seq = conn.execute(text("SELECT last_seq FROM order_daily_seq")).scalar_one()
        logs = conn.execute(text("SELECT COUNT(*) FROM print_logs")).scalar_one()
        notes = conn.execute(text("SELECT COUNT(*) FROM notifications")).scalar_one()
    print(f"  库里：订单 {orders}，发号器 {seq}，操作日志 {logs}，通知 {notes}")

    # ---------------- 阶段二：万人排队时读数 --------------------------
    # 待支付不算排队：把已下单的整批置成「排队中」，才是「一万人在排队」的真实状态
    with engine.begin() as conn:
        conn.execute(
            text("UPDATE orders SET status = 'queuing', paid = 1 WHERE status = 'pending_pay'")
        )
        queued = conn.execute(
            text("SELECT COUNT(*) FROM orders WHERE status = 'queuing'")
        ).scalar_one()
    print(f"\n阶段二：{queued} 单排队时的读取性能")
    queue_first, queue_deep, search, student_list, detail = [], [], [], [], []
    for _ in range(10):
        queue_first.append(
            _timed_get(args.base_url, "/api/admin/queue", admin_headers, {"page_size": 50})
        )
        queue_deep.append(
            _timed_get(
                args.base_url, "/api/admin/queue", admin_headers,
                {"page_size": 50, "page": max(1, orders // 50)},
            )
        )
        search.append(
            _timed_get(args.base_url, "/api/admin/queue", admin_headers, {"keyword": "extreme"})
        )
        student_list.append(_timed_get(args.base_url, "/api/order/list", student_headers))
        detail.append(_timed_get(args.base_url, "/api/order/1", student_headers))
    _report("老师端队列首页", queue_first)
    _report("老师端队列末页", queue_deep)
    _report("老师端关键词搜索", search)
    _report("学生订单列表", student_list)
    _report("订单详情", detail)

    # ---------------- 阶段三：写一百单每秒 + 两百人盯列表 ----------------
    if args.mixed_seconds <= 0:
        print("\n阶段三：按参数跳过混合场景")
        problems = []
        if errors:
            problems.append(f"下单失败 {errors} 单")
        if _percentile(queue_first, 0.95) > 1.0:
            problems.append("老师端队列首页 p95 超过 1 秒")
        if _percentile(student_list, 0.95) > 1.0:
            problems.append("学生订单列表 p95 超过 1 秒")
        if problems:
            print(f"结论：有 {len(problems)} 项不达标")
            for item in problems:
                print(f"  - {item}")
            return 1
        print("结论：读取未跌破阈值")
        return 0

    print(
        f"\n阶段三：每秒 {args.target_rps} 单下单，同时 {args.readers} 个学生盯列表，"
        f"持续 {args.mixed_seconds} s"
    )
    stop = threading.Event()
    read_latencies: list[float] = []
    read_errors = [0]
    read_lock = threading.Lock()

    def reader() -> None:
        while not stop.is_set():
            started_at = time.perf_counter()
            try:
                _client(args.base_url).get("/api/order/list", headers=student_headers)
            except Exception:
                with read_lock:
                    read_errors[0] += 1
                continue
            with read_lock:
                read_latencies.append(time.perf_counter() - started_at)

    reader_pool = ThreadPoolExecutor(max_workers=args.readers)
    for _ in range(args.readers):
        reader_pool.submit(reader)

    write_latencies: list[float] = []
    write_errors = 0
    scheduled = args.target_rps * args.mixed_seconds
    with ThreadPoolExecutor(max_workers=args.workers) as writer_pool:
        mixed_started = time.perf_counter()
        futures = []
        for i in range(scheduled):
            due = mixed_started + i / args.target_rps
            sleep_for = due - time.perf_counter()
            if sleep_for > 0:
                time.sleep(sleep_for)
            futures.append(writer_pool.submit(_create, args.base_url, tokens[i % len(tokens)]))
        for future in futures:
            latency, code = future.result()
            write_latencies.append(latency)
            if code != 0:
                write_errors += 1
        mixed_elapsed = time.perf_counter() - mixed_started

    stop.set()
    reader_pool.shutdown(wait=True)

    print(f"  实际下发 {scheduled} 单，用时 {mixed_elapsed:.1f} s，写入失败 {write_errors}")
    print(f"  写入速率 {scheduled / mixed_elapsed:.1f} 单/秒")
    _report("混合阶段下的下单耗时", write_latencies)
    _report("混合阶段下的学生列表", read_latencies)
    print(f"  学生列表请求失败 {read_errors[0]} 次")

    with engine.connect() as conn:
        final_orders = conn.execute(text("SELECT COUNT(*) FROM orders")).scalar_one()
    print(f"\n最终库里订单 {final_orders} 单")

    problems = []
    if errors or write_errors:
        problems.append(f"下单失败 {errors + write_errors} 单")
    if _percentile(queue_first, 0.95) > 1.0:
        problems.append("老师端队列首页 p95 超过 1 秒")
    if _percentile(student_list, 0.95) > 1.0:
        problems.append("学生订单列表 p95 超过 1 秒")
    if _percentile(write_latencies, 0.95) > 2.0:
        problems.append("混合阶段下单 p95 超过 2 秒")
    if problems:
        print(f"结论：有 {len(problems)} 项不达标")
        for item in problems:
            print(f"  - {item}")
        return 1
    print("结论：一万单规模下读写都没跌破阈值")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
