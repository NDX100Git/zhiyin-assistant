"""LibreOffice 转换并发闸门测试。

盯住三件事：
1. 同时运行的转换进程不超过配置上限，超出的排队而不是一起起进程；
2. 排队等不到空位时明确返回 50046，不静默降级；
3. 转换函数真的在槽位里跑，闸门饱和时不会再起 soffice 进程。
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from app.config import settings
from app.services import doc_parser
from app.utils.exceptions import BusinessError

LIMIT_CODE = 50046


@pytest.fixture
def gate(monkeypatch):
    """按用例设置并发上限与排队时长；信号量会随上限变化自动重建。"""

    def configure(limit: int, wait_seconds: float) -> None:
        monkeypatch.setattr(settings, "soffice_max_concurrency", limit)
        monkeypatch.setattr(settings, "soffice_queue_wait_seconds", wait_seconds)

    return configure


def _start_threads(count: int, target) -> list[threading.Thread]:
    threads = [threading.Thread(target=target) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads), "有线程没有在超时内结束"
    return threads


def test_超过上限的转换排队而不是同时起进程(gate):
    gate(limit=2, wait_seconds=5.0)
    barrier = threading.Barrier(6)
    lock = threading.Lock()
    active = 0
    peak = 0
    finished = 0

    def worker() -> None:
        nonlocal active, peak, finished
        barrier.wait()
        with doc_parser.conversion_slot():
            with lock:
                active += 1
                peak = max(peak, active)
            time.sleep(0.15)
            with lock:
                active -= 1
        with lock:
            finished += 1

    _start_threads(6, worker)

    assert peak == 2, f"同时运行的转换应正好卡在上限 2，实测峰值 {peak}"
    assert finished == 6, "排队的转换最终都要跑完，不应丢任务"


def test_排队超时返回明确错误(gate):
    gate(limit=1, wait_seconds=0.05)
    started = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with doc_parser.conversion_slot():
            started.set()
            release.wait(2)

    thread = threading.Thread(target=holder)
    thread.start()
    assert started.wait(1), "占位线程没有拿到槽位"

    try:
        with pytest.raises(BusinessError) as info:
            with doc_parser.conversion_slot():
                pytest.fail("槽位已占满，不该拿到槽位")
        assert info.value.code == LIMIT_CODE
        assert "排队已满" in info.value.message
    finally:
        release.set()
        thread.join(5)


def test_转换函数全程持有槽位(monkeypatch, gate, tmp_path):
    gate(limit=1, wait_seconds=2.0)
    monkeypatch.setattr(doc_parser, "_soffice_command", lambda *args, **kwargs: ["true"])

    lock = threading.Lock()
    active = 0
    peak = 0
    finished: list[Path] = []

    def fake_run(cmd, out_dir, stdout_log, stderr_log) -> None:
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.08)
        (out_dir / "input.pdf").write_bytes(b"%PDF-1.4\n")
        with lock:
            active -= 1

    monkeypatch.setattr(doc_parser, "_run_soffice", fake_run)
    barrier = threading.Barrier(4)

    def worker(index: int) -> None:
        out_dir = tmp_path / f"run-{index}"
        out_dir.mkdir()
        barrier.wait()
        finished.append(doc_parser.convert_document(Path("input.docx"), out_dir))

    threads = [threading.Thread(target=worker, args=(index,)) for index in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    assert all(not thread.is_alive() for thread in threads)

    assert peak == 1, "上限为 1 时四个转换必须串行"
    assert len(finished) == 4
    assert all(path.name == "input.pdf" for path in finished)


def test_闸门饱和时不再起转换进程(monkeypatch, gate, tmp_path):
    gate(limit=1, wait_seconds=0.02)
    monkeypatch.setattr(doc_parser, "_soffice_command", lambda *args, **kwargs: ["true"])
    calls: list[tuple] = []
    monkeypatch.setattr(doc_parser, "_run_soffice", lambda *args: calls.append(args))

    started = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with doc_parser.conversion_slot():
            started.set()
            release.wait(2)

    thread = threading.Thread(target=holder)
    thread.start()
    assert started.wait(1)

    try:
        with pytest.raises(BusinessError) as info:
            doc_parser.convert_document(Path("input.docx"), tmp_path)
        assert info.value.code == LIMIT_CODE
        assert calls == [], "拿不到槽位时不应该起 soffice 进程"
    finally:
        release.set()
        thread.join(5)


def test_并发上限与排队时长都有配置():
    assert settings.soffice_max_concurrency >= 1
    assert settings.soffice_queue_wait_seconds >= 0
    assert settings.soffice_timeout_seconds > 0


def test_转换失败后归还槽位(monkeypatch, gate, tmp_path):
    """转换抛错也必须释放名额，否则一次失败就把并发上限永久吃掉一格。"""
    gate(limit=1, wait_seconds=0.01)
    monkeypatch.setattr(doc_parser, "_soffice_command", lambda *args, **kwargs: ["true"])

    def failing_run(cmd, out_dir, stdout_log, stderr_log) -> None:
        raise BusinessError("模拟转换失败", code=50042)

    monkeypatch.setattr(doc_parser, "_run_soffice", failing_run)

    with pytest.raises(BusinessError) as info:
        doc_parser.convert_document(Path("input.docx"), tmp_path)
    assert info.value.code == 50042

    # 名额已归还：同一个上限下还能立刻拿到槽位
    with doc_parser.conversion_slot():
        pass
