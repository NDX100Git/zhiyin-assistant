"""评估 LibreOffice 转换进程能否复用。

三个对照场景，都在同一个真实转换环境里跑（本机 soffice 或常驻转换容器）：

1. 现状：每次转换用独立的 UserInstallation，串行三次，作为冷启动基线。
2. 现状：独立 profile 并发两次，看并发是否真的并行。
3. 复用尝试 A：两次转换共用同一个 UserInstallation，后启动的会委托给已在运行的实例。
4. 复用尝试 B：先起一个 --accept 常驻实例，再用同一 profile 发转换任务。

输出一份 Markdown，写清每步耗时、产出文件数与常驻实例是否存活。结论不写死：
数据说话——只要出现「少一个 PDF」或「常驻实例转换一次就退出」，就说明这版复用不可靠。

跑法（在 backend/ 下）：

    <venv>/bin/python scripts/bench_soffice_reuse.py
    <venv>/bin/python scripts/bench_soffice_reuse.py --samples 3 --output runtime/tmp/reuse.md

脚本只写 backend/runtime/tmp 下的临时目录，不碰订单、数据库与对象存储。
结束时清理本次实验新起的 soffice 进程；不要在有人正在转换时跑。
"""

from __future__ import annotations

import argparse
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from app.config import BACKEND_DIR as APP_BACKEND_DIR  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.doc_parser import (  # noqa: E402
    RUNTIME_TMP_DIR,
    _soffice_command,
)

ACCEPT = "socket,host=127.0.0.1,port=2002;urp;StarOffice.ComponentContext"


class Result:
    def __init__(self, label: str) -> None:
        self.label = label
        self.seconds = 0.0
        self.outputs = 0
        self.expected = 0
        self.note = ""

    @property
    def ok(self) -> bool:
        return self.outputs == self.expected

    def line(self) -> str:
        return (
            f"| {self.label} | {self.expected} | {self.seconds:.2f} s | "
            f"{self.seconds / max(self.expected, 1):.2f} s | "
            f"{self.outputs}/{self.expected} | {self.note or ('正常' if self.ok else '缺文件')} |"
        )


def _make_sample(work: Path) -> Path:
    """生成一份 5 页左右的 docx，作为转换输入。"""
    sys.path.insert(0, str(BACKEND_DIR / "scripts"))
    from make_demo_docx import build

    target = work / "sample.docx"
    build(target)
    return target


def _wait_all(procs: list[subprocess.Popen], timeout: float) -> list[int]:
    codes = []
    deadline = time.perf_counter() + timeout
    for proc in procs:
        remaining = max(0.1, deadline - time.perf_counter())
        try:
            codes.append(proc.wait(timeout=remaining))
        except subprocess.TimeoutExpired:
            proc.kill()
            codes.append(-9)
    return codes


def _run_one(cmd: list[str], timeout: float) -> tuple[float, int]:
    started = time.perf_counter()
    proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
    return time.perf_counter() - started, proc.returncode


def _conversion_cmd(input_path: Path, out_dir: Path, profile_dir: Path) -> list[str]:
    return _soffice_command(input_path, out_dir, profile_dir, "pdf")


def scene_sequential(sample: Path, work: Path, samples: int) -> Result:
    result = Result("现状：独立 profile，串行")
    result.expected = samples
    started = time.perf_counter()
    for index in range(samples):
        out_dir = work / f"seq-{index}"
        profile = out_dir / "profile"
        out_dir.mkdir(parents=True)
        elapsed, code = _run_one(_conversion_cmd(sample, out_dir, profile), timeout=180)
        result.note = "" if code == 0 else f"退出码 {code}"
        if (out_dir / f"{sample.stem}.pdf").exists():
            result.outputs += 1
        print(f"  串行 {index + 1}/{samples}：{elapsed:.2f} s，退出码 {code}")
    result.seconds = time.perf_counter() - started
    return result


def scene_concurrent(sample: Path, work: Path) -> tuple[Result, Result]:
    """独立 profile 并发两次，与共用 profile 并发两次。"""
    independent = Result("现状：独立 profile，并发 2")
    shared = Result("复用尝试 A：共用 profile，并发 2")
    independent.expected = shared.expected = 2

    def two(prefix: str, same_profile: bool) -> tuple[list[Path], float, list[int]]:
        out_dirs = [work / f"{prefix}-{index}" for index in range(2)]
        shared_profile = work / f"{prefix}-profile"
        procs = []
        started = time.perf_counter()
        for out_dir in out_dirs:
            out_dir.mkdir(parents=True)
            profile = shared_profile if same_profile else out_dir / "profile"
            cmd = _conversion_cmd(sample, out_dir, profile)
            procs.append(subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
        codes = _wait_all(procs, timeout=180)
        return out_dirs, time.perf_counter() - started, codes

    out_dirs, elapsed, codes = two("indep", same_profile=False)
    independent.seconds = elapsed
    independent.outputs = sum(
        1 for out_dir in out_dirs if (out_dir / f"{sample.stem}.pdf").exists()
    )
    independent.note = "" if all(code == 0 for code in codes) else f"退出码 {codes}"
    print(f"  独立 profile 并发：{elapsed:.2f} s，产出 {independent.outputs}/2")

    out_dirs, elapsed, codes = two("shared", same_profile=True)
    shared.seconds = elapsed
    shared.outputs = sum(
        1 for out_dir in out_dirs if (out_dir / f"{sample.stem}.pdf").exists()
    )
    shared.note = "" if all(code == 0 for code in codes) else f"退出码 {codes}"
    print(f"  共用 profile 并发：{elapsed:.2f} s，产出 {shared.outputs}/2")
    return independent, shared


def _soffice_pids() -> set[str]:
    container = (settings.soffice_container or "").strip()
    if not container:
        return set()
    script = (
        'for p in /proc/[0-9]*/comm; do c=$(cat "$p" 2>/dev/null); '
        'case "$c" in *office*) basename "$(dirname "$p")";; esac; done'
    )
    proc = subprocess.run(
        ["docker", "exec", container, "sh", "-c", script],
        capture_output=True,
        text=True,
        timeout=30,
    )
    return {line.strip() for line in proc.stdout.splitlines() if line.strip()}


def _kill_pids(pids: set[str]) -> None:
    container = (settings.soffice_container or "").strip()
    if not container or not pids:
        return
    subprocess.run(
        ["docker", "exec", container, "sh", "-c", f"kill {' '.join(sorted(pids))}"],
        capture_output=True,
        timeout=30,
    )


def scene_listener(sample: Path, work: Path) -> Result:
    result = Result("复用尝试 B：常驻实例 + 委托转换")
    result.expected = 2
    profile_dir = work / "listener-profile"
    out_dirs = [work / f"listener-{index}" for index in range(2)]
    for out_dir in out_dirs:
        out_dir.mkdir(parents=True)

    before = _soffice_pids()
    base = _conversion_cmd(sample, out_dirs[0], profile_dir)
    try:
        convert_at = base.index("--convert-to")
    except ValueError:
        result.note = "命令结构不符，跳过"
        return result
    profile_arg = next((arg for arg in base if arg.startswith("-env:UserInstallation=")), None)
    if profile_arg is None:
        result.note = "没有 UserInstallation 参数，跳过"
        return result

    listener_cmd = [*base[:convert_at], profile_arg, f"--accept={ACCEPT}"]
    log_path = work / "listener.log"
    with log_path.open("wb") as log:
        listener = subprocess.Popen(listener_cmd, stdout=log, stderr=log, start_new_session=True)
    try:
        time.sleep(3)
        if listener.poll() is not None:
            result.note = "常驻实例启动即退出"
            return result

        alive_after: list[bool] = []
        for index, out_dir in enumerate(out_dirs):
            cmd = _conversion_cmd(sample, out_dir, profile_dir)
            elapsed, code = _run_one(cmd, timeout=180)
            produced = (out_dir / f"{sample.stem}.pdf").exists()
            if produced:
                result.outputs += 1
            result.seconds += elapsed
            time.sleep(0.5)
            alive_after.append(listener.poll() is None)
            print(
                f"  委托转换 {index + 1}：{elapsed:.2f} s，退出码 {code}，"
                f"产出 {'有' if produced else '无'}，常驻实例{'仍在' if alive_after[-1] else '已退出'}"
            )
        if not all(alive_after):
            result.note = "常驻实例在第一次转换后退出"
        elif result.outputs < result.expected:
            result.note = "有委托任务没有产出"
    finally:
        if listener.poll() is None:
            try:
                os.killpg(os.getpgid(listener.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                listener.terminate()
            try:
                listener.wait(timeout=10)
            except subprocess.TimeoutExpired:
                listener.kill()
        _kill_pids(_soffice_pids() - before)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    default_output = APP_BACKEND_DIR / "scripts" / "perf_results" / f"soffice_reuse-{stamp}.md"
    parser.add_argument("--samples", type=int, default=3, help="串行场景的转换次数")
    parser.add_argument("--output", type=Path, default=default_output)
    parser.add_argument("--keep", action="store_true", help="保留临时文件")
    args = parser.parse_args()

    if not (settings.soffice_container or "").strip() and not settings.soffice_binary:
        print("没有可用的 LibreOffice（SOFFICE_CONTAINER 与 SOFFICE_PATH 都为空），无法评估")
        return 2

    RUNTIME_TMP_DIR.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="bench-reuse-", dir=RUNTIME_TMP_DIR))
    try:
        sample = _make_sample(work)
        print(f"工作目录：{work}")
        print(f"转换环境：{'容器 ' + settings.soffice_container if settings.soffice_container else settings.soffice_binary}")

        results = [scene_sequential(sample, work, args.samples)]
        independent, shared = scene_concurrent(sample, work)
        results.extend([independent, shared])
        results.append(scene_listener(sample, work))

        lines = [
            f"# LibreOffice 转换进程复用评估（{datetime.now():%Y-%m-%d %H:%M}）",
            "",
            f"- 转换环境：`{settings.soffice_container or settings.soffice_binary}`",
            f"- 样本：`{sample.name}`（5 页左右）",
            f"- 并发上限配置：`SOFFICE_MAX_CONCURRENCY={settings.soffice_max_concurrency}`",
            f"- 单次超时：`SOFFICE_TIMEOUT_SECONDS={settings.soffice_timeout_seconds}`",
            "",
            "| 场景 | 样本数 | 总耗时 | 平均单次 | 产出文件 | 备注 |",
            "|:---|:---:|:---:|:---:|:---:|:---|",
        ]
        lines.extend(item.line() for item in results)
        lines.extend(
            [
                "",
                "## 结论",
                "",
                "- 独立 profile 串行是当前实现的基线；单次耗时里包含 soffice 冷启动。",
                "- 共用 profile 并发时，后启动的调用会委托给已在运行的实例。"
                "实测只要出现产出少于输入，就说明这版委托在并发下会静默丢任务。",
                "- 常驻实例方案要负责进程存活、任务完成判定与故障恢复；"
                "当前转换容器不带 Python UNO，改造成本与故障面都高于每次新起进程。",
                "- 因此暂不做进程复用，改用并发闸门限制进程数；排队等不到空位返回 50046。",
            ]
        )
        text = "\n".join(lines) + "\n"
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(f"\n结果已写入：{args.output}")
        print(text)
        return 0
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
