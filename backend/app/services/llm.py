"""统一的模型调用入口（阶段 5）。

约定：所有 DeepSeek 调用收口到本模块，业务代码不得直接调用 SDK。
API Key、base_url、模型名全部从环境变量读取（见 app.config.settings）。
内置有限次重试与超时；简单任务不开思考模式，复杂任务开启。

重试只对「等一会儿可能就好了」的失败生效：网络抖动、超时、5xx、限流。
鉴权失败、参数不合法这类必然失败的错，重试只会把同一个错误再撞一遍，
白等几十秒还多烧一次请求，所以按错误类型分流，一次就报错。
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
from typing import Any

from openai import (
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    NotFoundError,
    OpenAI,
    PermissionDeniedError,
)

from app.config import settings
from app.utils.exceptions import BusinessError

logger = logging.getLogger(__name__)

# 必然失败的状态码：改配置或改代码才会好，重试毫无意义
PERMANENT_STATUS_CODES = frozenset({400, 401, 403, 404, 405, 409, 422})
# 必然失败的异常类型（SDK 已按语义分好类，比只看状态码更稳）
PERMANENT_ERROR_TYPES = (
    AuthenticationError,
    PermissionDeniedError,
    BadRequestError,
    NotFoundError,
)


def is_permanent_error(exc: BaseException) -> bool:
    """这次失败重试也没用吗？鉴权、参数、模型不存在都属于这一类。"""
    if isinstance(exc, PERMANENT_ERROR_TYPES):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in PERMANENT_STATUS_CODES


def _permanent_error(exc: BaseException) -> BusinessError:
    """把必然失败包装成对外错误：说清是配置问题，别让学生一直点重试。"""
    status = getattr(exc, "status_code", None)
    detail = f"（HTTP {status}）" if isinstance(status, int) else ""
    return BusinessError(
        f"AI 服务当前不可用{detail}，请联系管理员检查模型与密钥配置", code=50031
    )

_client: OpenAI | None = None
_async_client: AsyncOpenAI | None = None
# 懒加载必须加锁：首次被并发调用时，几个线程会同时看到 None，各建一个客户端，
# 后建的把先建的覆盖掉。被覆盖的那个连接池没人关闭（AsyncOpenAI 还绑着事件循环），
# 「第一次有人点体检」就成了一个稳定的连接泄漏点。
# 锁只包住「创建」这一小段，模型请求一行都不在锁里，否则并发又被串回一条线。
_client_lock = threading.Lock()
_async_client_lock = threading.Lock()

# 并发闸门：模型调用慢，必须限流，否则几十个人同时点体检会占满线程池，
# 登录、下单这些快接口跟着排队。闸门大小随配置变化自动重建，改配置不用重启也行。
_gate_guard = threading.Lock()
_gate: tuple[int, threading.BoundedSemaphore] | None = None
_in_flight = 0


def _semaphore() -> threading.BoundedSemaphore:
    global _gate
    size = max(1, int(settings.llm_max_concurrency))
    with _gate_guard:
        if _gate is None or _gate[0] != size:
            _gate = (size, threading.BoundedSemaphore(size))
        return _gate[1]


def is_configured() -> bool:
    """是否已配置 API Key。"""
    return bool(settings.llm_api_key and settings.llm_api_key.strip())


def _client_kwargs() -> dict[str, Any]:
    """同步与异步客户端共用同一套参数，避免两处各写一遍后慢慢跑偏。"""
    return {
        "api_key": settings.llm_api_key,
        "base_url": settings.llm_base_url,
        "timeout": settings.llm_timeout_seconds,
        # 关掉 SDK 自带重试：外层 _chat_with_retry 已经重试，两层叠加时
        # 最坏 2×3×timeout 秒，模块承诺的「一次调用 60 秒内结束」直接作废
        "max_retries": 0,
    }


def get_client() -> OpenAI:
    """懒加载 OpenAI 客户端（DeepSeek 完全兼容 OpenAI SDK）。"""
    global _client
    if not is_configured():
        raise BusinessError("服务端未配置 LLM_API_KEY，AI 功能暂不可用", code=50030)
    client = _client
    if client is not None:
        # 快路径不加锁：建好之后每次调用都走这里，不该为一把锁排一次队
        return client
    with _client_lock:
        if _client is None:
            _client = OpenAI(**_client_kwargs())
        return _client


def get_async_client() -> AsyncOpenAI:
    """异步客户端（长文档分块并发调用时使用）。"""
    global _async_client
    if not is_configured():
        raise BusinessError("服务端未配置 LLM_API_KEY，AI 功能暂不可用", code=50030)
    client = _async_client
    if client is not None:
        return client
    with _async_client_lock:
        if _async_client is None:
            _async_client = AsyncOpenAI(**_client_kwargs())
        return _async_client


def _build_kwargs(
    messages: list[dict[str, Any]],
    json_mode: bool,
    thinking: bool,
    temperature: float | None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = {"model": settings.llm_model, "messages": messages}
    if temperature is not None:
        kwargs["temperature"] = temperature
    if json_mode:
        kwargs["response_format"] = {"type": "json_object"}
    if thinking:
        kwargs["extra_body"] = {"thinking": {"type": "enabled"}}
    return kwargs


def chat(
    messages: list[dict[str, Any]],
    json_mode: bool = False,
    thinking: bool = False,
    temperature: float | None = None,
) -> str:
    """统一模型调用入口，返回文本内容。

    json_mode=True 时要求模型返回 JSON 对象；
    thinking=True 用于版式诊断、格式校验等需要推理的复杂任务。
    """
    kwargs = _build_kwargs(messages, json_mode, thinking, temperature)
    client = get_client()

    slot = _acquire_slot(float(settings.llm_queue_wait_seconds))
    try:
        return _chat_with_retry(client, kwargs)
    finally:
        _release_slot(slot)


def _acquire_slot(wait_seconds: float) -> threading.BoundedSemaphore:
    """拿全局模型调用名额，同步与异步共用。

    返回拿到的信号量本身，归还时释放同一个对象，避免配置变更后释放到新信号量。
    """
    semaphore = _semaphore()
    if not semaphore.acquire(timeout=wait_seconds):
        # 队列里等太久说明此刻排满了，早失败比让请求占着线程干等更好
        logger.warning("AI 并发已满，等待 %.1f 秒未拿到名额，直接拒绝", wait_seconds)
        raise BusinessError("当前使用 AI 的人较多，请稍后重试", code=50034)

    global _in_flight
    with _gate_guard:
        _in_flight += 1
        current = _in_flight
    logger.info("AI 调用开始，当前并发 %d", current)
    return semaphore


def _release_slot(semaphore: threading.BoundedSemaphore) -> None:
    global _in_flight
    with _gate_guard:
        _in_flight -= 1
    semaphore.release()


def _release_orphan_slot(task: asyncio.Future) -> None:
    """排队期间被取消的调用：线程事后拿到的名额要就地归还。"""
    if task.cancelled():
        return
    if task.exception() is not None:
        # 排队超时之类的失败，本来就没拿到名额，不用归还
        return
    semaphore = task.result()
    if semaphore is not None:
        _release_slot(semaphore)


async def _acquire_slot_async(wait_seconds: float) -> threading.BoundedSemaphore:
    """异步排队拿名额，且任务被取消时不留孤儿名额。

    `asyncio.to_thread` 只把排队丢进线程：调用方取消之后线程照旧继续等，最终
    仍会拿到名额。若直接放弃等待，这个名额就永远悬在那儿没人归还，几次取消之后
    异步模型调用的并发上限被吃光，后续调用全部排队超时。

    所以这里记一个「调用方已经走了」的标志：线程拿到名额时看到标志就当场归还；
    标志与拿到名额正好擦肩而过时，由回调归还。两条路互斥，名额只还一次。
    """
    abandoned = threading.Event()

    def worker() -> threading.BoundedSemaphore | None:
        semaphore = _acquire_slot(wait_seconds)
        if abandoned.is_set():
            _release_slot(semaphore)
            return None
        return semaphore

    task = asyncio.ensure_future(asyncio.to_thread(worker))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        abandoned.set()
        task.add_done_callback(_release_orphan_slot)
        raise


def _chat_with_retry(client: OpenAI, kwargs: dict[str, Any]) -> str:
    attempts = max(1, int(settings.llm_max_attempts))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as exc:  # 需要把各类 SDK 异常统一成重试
            if is_permanent_error(exc):
                logger.error("模型调用失败且重试无效，直接报错：%s", exc)
                raise _permanent_error(exc) from exc
            last_error = exc
            logger.warning("模型调用失败（第 %d/%d 次）：%s", attempt, attempts, exc)

    raise BusinessError("AI 服务暂时不可用，请稍后重试", code=50031) from last_error


def extract_json(text: str) -> dict[str, Any]:
    """JSON 解析容错：模型偶尔会多输出解释文字，需要截取合法 JSON 部分。"""
    if not text or not text.strip():
        raise BusinessError("AI 未返回内容", code=50032)

    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(.+?)```", candidate, re.S)
    if fence:
        candidate = fence.group(1).strip()

    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        pass

    start = candidate.find("{")
    if start == -1:
        raise BusinessError("AI 返回内容不是合法 JSON", code=50033)

    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(candidate)):
        char = candidate[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(candidate[start : index + 1])
                except json.JSONDecodeError as exc:
                    raise BusinessError("AI 返回内容不是合法 JSON", code=50033) from exc

    raise BusinessError("AI 返回内容不是合法 JSON", code=50033)


def chat_json(
    messages: list[dict[str, Any]],
    thinking: bool = False,
    temperature: float | None = None,
) -> dict[str, Any]:
    """JSON 模式调用 + 解析容错。"""
    return extract_json(chat(messages, json_mode=True, thinking=thinking, temperature=temperature))


async def achat(
    messages: list[dict[str, Any]],
    json_mode: bool = False,
    thinking: bool = False,
    temperature: float | None = None,
) -> str:
    """异步版 chat，供 asyncio.gather 并发调用。

    异步调用也必须经过同步 chat 用的那道全局闸门，否则校对任务与体检端点
    各算各的并发，实际模型调用量会突破配置上限。
    """
    kwargs = _build_kwargs(messages, json_mode, thinking, temperature)

    # 排队在别的线程里等，不卡事件循环；取消时名额也会被还掉；拿到的名额在 finally 归还
    slot = await _acquire_slot_async(float(settings.llm_queue_wait_seconds))
    try:
        attempts = max(1, int(settings.llm_max_attempts))
        last_error: Exception | None = None
        for attempt in range(1, attempts + 1):
            try:
                response = await get_async_client().chat.completions.create(**kwargs)
                return response.choices[0].message.content or ""
            except Exception as exc:  # 统一重试，但必然失败的不重试
                if is_permanent_error(exc):
                    logger.error("异步模型调用失败且重试无效，直接报错：%s", exc)
                    raise _permanent_error(exc) from exc
                last_error = exc
                logger.warning("异步模型调用失败（第 %d/%d 次）：%s", attempt, attempts, exc)

        raise BusinessError("AI 服务暂时不可用，请稍后重试", code=50031) from last_error
    finally:
        _release_slot(slot)


async def achat_json(
    messages: list[dict[str, Any]],
    thinking: bool = False,
    temperature: float | None = None,
) -> dict[str, Any]:
    """异步 JSON 模式调用 + 解析容错。"""
    return extract_json(
        await achat(messages, json_mode=True, thinking=thinking, temperature=temperature)
    )
