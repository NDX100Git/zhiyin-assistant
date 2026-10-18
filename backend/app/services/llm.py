"""统一的模型调用入口（阶段 5）。

约定：所有 DeepSeek 调用收口到本模块，业务代码不得直接调用 SDK。
API Key、base_url、模型名全部从环境变量读取（见 app.config.settings）。
内置 3 次重试与 60 秒超时；简单任务不开思考模式，复杂任务开启。
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any

from openai import AsyncOpenAI, OpenAI

from app.config import settings
from app.utils.exceptions import BusinessError

logger = logging.getLogger(__name__)

_client: OpenAI | None = None
_async_client: AsyncOpenAI | None = None

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


def get_client() -> OpenAI:
    """懒加载 OpenAI 客户端（DeepSeek 完全兼容 OpenAI SDK）。"""
    global _client
    if not is_configured():
        raise BusinessError("服务端未配置 LLM_API_KEY，AI 功能暂不可用", code=50030)
    if _client is None:
        _client = OpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
        )
    return _client


def get_async_client() -> AsyncOpenAI:
    """异步客户端（长文档分块并发调用时使用）。"""
    global _async_client
    if not is_configured():
        raise BusinessError("服务端未配置 LLM_API_KEY，AI 功能暂不可用", code=50030)
    if _async_client is None:
        _async_client = AsyncOpenAI(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            timeout=settings.llm_timeout_seconds,
        )
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

    wait_seconds = float(settings.llm_queue_wait_seconds)
    if not _semaphore().acquire(timeout=wait_seconds):
        # 队列里等太久说明此刻排满了，早失败比让请求占着线程干等更好
        logger.warning("AI 并发已满，等待 %.1f 秒未拿到名额，直接拒绝", wait_seconds)
        raise BusinessError("当前使用 AI 的人较多，请稍后重试", code=50034)

    global _in_flight
    with _gate_guard:
        _in_flight += 1
        current = _in_flight
    try:
        logger.info("AI 调用开始，当前并发 %d", current)
        return _chat_with_retry(client, kwargs)
    finally:
        with _gate_guard:
            _in_flight -= 1
        _semaphore().release()


def _chat_with_retry(client: OpenAI, kwargs: dict[str, Any]) -> str:
    attempts = max(1, int(settings.llm_max_attempts))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as exc:  # 需要把各类 SDK 异常统一成重试
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
    """异步版 chat，供 asyncio.gather 并发调用。"""
    kwargs = _build_kwargs(messages, json_mode, thinking, temperature)

    attempts = max(1, int(settings.llm_max_attempts))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = await get_async_client().chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as exc:  # 统一重试
            last_error = exc
            logger.warning("异步模型调用失败（第 %d/%d 次）：%s", attempt, attempts, exc)

    raise BusinessError("AI 服务暂时不可用，请稍后重试", code=50031) from last_error


async def achat_json(
    messages: list[dict[str, Any]],
    thinking: bool = False,
    temperature: float | None = None,
) -> dict[str, Any]:
    """异步 JSON 模式调用 + 解析容错。"""
    return extract_json(
        await achat(messages, json_mode=True, thinking=thinking, temperature=temperature)
    )
