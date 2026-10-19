"""AI 专用线程池的测试：并发上限、不占事件循环、异常照原样抛出。

这一层解决的是「AI 慢就把全站拖死」的问题，所以断言盯三件事：
慢活真的在别的线程里跑、并发不超过配置、等名额的时候事件循环还能干活。
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import threading
import time

import pytest

from app.config import settings
from app.services import ai_pool
from app.utils.exceptions import BusinessError

slot = contextlib.asynccontextmanager(ai_pool.ai_slot)


@pytest.fixture(autouse=True)
def _reset_pool():
    ai_pool.shutdown_ai_pool()
    yield
    ai_pool.shutdown_ai_pool()


def test_任务在别的线程里跑() -> None:
    caller = threading.get_ident()
    seen: list[int] = []

    async def scenario() -> None:
        await ai_pool.run_ai_job(lambda: seen.append(threading.get_ident()))

    asyncio.run(scenario())
    assert seen and seen[0] != caller


def test_并发不超过池子大小(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_max_workers", 2)
    monkeypatch.setattr(settings, "ai_queue_wait_seconds", 10.0)
    lock = threading.Lock()
    state = {"active": 0, "peak": 0}

    def slow() -> None:
        with lock:
            state["active"] += 1
            state["peak"] = max(state["peak"], state["active"])
        time.sleep(0.15)
        with lock:
            state["active"] -= 1

    async def one() -> None:
        async with slot():
            await ai_pool.run_ai_job(slow)

    async def scenario() -> None:
        await asyncio.gather(*(one() for _ in range(6)))

    asyncio.run(scenario())
    assert state["peak"] <= 2


def test_等名额时事件循环不阻塞(monkeypatch) -> None:
    """八个任务抢两个名额，期间事件循环仍要能按时跑别的协程。"""
    monkeypatch.setattr(settings, "ai_max_workers", 2)
    monkeypatch.setattr(settings, "ai_queue_wait_seconds", 10.0)
    ticks: list[float] = []

    async def heartbeat() -> None:
        for _ in range(10):
            ticks.append(time.perf_counter())
            await asyncio.sleep(0.02)

    async def job() -> None:
        async with slot():
            await ai_pool.run_ai_job(time.sleep, 0.1)

    async def scenario() -> None:
        await asyncio.gather(heartbeat(), *(job() for _ in range(8)))

    asyncio.run(scenario())
    gaps = [b - a for a, b in itertools.pairwise(ticks)]
    # 心跳间隔应当始终接近 20 毫秒；被线程阻塞的话会出现几百毫秒的空洞
    assert max(gaps) < 0.15


def test_排队超时早失败(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_max_workers", 1)
    monkeypatch.setattr(settings, "ai_queue_wait_seconds", 0.2)

    async def hold() -> None:
        async with slot():
            await asyncio.sleep(0.6)

    async def scenario() -> BusinessError:
        first = asyncio.create_task(hold())
        await asyncio.sleep(0.05)  # 让第一个先占住名额
        try:
            async with slot():
                pass
        except BusinessError as error:
            return error
        finally:
            await first
        raise AssertionError("排队超时却没有报错")

    error = asyncio.run(scenario())
    assert error.code == 50034
    assert "稍后重试" in str(error)


def test_业务异常原样抛出() -> None:
    def boom() -> None:
        raise BusinessError("模型没配好", code=50030)

    async def scenario() -> BusinessError:
        try:
            await ai_pool.run_ai_job(boom)
        except BusinessError as error:
            return error
        raise AssertionError("异常没有抛出")

    assert asyncio.run(scenario()).code == 50030


def test_名额用完会归还(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ai_max_workers", 1)
    monkeypatch.setattr(settings, "ai_queue_wait_seconds", 1.0)

    async def scenario() -> None:
        for _ in range(5):
            async with slot():
                await ai_pool.run_ai_job(lambda: None)

    asyncio.run(scenario())
