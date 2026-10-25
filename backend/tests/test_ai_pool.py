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


def test_取消排队中的请求会归还名额(monkeypatch) -> None:
    """学生关页面、网关断开都会让请求被取消：排队中的名额不能留在池子里。"""
    monkeypatch.setattr(settings, "ai_max_workers", 1)
    monkeypatch.setattr(settings, "ai_queue_wait_seconds", 0.5)

    async def hold() -> None:
        async with slot():
            await asyncio.sleep(5)

    async def scenario() -> None:
        running = asyncio.create_task(hold())
        await asyncio.sleep(0.05)  # 先占住唯一名额
        queued = asyncio.create_task(hold())
        await asyncio.sleep(0.05)  # 第二个任务在事件循环里排队
        queued.cancel()
        with pytest.raises(asyncio.CancelledError):
            await queued
        running.cancel()
        with pytest.raises(asyncio.CancelledError):
            await running

        # 名额都还回来了：这里排队超时上限只有半秒，卡住就说明漏了
        async with slot():
            pass

    asyncio.run(scenario())


def test_取消执行中的请求会归还名额(monkeypatch) -> None:
    """名额已经拿到、活还在线程里跑的时候取消，同样要归还。"""
    monkeypatch.setattr(settings, "ai_max_workers", 1)
    monkeypatch.setattr(settings, "ai_queue_wait_seconds", 0.5)
    release = threading.Event()

    async def job() -> None:
        async with slot():
            await ai_pool.run_ai_job(release.wait, 5)

    async def scenario() -> None:
        task = asyncio.create_task(job())
        await asyncio.sleep(0.1)  # 名额已到手，线程卡在慢活里
        task.cancel()
        # 取消之后协程会先等线程里的慢活跑完（见 run_ai_job 的说明），
        # 所以这里要放开慢活，否则要等满 wait 的 5 秒
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with slot():
            pass

    try:
        asyncio.run(scenario())
    finally:
        release.set()  # 放开线程，别把慢活留到别的用例里


# ---------------------------------------------------------------- 断开连接后的会话生命周期
class _FakeSession:
    """模拟请求级数据库会话：请求结束后被关闭，关闭后再用就报错。

    真实场景就是 SQLAlchemy 的那句 "This session is closed"：会话关闭后任何一句
    SQL 都会抛这个异常，线程里的任务当场失败，结果没人收。
    """

    def __init__(self) -> None:
        self.closed = False
        self.used_after_close = 0

    def write(self, value: str) -> str:
        if self.closed:
            self.used_after_close += 1
            raise RuntimeError("This session is closed")
        return value


def test_客户端断开后任务跑完才关会话(monkeypatch) -> None:
    """客户端取消后，线程里的任务必须继续跑完，且会话在任务结束之后才关闭。"""
    monkeypatch.setattr(settings, "ai_max_workers", 1)
    session = _FakeSession()
    wrote: list[str] = []
    finished = threading.Event()

    def slow_job(db: _FakeSession) -> str:
        # 慢活：真实任务是解析文档、调模型、写报告缓存
        time.sleep(0.15)
        wrote.append(db.write("报告已写入缓存"))
        finished.set()
        return "done"

    async def endpoint() -> str:
        """模仿路由：拿到请求级会话，把它交给 AI 线程池。"""
        try:
            return await ai_pool.run_ai_job(slow_job, session)
        finally:
            session.closed = True  # get_db 的 finally：请求结束就关会话

    async def scenario() -> None:
        task = asyncio.create_task(endpoint())
        await asyncio.sleep(0.05)  # 线程已经在跑
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert session.closed, "请求结束后会话必须已经关闭"
        assert finished.is_set(), "取消之后线程里的任务必须跑完，不能半途丢下会话"
        assert wrote == ["报告已写入缓存"]
        assert session.used_after_close == 0, "任务用会话时它不能已经被关闭"

    asyncio.run(scenario())


def test_断开后任务报错也不会让请求卡死(monkeypatch) -> None:
    """任务自己失败时同样要收尾：请求结束、会话关闭，异常不向外冒成未处理错误。"""
    monkeypatch.setattr(settings, "ai_max_workers", 1)
    session = _FakeSession()

    def failing_job(db: _FakeSession) -> None:
        time.sleep(0.1)
        raise RuntimeError("模拟 AI 任务失败")

    async def endpoint() -> None:
        try:
            await ai_pool.run_ai_job(failing_job, session)
        finally:
            session.closed = True

    async def scenario() -> None:
        task = asyncio.create_task(endpoint())
        await asyncio.sleep(0.03)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert session.closed

    asyncio.run(scenario())
