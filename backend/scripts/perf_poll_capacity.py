"""复测一万单排队下的单进程读取容量。

判定口径（本轮的硬指标）：一万单排队、单进程、并发 16 时 QPS 不低于 200、
各档 p95 低于 1000 ms。脚本按这个口径判通过与否。

背景口径：一万人同时在线、每人每 30 秒拉一次「我的订单」列表 = 333.3 次/秒。
那是「整套服务（多实例 / 多进程）要扛住的峰值」，不是单个进程的目标；
把它记在结果里只作参照，不作判定线。

做法：用真实下单接口灌 N 单并置成「排队中」，再按不同并发持续读订单列表，
量出每秒成功次数、延迟分位，以及（给了 --server-pid 时）每请求进程 CPU。

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
from concurrent.futures import ThreadPoolExecutor
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
from scripts.stress_extreme import _login, _percentile  # noqa: E402

# 本轮判定线：一万单排队、单进程、并发 16 时不低于 200 次/秒，p95 低于 1000 ms
TARGET_QPS = 200.0
TARGET_P95_MS = 1000.0

# 背景口径：一万人 / 30 秒；学生端默认轮询间隔见 student-app/src/config/index.js
# 10000 / 30 = 333.3 次/秒。这是整套服务要扛的峰值，单进程目标按 200 算，留出余量。
ONLINE_USERS = 10000
POLL_SECONDS = 30.0
DEMAND_QPS = ONLINE_USERS / POLL_SECONDS


def _proc_cpu_seconds(pids: list[int]) -> float | None:
    """读 /proc/<pid>/stat 的 utime+stime（单位秒）并求和，量进程真实吃的 CPU。

    多 worker 诊断时把各个 worker 的 pid 都传进来，否则只量到一个 worker 会得出 0。
    """
    ticks = 0
    read_any = False
    for pid in pids:
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().split()
        except OSError:
            continue
        read_any = True
        ticks += int(fields[13]) + int(fields[14])
    if not read_any:
        return None
    return ticks / os.sysconf("SC_CLK_TCK")


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
    所以压测也必须走一次真实上传。下单还校验文件归属，所以每个压测用户各传一份，
    不共用别人上传的文件。
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


def _fill_orders(
    base_url: str, file_keys: list[str], tokens: list[str], total: int, workers: int
) -> tuple[dict[int, int], float]:
    """多线程不限速下单，返回 ({错误码: 次数}, 用时)。

    每个 token 用自己的文件下单：下单校验文件归属，拿别人的 key 会被 40404 拒掉。
    """
    codes: dict[int, int] = {}
    lock = threading.Lock()
    done = 0
    milestone = 1000
    phase_started = time.perf_counter()

    def worker(worker_id: int) -> None:
        nonlocal done, milestone, phase_started
        local: dict[int, int] = {}
        index = 0
        while True:
            with lock:
                if done >= total:
                    break
                done += 1
                current = done
            slot = (worker_id * 7 + index) % len(tokens)
            index += 1
            payload = {
                "file_key": file_keys[slot],
                "file_name": "perf-probe.pdf",
                "page_count": 1,
                "print_side": 2,
                "color_mode": 0,
                "copies": 1,
                "paper_size": "A4",
            }
            body = _client(base_url).post(
                "/api/order/create",
                headers={"Authorization": f"Bearer {tokens[slot]}"},
                json=payload,
            ).json()
            code = int(body.get("code", -1))
            local[code] = local.get(code, 0) + 1
            if current % milestone == 0:
                now = time.perf_counter()
                with lock:
                    print(
                        f"    {current:>6} 单，本段用时 {now - phase_started:5.1f} s，"
                        f"约 {milestone / (now - phase_started):5.1f} 单/秒"
                    )
                    milestone += 1000
                    phase_started = now
        with lock:
            for code, count in local.items():
                codes[code] = codes.get(code, 0) + count

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(worker, range(workers)))
    print(f"    {total:>6} 单，全部提交完毕")
    return codes, time.perf_counter() - started


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
    parser.add_argument(
        "--server-processes",
        type=int,
        default=1,
        help="被测服务的进程数：1 表示单进程（启动时不加 --workers），大于是诊断对照",
    )
    parser.add_argument(
        "--server-pid",
        type=str,
        default=None,
        help="被测服务进程的 pid，多个用逗号分隔；给了就额外量每请求 CPU",
    )
    parser.add_argument(
        "--note", default="", help="写进结果文件的口径补充（逐条列，用 | 分隔）"
    )
    parser.add_argument("--output", type=Path, default=None)
    args = parser.parse_args()

    db_name = settings.db_name
    if not db_name.endswith("_stress"):
        print(f"目标库 {db_name} 不像压力测试库（要求以 _stress 结尾），拒绝清库")
        return 2

    server_pids = [
        int(item) for item in (args.server_pid or "").replace(" ", "").split(",") if item
    ]
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

    fill_note = "跳过灌数据，复用库里已有订单"
    if args.skip_fill:
        print("按参数跳过灌数据，复用库里已有订单")
    else:
        print(f"\n阶段一：灌 {args.total} 单（{args.workers} 个并发连接）")
        file_keys = [_upload_probe_pdf(args.base_url, token) for token in tokens]
        print(f"  压测文件：{len(file_keys)} 份，每个压测用户各一份")
        codes, elapsed = _fill_orders(
            args.base_url, file_keys, tokens, args.total, args.workers
        )
        print(
            f"  完成 {args.total} 单，用时 {elapsed:.1f} s，均速 "
            f"{args.total / elapsed:.1f} 单/秒，错误码分布 "
            f"{{{', '.join(f'{code}: {count}' for code, count in sorted(codes.items()))}}}"
        )
        fill_note = (
            f"灌入 {args.total} 单，用时 {elapsed:.1f} 秒，均速 {args.total / elapsed:.1f} 单/秒"
        )
        if codes.get(0, 0) != args.total:
            print("  有下单失败，读取容量结果不可信；先修下单链路再复测")
            return 1

    with engine.begin() as conn:
        conn.execute(
            sql_text("UPDATE orders SET status = 'queuing', paid = 1 WHERE status = 'pending_pay'")
        )
        queued = conn.execute(
            sql_text("SELECT COUNT(*) FROM orders WHERE status = 'queuing'")
        ).scalar_one()
    print(f"\n阶段二：{queued} 单排队时的读取容量（单进程即 {TARGET_QPS:.0f} 次/秒的目标）")

    results = []
    for index, level in enumerate(levels):
        if index:
            time.sleep(1.0)
        print(f"  并发 {level:>3}：", end="", flush=True)
        cpu_before = _proc_cpu_seconds(server_pids) if server_pids else None
        result = _measure_level(args.base_url, tokens[0], level, args.seconds)
        cpu_after = _proc_cpu_seconds(server_pids) if server_pids else None
        if cpu_before is not None and cpu_after is not None and result["success"]:
            result["cpu_ms_per_request"] = (cpu_after - cpu_before) * 1000 / result["success"]
            result["cpu_cores"] = (cpu_after - cpu_before) / result["seconds"]
        results.append(result)
        extra = ""
        if "cpu_ms_per_request" in result:
            extra = (
                f"，每请求 CPU {result['cpu_ms_per_request']:.2f} ms"
                f"（{result['cpu_cores']:.2f} 核）"
            )
        print(
            f"{result['qps']:.1f} 次/秒，成功 {result['success']}，失败 {result['errors']}，"
            f"p50 {result['p50']:.0f} ms，p95 {result['p95']:.0f} ms，"
            f"p99 {result['p99']:.0f} ms{extra}"
        )
        if result["error_sample"]:
            print(f"    失败样本：{result['error_sample']}")

    best = max(results, key=lambda item: item["qps"])
    main = next((item for item in results if item["concurrency"] == 16), best)
    headroom = (main["qps"] / TARGET_QPS - 1.0) * 100
    demand_headroom = (best["qps"] / DEMAND_QPS - 1.0) * 100 if DEMAND_QPS else 0.0
    passed = (
        main["qps"] >= TARGET_QPS
        and all(item["p95"] < TARGET_P95_MS for item in results)
    )
    verdict = "通过" if passed else "不通过"

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output = args.output or (
        BACKEND_DIR / "scripts" / "perf_results" / f"poll_capacity-{stamp}.md"
    )
    output.parent.mkdir(parents=True, exist_ok=True)

    has_cpu = any("cpu_ms_per_request" in item for item in results)
    header = "| 并发 | 测量时长 | 成功次数 | 失败次数 | 实测 QPS | p50 | p95 | p99 | 最大 |"
    rule = "|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|"
    if has_cpu:
        header += " 每请求 CPU | 折合核数 |"
        rule += ":---:|:---:|"
    table = [header, rule]
    for item in results:
        row = (
            f"| {item['concurrency']} | {item['seconds']:.1f} s | {item['success']} | "
            f"{item['errors']} | {item['qps']:.1f} | {item['p50']:.0f} ms | "
            f"{item['p95']:.0f} ms | {item['p99']:.0f} ms | {item['max']:.0f} ms |"
        )
        if has_cpu:
            if "cpu_ms_per_request" in item:
                row += f" {item['cpu_ms_per_request']:.2f} ms | {item['cpu_cores']:.2f} |"
            else:
                row += " — | — |"
        table.append(row)

    processes_note = (
        "单进程（启动时不加 --workers）"
        if args.server_processes == 1
        else f"{args.server_processes} 个 worker（诊断对照，不能当单进程交付）"
    )
    lines = [
        f"# 一万单排队下的读取容量（{datetime.now():%Y-%m-%d %H:%M}）",
        "",
        f"- 结论：**{verdict}**",
        f"- 判定线（本轮硬指标）：一万单排队、单进程、并发 16 时 QPS 不低于 "
        f"{TARGET_QPS:.0f}，且各档 p95 低于 {TARGET_P95_MS:.0f} ms",
        f"- 实测：并发 16 时 **{main['qps']:.1f} 次/秒**，p95 {main['p95']:.0f} ms，"
        f"对 {TARGET_QPS:.0f} 的余量 {headroom:+.1f}%",
        f"- 各档峰值：{best['qps']:.1f} 次/秒（并发 {best['concurrency']}）",
        f"- 被测服务进程数：**{processes_note}**",
        f"- 背景口径（不作判定线）：{ONLINE_USERS} 人同时在线、每 {POLL_SECONDS:g} 秒轮询一次"
        f" = {DEMAND_QPS:.1f} 次/秒，峰值 {best['qps']:.1f} 次/秒对它的余量 "
        f"{demand_headroom:+.1f}%",
        "- 读取容量随在途排队订单数变化；本结果对应上面的排队规模，换规模要重跑。",
        "",
        "## 环境",
        "",
        f"- 服务地址：`{args.base_url}`",
        f"- 数据库：`{db_name}`，排队订单 {queued} 单",
        f"- 准备：{fill_note}",
        f"- 代码提交：`{_git_commit()}`",
        f"- 系统：{platform.platform()}，CPU {os.cpu_count()} 核",
        f"- Python：{platform.python_version()}",
        *([f"- 采样进程：{', '.join(str(pid) for pid in server_pids)}"]
          if server_pids else []),
        f"- 连接池：size {settings.db_pool_size}，overflow {settings.db_max_overflow}，"
        f"timeout {settings.db_pool_timeout} 秒",
        "",
        "## 读数",
        "",
        *table,
        "",
        *(
            ["## 补充说明", "", *[f"- {item.strip()}" for item in args.note.split("|") if item.strip()], ""]
            if args.note.strip()
            else []
        ),
        "## 口径",
        "",
        "- 每个档位用固定并发持续打 `/api/order/list`，按成功响应数除以实际测量时长算 QPS。",
        "- 失败计入失败次数，不计入 QPS；失败样本会打印在终端。",
        "- 每请求 CPU = 被测进程 `/proc/<pid>/stat` 的 utime+stime 增量除以成功次数；"
        "折合核数 = CPU 增量除以墙钟时长，单进程超过 1.0 核说明这一档已经打满一个核。",
        "- 一万人每 30 秒一次摊平成均匀流量是「整套服务」的容量口径；真实流量是脉冲式，"
        "瞬时峰值会更高，需要多实例时另算。单进程目标是 200 次/秒，留出多实例叠加的余量。",
        "",
    ]
    text = "\n".join(lines)
    output.write_text(text + "\n", encoding="utf-8")
    print(
        f"\n结论：{verdict}，并发 16 时 {main['qps']:.1f} 次/秒（目标 {TARGET_QPS:.0f}），"
        f"p95 {main['p95']:.0f} ms（上限 {TARGET_P95_MS:.0f}），进程数 {args.server_processes}"
    )
    print(f"结果已写入：{output}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
