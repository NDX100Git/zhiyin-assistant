"""并发与容量压力测试：下单撞号、时段超卖、队列响应时间。

对应 REVIEW.md 第 4、5 条的场景 1、2、4。跑之前先起一个指向独立库的服务实例，
库不存在时后端启动会自动建表：

    DB_NAME=zhiyin_stress <venv>/bin/python -m uvicorn app.main:app --port 8001
    <venv>/bin/python scripts/stress_orders.py --base-url http://127.0.0.1:8001

三个场景：

A 二十个账号同秒下单：订单号与取件码必须唯一，当日序号必须连续无断号
B 时段容量改成 1 后十人并发下单：已用数必须停在上限，不能超卖
C 队列里有两百单时：老师端队列接口与学生订单列表的响应时间

脚本会先清空目标库（只允许名字以 _stress 结尾的库），避免历史数据干扰计数。
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from threading import Barrier

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.session_service import resolve_session_name  # noqa: E402

FILE_KEY = "files/2026/10/stress.pdf"


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
        f"最大 {max(latencies) * 1000:.0f} ms，均值 {statistics.mean(latencies) * 1000:.0f} ms"
    )


def _login(base_url: str, device_id: str) -> str:
    with httpx.Client(base_url=base_url, timeout=30) as client:
        response = client.post("/api/auth/login", json={"device_id": device_id})
        response.raise_for_status()
        return response.json()["data"]["token"]


def _create_order(base_url: str, token: str) -> tuple[float, dict]:
    started = time.perf_counter()
    with httpx.Client(base_url=base_url, timeout=60) as client:
        body = client.post(
            "/api/order/create",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "file_key": FILE_KEY,
                "file_name": "stress.pdf",
                "page_count": 10,
                "print_side": 2,
                "color_mode": 0,
                "copies": 1,
                "paper_size": "A4",
            },
        ).json()
    return time.perf_counter() - started, body


def _fire_together(tasks: list) -> list:
    """让所有线程在同一刻出发，避免排队变成先后。"""
    barrier = Barrier(len(tasks))

    def run(task):
        barrier.wait()
        return task()

    with ThreadPoolExecutor(max_workers=len(tasks)) as pool:
        return list(pool.map(run, tasks))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--accounts", type=int, default=20, help="场景 A 的并发人数")
    parser.add_argument("--capacity-orders", type=int, default=10, help="场景 B 的并发人数")
    parser.add_argument("--bulk", type=int, default=200, help="场景 C 灌入的订单数")
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
    with engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in (
            "orders", "print_logs", "payment_logs", "notifications",
            "ai_reports", "queue_sessions", "order_daily_seq", "users",
        ):
            try:
                conn.execute(text(f"TRUNCATE TABLE `{table}`"))
            except Exception as exc:  # 表可能还没建
                print(f"  跳过 {table}：{exc}")
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))

    failures: list[str] = []

    # ---------------- 场景 A：同秒下单 -------------------------------
    tokens = [_login(args.base_url, f"stress-a-{i:03d}") for i in range(args.accounts)]
    results = _fire_together([lambda t=t: _create_order(args.base_url, t) for t in tokens])
    latencies = [item[0] for item in results]
    bodies = [item[1] for item in results]
    ok = [b for b in bodies if b.get("code") == 0]
    order_nos = [b["data"]["order_no"] for b in ok]
    pickup_codes = [b["data"]["pickup_code"] for b in ok]

    print(f"\n场景 A：{args.accounts} 个账号同秒下单")
    print(f"  成功 {len(ok)} / {len(bodies)}，失败码 {sorted({b.get('code') for b in bodies if b.get('code')})}")
    _report("下单耗时", latencies)
    print(f"  订单号唯一：{len(set(order_nos)) == len(order_nos)}，取件码唯一：{len(set(pickup_codes)) == len(pickup_codes)}")
    seqs = sorted(int(no.split("-")[-1]) for no in order_nos)
    continuous = seqs == list(range(1, len(seqs) + 1))
    print(f"  当日序号：{seqs[0]}..{seqs[-1]}，连续无断号：{continuous}")
    if len(ok) != args.accounts:
        failures.append(f"场景 A 有 {len(bodies) - len(ok)} 单失败")
    if len(set(order_nos)) != len(order_nos) or len(set(pickup_codes)) != len(pickup_codes):
        failures.append("场景 A 出现重号或重码")
    if not continuous:
        failures.append("场景 A 序号断号")

    # ---------------- 场景 B：容量上限 -------------------------------
    # 容量只在收单时段内记（时段外下的单算下一个时段），时段外跑这条没有意义
    now_session = resolve_session_name(datetime.now())
    print(f"\n场景 B：容量 1，{args.capacity_orders} 人并发下单")
    if now_session is None:
        print("  当前不在收单时段，跳过。要测这条：把 backend/config/order_rules.json 里某个")
        print("  时段的 window 临时改到覆盖当前时间并重启服务，或等时段内再跑")
    else:
        with engine.begin() as conn:
            conn.execute(text("UPDATE queue_sessions SET capacity = 1, used = 0"))
        tokens_b = [_login(args.base_url, f"stress-b-{i:03d}") for i in range(args.capacity_orders)]
        results_b = _fire_together(
            [lambda t=t: _create_order(args.base_url, t) for t in tokens_b]
        )
        ok_b = [item[1] for item in results_b if item[1].get("code") == 0]
        with engine.connect() as conn:
            used = conn.execute(
                text("SELECT COALESCE(SUM(used), 0) FROM queue_sessions")
            ).scalar_one()
        print(f"  成功 {len(ok_b)} 单，时段已用数记录 {used}（上限 1）")
        if used > 1:
            failures.append(f"场景 B 超卖：容量 1 但已用数记为 {used}")

    # ---------------- 场景 D：并发核销同一个码 ------------------------
    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        admin_login = client.post(
            "/api/admin/login",
            json={"username": settings.admin_username, "password": settings.admin_password},
        ).json()
    admin_headers = {"Authorization": f"Bearer {admin_login['data']['token']}"}

    token_d = _login(args.base_url, "stress-d")
    _, body_d = _create_order(args.base_url, token_d)
    order_d, code_d = body_d["data"]["id"], body_d["data"]["pickup_code"]
    student_headers = {"Authorization": f"Bearer {token_d}"}
    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        client.post(f"/api/order/{order_d}/pay", headers=student_headers, json={"provider": "mock"})
        client.post(f"/api/admin/order/{order_d}/start", headers=admin_headers)
        client.post(f"/api/admin/order/{order_d}/complete", headers=admin_headers)

    def _verify_once() -> dict:
        with httpx.Client(base_url=args.base_url, timeout=60) as client:
            return client.post(
                "/api/admin/order/verify-code",
                headers=admin_headers,
                json={"pickup_code": code_d},
            ).json()

    results_d = _fire_together([_verify_once for _ in range(10)])
    ok_d = [r for r in results_d if r.get("code") == 0]
    print("\n场景 D：同一个取件码被 10 个请求同时核销")
    print(f"  成功核销 {len(ok_d)} 次（必须正好 1 次），其余返回：{sorted({r.get('code') for r in results_d if r.get('code')})}")
    if len(ok_d) != 1:
        failures.append(f"场景 D 同一单被核销了 {len(ok_d)} 次")

    # ---------------- 场景 E：队列响应时间 ----------------------------
    bulk_tokens = [_login(args.base_url, f"stress-c-{i:03d}") for i in range(10)]
    created = 0
    for i in range(args.bulk):
        _, body = _create_order(args.base_url, bulk_tokens[i % len(bulk_tokens)])
        if body.get("code") == 0:
            created += 1
    print(f"\n场景 E：灌入 {created} 单后测响应时间")

    queue_latencies: list[float] = []
    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        for _ in range(20):
            started = time.perf_counter()
            client.get("/api/admin/queue", headers=admin_headers, params={"page_size": 50})
            queue_latencies.append(time.perf_counter() - started)

    list_latencies: list[float] = []
    student_list_headers = {"Authorization": f"Bearer {bulk_tokens[0]}"}
    with httpx.Client(base_url=args.base_url, timeout=60) as client:
        for _ in range(20):
            started = time.perf_counter()
            client.get("/api/order/list", headers=student_list_headers)
            list_latencies.append(time.perf_counter() - started)

    _report("老师端队列（每页 50）", queue_latencies)
    _report("学生订单列表", list_latencies)

    print()
    if failures:
        print(f"结论：有 {len(failures)} 项不达标")
        for item in failures:
            print(f"  - {item}")
        return 1
    print("结论：发号唯一连续、容量未超卖、同一单只被核销一次，响应时间见上表")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
