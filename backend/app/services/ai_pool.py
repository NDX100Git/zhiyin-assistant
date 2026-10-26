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

2026-10 拆成两条车道（见下面的 LANE_*）：表格归一化 + LibreOffice 转换一条请求
的预算默认 240 秒，与「几十秒内结束」的模型调用差一个量级。混在同一条车道上时，
一张大表就能把全部 AI 名额占满四分钟，别人点体检只会看到「人较多」。所以转换
单独一条车道、自己一套线程与名额，且上限更小：转换可以等，但不能拖住模型调用。

这样公共线程池始终留给交易链路，AI 再慢也只影响自己那几个名额。
"""

from __future__ import annotations

import asyncio
import functools
import logging
from collections.abc import AsyncIterator, Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from typing import Any

from app.config import settings
from app.utils.exceptions import BusinessError
from app.utils.filetype import normalize_ext

logger = logging.getLogger(__name__)

# 两条车道。模型车道放「几十秒内应该有结果」的调用；转换车道放表格归一化与
# LibreOffice 转换这类能跑满几分钟的长活。
LANE_AI = "ai"
LANE_CONVERSION = "conversion"
# 转换车道认的文件类型：表格要先归一版式再转 PDF，放宽横向页数会重跑多轮。
# 与 doc_parser.SPREADSHEET_EXTS 同口径（tests/test_llm_gate.py 有一步锁住）；
# 不直接 import doc_parser 是为了不让线程池模块拖进 PyMuPDF 与存储层。
CONVERSION_EXTS = frozenset({"xls", "xlsx"})

_executors: dict[str, tuple[int, ThreadPoolExecutor]] = {}
# 每个事件循环、每条车道一份信号量：asyncio 的同步原语跨循环复用会出问题
_semaphores: dict[int, dict[str, asyncio.Semaphore]] = {}


def lane_for_file(file_key: str) -> str:
    """这个文件该走哪条车道：表格走转换车道，其余走模型车道。"""
    return LANE_CONVERSION if normalize_ext(file_key) in CONVERSION_EXTS else LANE_AI


def _lane_size(lane: str) -> int:
    """车道里同时能跑几个任务（也是线程数）。"""
    if lane == LANE_CONVERSION:
        return max(1, int(settings.conversion_max_workers))
    return max(1, int(settings.ai_max_workers))


def _lane_wait(lane: str) -> float:
    """这条车道上排队最多等多久，等不到就早失败。"""
    if lane == LANE_CONVERSION:
        return float(settings.conversion_queue_wait_seconds)
    return float(settings.ai_queue_wait_seconds)


def get_executor(lane: str = LANE_AI) -> ThreadPoolExecutor:
    """懒加载这条车道的线程池；配置里的并发数改了会自动重建。"""
    size = _lane_size(lane)
    current = _executors.get(lane)
    if current is None or current[0] != size:
        if current is not None:
            current[1].shutdown(wait=False)
        executor = ThreadPoolExecutor(max_workers=size, thread_name_prefix=f"ai-job-{lane}")
        _executors[lane] = (size, executor)
        return executor
    return current[1]


def _semaphore(lane: str) -> asyncio.Semaphore:
    loop_id = id(asyncio.get_running_loop())
    lanes = _semaphores.setdefault(loop_id, {})
    semaphore = lanes.get(lane)
    if semaphore is None:
        semaphore = asyncio.Semaphore(_lane_size(lane))
        lanes[lane] = semaphore
    return semaphore


@asynccontextmanager
async def job_lane(lane: str) -> AsyncIterator[None]:
    """在指定车道上拿一个名额，拿不到就早失败；退出时归还。

    归还放在一个 finally 里，且只释放拿到的那个信号量对象：正常结束、排队超时、
    请求被取消（学生关页面、网关断开）都走同一条路，不会把名额留在池子里。
    """
    wait_seconds = _lane_wait(lane)
    semaphore = _semaphore(lane)
    acquired = False
    try:
        try:
            async with asyncio.timeout(wait_seconds):
                await semaphore.acquire()
                # 标记写在 timeout 内部：拿到名额之后无论怎么退出，finally 都能归还
                acquired = True
        except TimeoutError:
            logger.warning(
                "%s 车道名额排队超过 %.1f 秒，直接拒绝", lane, wait_seconds
            )
            raise BusinessError("当前使用 AI 的人较多，请稍后重试", code=50034) from None

        yield
    finally:
        if acquired:
            semaphore.release()


async def ai_slot() -> AsyncIterator[None]:
    """拿一个模型车道名额（体检、看图识字、密钥检查等短任务）。

    这是 FastAPI 依赖，必须排在数据库相关依赖**之前**：签名里写在前面，
    FastAPI 就先解它，排队期间不会占数据库连接。
    """
    async with job_lane(LANE_AI):
        yield


async def conversion_slot() -> AsyncIterator[None]:
    """拿一个转换车道名额（PDF 转 Word、修复文档、文档转换等长任务）。"""
    async with job_lane(LANE_CONVERSION):
        yield


async def run_ai_job[T](func: Callable[..., T], *args: Any, lane: str = LANE_AI, **kwargs: Any) -> T:
    """把阻塞的 AI 任务丢到指定车道的专用线程池执行，等待过程不占线程。

    线程池本身有固定大小，超出的任务在池子的队列里等，等待同样不占线程。
    并发名额由对应的 slot 依赖负责，这里不再重复限制。

    任务跑在线程里，但用的仍是**请求级数据库会话**（调用方把它当第一个参数传进来）。
    客户端断开（关页面、网关超时）时，Starlette 会取消这个请求的协程，请求随即结束，
    get_db 的 finally 会把会话关掉——而线程里的任务还在用它，接下来每一句 SQL 都会抛
    "This session is closed"。任务的结果没人收，日志里只剩一堆看不出原因的报错。

    所以这里屏蔽取消：客户端断开只是「不再需要这个响应」，不意味着活该不干完。
    协程会一直等到线程任务真正结束才向后传取消信号，会话关闭必然发生在任务结束之后。
    代价是一个已经断开连接的请求仍会占满它的名额直到任务结束，这是故意的：
    提前放弃会留下一个跑到一半的任务和一个已被关闭的会话，比多占几秒名额严重得多。
    """
    loop = asyncio.get_running_loop()
    call = functools.partial(func, *args, **kwargs)
    task = loop.run_in_executor(get_executor(lane), call)
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # 断开后仍要等任务跑完：等不到就会话已关，线程里的任务只能报错退出。
        # 重复取消（网关重试、层层收尾）会再抛 CancelledError，所以用循环兜住，
        # 直到任务真的结束才把取消信号向外传。
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:  # 任务自己失败：结果已无人接收，只需保证会话不被提前关
                break
        logger.info("客户端已断开，AI 任务跑完后才结束请求，会话不会被提前关闭")
        raise


def shutdown_ai_pool() -> None:
    """服务关闭时收尾；不等待正在跑的任务，进程本来就要退。"""
    for _size, executor in list(_executors.values()):
        executor.shutdown(wait=False)
    _executors.clear()
    _semaphores.clear()
