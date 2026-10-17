"""统一的模型调用入口（阶段 5）。

约定：所有 DeepSeek 调用收口到本模块，业务代码不得直接调用 SDK。
API Key、base_url、模型名全部从环境变量读取（见 app.config.settings）。
内置 3 次重试与 60 秒超时；简单任务不开思考模式，复杂任务开启。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from openai import AsyncOpenAI, OpenAI

from app.config import settings
from app.utils.exceptions import BusinessError

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT = 60
MAX_ATTEMPTS = 3

_client: OpenAI | None = None
_async_client: AsyncOpenAI | None = None


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
            timeout=REQUEST_TIMEOUT,
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
            timeout=REQUEST_TIMEOUT,
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

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = get_client().chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as exc:  # 需要把各类 SDK 异常统一成重试
            last_error = exc
            logger.warning("模型调用失败（第 %d/%d 次）：%s", attempt, MAX_ATTEMPTS, exc)

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

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = await get_async_client().chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as exc:  # 统一重试
            last_error = exc
            logger.warning("异步模型调用失败（第 %d/%d 次）：%s", attempt, MAX_ATTEMPTS, exc)

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
