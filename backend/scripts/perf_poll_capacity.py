"""复测 30 秒轮询下的单实例读取容量。

口径：一万人同时在线，每人每 30 秒拉一次「我的订单」列表，平均约 333 次/秒。
做法：用真实下单接口灌 N 单并置成「排队中」，再按不同并发持续读订单列表，
量出单实例的每秒成功次数与延迟分位，和 333 次/秒对比。

脚本只肯清名字以 `_stress` 结尾的库，跑法见 RUNBOOK 第 13 节：

    DB_NAME=zhiyin_stress <venv>/bin/python scripts/perf_poll_capacity.py \\
        --base-url http://127.0.0.1:8001 --total 10000

每次运行写一份带时间戳的 Markdown 到 scripts/perf_results/，不覆盖历史结果。
压测数据与开发库、测试库隔离，不混在一起。
"""

from __future__ import annotations

import argparse
import os
import platform
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

import httpx  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy import text as sql_text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402

from app.config import settings  # noqa: E402
from scripts import stress_extreme  # noqa: E402
from scripts.stress_extreme import _bulk_fill, _login, _percentile  # noqa: E402

# 一万人 / 30 秒；学生端默认轮询间隔见 student-app/src/config/index.js
ONLINE_USERS = 10000
POLL_SECONDS = 30.0
DEMAND_QPS = ONLINE_USERS / POLL_SECONDS


def _client(base_url: str) -> httpx.Client:
    client = getattr(threading.current_thread(), "_perf_client", None)
    if client is None:
        client = httpx.Client(base_url=base_url, timeout=60)
        threading.current_thread()._perf_client = client
    return client


def _measure_level(
    base_url: str, token: str, concurrency: int, seconds: float
) -> dict:
    """固定并发持续打订单列表，返回成功次数、QPS 与延迟分位。"""
    latencies: list[float] = []
    errors: list[str] = []
    lock = threading.Lock()
    stop_at = time.perf_counter() + seconds
    barrier = threading.Barrier(concurrency)

    def worker() -> None:
        headers = {"Authorization": f"Bearer {token}"}
        local: list[float] = []
        local_errors: list[str] = []
        try:
            barrier.wait(timeout=30)
        except threading.BrokenBarrierError:
            return
        while time.perf_counter() < stop_at:
            started = time.perf_counter()
            try:
                response = _client(base_url).get("/api/order/list", headers=headers)
                body = response.json()
            except Exception as exc:  # 网络或解析失败都算一次失败
                local_errors.append(type(exc).__name__)
                continue
            elapsed = time.perf_counter() - started
            if body.get("code") == 0:
                local.append(elapsed)
            else:
                local_errors.append(str(body.get("code")))
        with lock:
            latencies.extend(local)
            errors.extend(local_errors)

    threads = [threading.Thread(target=worker) for _ in range(concurrency)]
    started = time.perf_counter()
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=seconds + 30)
    elapsed = time.perf_counter() - started

    return {
        "concurrency": concurrency,
        "seconds": elapsed,
        "success": len(latencies),
        "errors": len(errors),
        "error_sample": sorted(set(errors))[:5],
        "qps": len(latencies) / elapsed if elapsed > 0 else 0.0,
        "p50": _percentile(latencies, 0.5) * 1000,
        "p95": _percentile(latencies, 0.95) * 1000,
        "p99": _percentile(latencies, 0.99) * 1000,
        "max": (max(latencies) * 1000) if latencies else 0.0,
    }


def _upload_probe_pdf(base_url: str, token: str) -> str:
    """上传一份真实 PDF，压测订单引用它。

    下单会校验对象存储里文件还在、且没超过下单宽限期；假 key 会被 40053 拒掉，
    所以压测也必须走一次真实上传。所有压测订单共用这一份文件，只为量读取容量。
    """
    import fitz

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((72, 72), "perf probe", fontsize=12)
        data = document.tobytes()
    response = httpx.post(
        f"{base_url}/api/file/upload",
        headers={"Authorization": f"Bearer {token}"},
        files={"file": ("perf-probe.pdf", data, "application/pdf")},
        data={"file_name": "perf-probe.pdf"},
        timeout=60,
    )
    body = response.json()
    if body.get("code") != 0:
        raise SystemExit(f"上传压测文件失败：{body.get('code')} {body.get('message')}")
    return body["data"]["file_key"]


def _git_commit() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(BACKEND_DIR.parent), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8001")
    parser.add_argument("--total", type=int, default=10000, help="灌入订单数")
    parser.add_argument("--workers", type=int, default=16, help="灌库并发数")
    parser.add_argument("--seconds", type=float, default=10.0, help="每个并发档位测多久")
    parser.add_argument("--levels", default="16,32,64", help="并发档位，逗号分隔")
    parser.add_argument("--skip-fill", action="store_true", help="不清库不灌数据，直接测读")
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    db_name = settings.db_name
    if not db_name.endswith("_stress"):
        print(f"目标库 {db_name} 不像压力测试库（要求以 _stress 结尾），拒绝清库")
        return 2

    levels = [int(item) for item in args.levels.split(",") if item.strip()]
    if not levels or any(level < 1 for level in levels):
        print("并发档位必须是正整数")
        return 2

    print(f"目标服务：{args.base_url}")
    print(f"目标库：{db_name}，灌单 {args.total}，并发档位 {levels}，每档 {args.seconds:g} 秒")

    url = settings.database_url
    root = create_engine(make_url(url).set(database=None), pool_pre_ping=True)
    with root.begin() as conn:
        conn.execute(
            sql_text(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
    root.dispose()
    engine = create_engine(url, pool_pre_ping=True)

    if not args.skip_fill:
        with engine.begin() as conn:
            conn.execute(sql_text("SET FOREIGN_KEY_CHECKS=0"))
            for table in (
                "orders",
                "order_files",
                "order_file_changes",
                "order_file_versions",
                "uploaded_files",
                "print_logs",
                "payment_logs",
                "notifications",
                "notices",
                "ai_reports",
                "queue_sessions",
                "order_daily_seq",
                "users",
            ):
                try:
                    conn.execute(sql_text(f"TRUNCATE TABLE `{table}`"))
                except Exception as exc:  # 表可能还没被迁移出来
                    print(f"  跳过 {table}：{exc}")
            conn.execute(sql_text("SET FOREIGN_KEY_CHECKS=1"))

    tokens = [_login(args.base_url, f"poll-{index:04d}") for index in range(max(levels) + 2)]

    if args.skip_fill:
        print("按参数跳过灌数据，复用库里已有订单")
    else:
        file_key = _upload_probe_pdf(args.base_url, tokens[0])
        stress_extreme.ORDER_BODY = {
            **stress_extreme.ORDER_BODY,
            "file_key": file_key,
            "file_name": "perf-probe.pdf",
            "page_count": 1,
        }
        print(f"  压测文件：{file_key}")
        print(f"\n阶段一：灌 {args.total} 单（{args.workers} 个并发连接）")
        started = time.perf_counter()
        errors, _latencies = _bulk_fill(args.base_url, tokens, args.total, args.workers)
        elapsed = time.perf_counter() - started
        print(
            f"  完成 {args.total} 单，用时 {elapsed:.1f} s，均速 "
            f"{args.total / elapsed:.1f} 单/秒，失败 {errors} 单"
        )
        if errors:
            print("  有下单失败，读取容量结果不可信；先修下单链路再复测")
            return 1

    with engine.begin() as conn:
        conn.execute(
            sql_text("UPDATE orders SET status = 'queuing', paid = 1 WHERE status = 'pending_pay'")
        )
        queued = conn.execute(
            sql_text("SELECT COUNT(*) FROM orders WHERE status = 'queuing'")
        ).scalar_one()
    print(f"\n阶段二：{queued} 单排队时的读取容量（模拟一万人每 30 秒轮询）")

    results = []
    for index, level in enumerate(levels):
        if index:
            time.sleep(1.0)
        print(f"  并发 {level:>3}：", end="", flush=True)
        result = _measure_level(args.base_url, tokens[0], level, args.seconds)
        results.append(result)
        print(
            f"{result['qps']:.1f} 次/秒，成功 {result['success']}，失败 {result['errors']}，"
            f"p50 {result['p50']:.0f} ms，p95 {result['p95']:.0f} ms，"
            f"p99 {result['p99']:.0f} ms"
        )
        if result["error_sample"]:
            print(f"    失败样本：{result['error_sample']}")

    best = max(results, key=lambda item: item["qps"])
    headroom = (best["qps"] / DEMAND_QPS - 1.0) * 100 if DEMAND_QPS else 0.0
    passed = best["qps"] >= DEMAND_QPS and all(item["p95"] < 1000 for item in results)
    verdict = "通过" if passed else "不通过"

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output or (
        BACKEND_DIR / "scripts" / "perf_results" / f"poll_capacity-{stamp}.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    table = [
        "| 并发 | 测量时长 | 成功次数 | 失败次数 | 实测 QPS | p50 | p95 | p99 | 最大 |",
        "|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|",
    ]
    for item in results:
        table.append(
            f"| {item['concurrency']} | {item['seconds']:.1f} s | {item['success']} | "
            f"{item['errors']} | {item['qps']:.1f} | {item['p50']:.0f} ms | "
            f"{item['p95']:.0f} ms | {item['p99']:.0f} ms | {item['max']:.0f} ms |"
        )

    lines = [
        f"# 30 秒轮询下的单实例读取容量（{datetime.now():%Y-%m-%d %H:%M}）",
        "",
        f"- 结论：**{verdict}**",
        f"- 需求口径：{ONLINE_USERS} 人同时在线，每 {POLL_SECONDS:g} 秒轮询一次，"
        f"平均 {DEMAND_QPS:.1f} 次/秒",
        f"- 单实例实测峰值：**{best['qps']:.1f} 次/秒**（并发 {best['concurrency']}），"
        f"余量 {headroom:+.1f}%",
        f"- 判定线：QPS 不低于 {DEMAND_QPS:.1f}，且各档 p95 低于 1000 ms",
        "- 读取容量随在途排队订单数变化；本结果对应上面的排队规模，换规模要重跑。",
        "",
        "## 环境",
        "",
        f"- 服务地址：`{args.base_url}`",
        f"- 数据库：`{db_name}`，排队订单 {queued} 单",
        f"- 代码提交：`{_git_commit()}`",
        f"- 系统：{platform.platform()}，CPU {os.cpu_count()} 核",
        f"- Python：{platform.python_version()}",
        f"- 连接池：size {settings.db_pool_size}，overflow {settings.db_max_overflow}，"
        f"timeout {settings.db_pool_timeout} 秒",
        "",
        "## 读数",
        "",
        *table,
        "",
        "## 口径",
        "",
        "- 每个档位用固定并发持续打 `/api/order/list`，按成功响应数除以实际测量时长算 QPS。",
        "- 失败计入失败次数，不计入 QPS；失败样本会打印在终端。",
        "- 把一万人每 30 秒一次摊平成均匀流量，是容量上限的口径；真实流量是脉冲式，"
        "瞬时峰值会更高，需要多实例时另算。",
        "",
    ]
    text = "\n".join(lines)
    output.write_text(text + "\n", encoding="utf-8")
    print(f"\n结论：{verdict}，单实例峰值 {best['qps']:.1f} 次/秒，需求 {DEMAND_QPS:.1f} 次/秒")
    print(f"结果已写入：{output}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
