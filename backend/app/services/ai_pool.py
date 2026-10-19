"""AI 任务专用线程池：把慢活从公共线程池里分出去。

FastAPI 只会给同步端点分配公共线程池（默认四十个线程），而一次 AI 体检能占住
其中一线程几十秒。几十个人同时点体检，登录、下单这些毫秒级接口就得排队——这就是
「全站超时、但 CPU 几乎空转」的典型症状。

分流办法：
- AI 端点改成异步函数，慢活交给本模块的小线程池（默认四个工作线程）；
- 名额用 asyncio 信号量控制，**等待发生在事件循环里，不占线程**；
- 名额要在数据库会话之前拿到（见 ai_slot 依赖）。FastAPI 会先解出依赖再进端点，
  如果先开数据库会话再排队，六十个等模型的请求就会占掉六十条数据库连接——
  实测就是这样把连接池打爆、交易链路出现十秒尖峰的；
- 排太久就早失败，让学生重试，而不是让请求在内存里堆着。

这样公共线程池始终留给交易链路，AI 再慢也只影响自己那几个名额。
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from typing import Any, TypeVar

from app.config import settings
from app.utils.exceptions import BusinessError

logger = logging.getLogger(__name__)

T = TypeVar("T")

_executor: ThreadPoolExecutor | None = None
_executor_size: int = 0
# 每个事件循环一份信号量：asyncio 的同步原语跨循环复用会出问题
_semaphores: dict[int, asyncio.Semaphore] = {}


def _pool_size() -> int:
    return max(1, int(settings.ai_max_workers))


def get_executor() -> ThreadPoolExecutor:
    """懒加载线程池；配置里的并发数改了会自动重建。"""
    global _executor, _executor_size
    size = _pool_size()
    if _executor is None or _executor_size != size:
        if _executor is not None:
            _executor.shutdown(wait=False)
        _executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix="ai-job")
        _executor_size = size
    return _executor


def _semaphore() -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    semaphore = _semaphores.get(loop_id)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_pool_size())
        _semaphores[loop_id] = semaphore
    return semaphore


async def ai_slot() -> AsyncIterator[None]:
    """拿一个 AI 名额，拿不到就早失败；退出时归还。

    这是 FastAPI 依赖，必须排在数据库相关依赖**之前**：签名里写在前面，
    FastAPI 就先解它，排队期间不会占数据库连接。

    归还放在一个 finally 里，且只释放拿到的那个信号量对象：正常结束、排队超时、
    请求被取消（学生关页面、网关断开）都走同一条路，不会把名额留在池子里。
    """
    wait_seconds = float(settings.ai_queue_wait_seconds)
    semaphore = _semaphore()
    acquired = False
    try:
        try:
            async with asyncio.timeout(wait_seconds):
                await semaphore.acquire()
                # 标记写在 timeout 内部：拿到名额之后无论怎么退出，finally 都能归还
                acquired = True
        except TimeoutError:
            logger.warning("AI 名额排队超过 %.1f 秒，直接拒绝", wait_seconds)
            raise BusinessError("当前使用 AI 的人较多，请稍后重试", code=50034) from None

        yield
    finally:
        if acquired:
            semaphore.release()


async def run_ai_job(func: Callable[..., T], *args: Any, **kwargs: Any) -> T:
    """把阻塞的 AI 任务丢到专用线程池执行，等待过程不占线程。

    线程池本身有固定大小，超出的任务在池子的队列里等，等待同样不占线程。
    并发名额由 ai_slot 依赖负责，这里不再重复限制。
    """
    loop = asyncio.get_running_loop()
    call = functools.partial(func, *args, **kwargs)
    return await loop.run_in_executor(get_executor(), call)


def shutdown_ai_pool() -> None:
    """服务关闭时收尾；不等待正在跑的任务，进程本来就要退。"""
    global _executor, _executor_size
    if _executor is not None:
        _executor.shutdown(wait=False)
        _executor = None
        _executor_size = 0
    _semaphores.clear()
