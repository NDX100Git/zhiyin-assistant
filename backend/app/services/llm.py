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
import functools
import json
import logging
import re
import threading
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
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


# ---------------------------------------------------------------- 学生自带的密钥
#
# 产品口径：学校有一把服务器上的大 key，学生也可以另外填自己的 key。学生的 key
# 只在请求头里带一次，后端用完即弃：不写数据库、不写日志、不写任务结果、不进 URL
# 查询参数、不进响应体。下面这几条约定就是这条口径的落地方式：
# - 密钥以**参数**的形式传进每一次调用（chat/achat 的 api_key），不落到任何模块级
#   全局变量上，并发请求因此不会互相串用；
# - 带学生密钥的客户端不缓存，每次调用新建、用完立刻关掉（见 _sync_client 的说明）；
# - 任何要进日志或错误信息的文本都过 scrub_secret，SDK 报错里常把 key 原样带出来。
USER_KEY_HEADER = "X-LLM-Api-Key"
# 太短的串不可能是有效密钥（DeepSeek 的形如 sk-xxxx），当成本地传错处理
MIN_USER_KEY_LENGTH = 8
MASK_HEAD = 4
MASK_TAIL = 4


def mask_key(value: str | None) -> str:
    """密钥脱敏：只保留前 4 后 4，长度不足 4+4 则整串遮掉。"""
    if not value:
        return ""
    text = str(value)
    if len(text) <= MASK_HEAD + MASK_TAIL:
        return "*" * len(text)
    return f"{text[:MASK_HEAD]}****{text[-MASK_TAIL:]}"


def scrub_secret(text: str, secret: str | None) -> str:
    """把文本里出现的密钥换成脱敏形式。SDK 的报错会把 key 原样打在消息里。"""
    if not text or not secret:
        return text
    return text.replace(secret, mask_key(secret))


def user_key_from_header(raw: str | None) -> str | None:
    """解析请求头里的学生密钥。

    没带、或运维关掉了这个功能时返回 None（仍按学校共享密钥调用）；带了但格式
    明显不对时直接报错，而不是猜一个值去打模型。日志里只出现脱敏形式。
    """
    value = (raw or "").strip()
    if not value:
        return None
    if not settings.llm_user_key_enabled:
        logger.info(
            "学校已关闭学生自带密钥，本次请求头被忽略（%s），改用学校共享密钥",
            mask_key(value),
        )
        return None
    if len(value) < MIN_USER_KEY_LENGTH or any(char.isspace() for char in value):
        raise BusinessError("提交的 AI 密钥格式不正确，请检查后重新填写", code=50036)
    logger.info("本次请求使用学生自带的 AI 密钥：%s", mask_key(value))
    return value


def bind_key(func: Callable[..., Any], api_key: str | None) -> Callable[..., Any]:
    """把「本次调用的学生密钥」绑到一个模型入口函数上。

    没带密钥时原样返回函数对象：绝大多数请求走的是学校共享密钥，入口保持原签名，
    测试里的替身（monkeypatch 模块属性）也照旧生效。带了密钥才用 partial 绑定，
    密钥只存在这一次调用的参数里，不进模块级变量，也不进任何缓存键。
    """
    if not api_key:
        return func
    return functools.partial(func, api_key=api_key)


def is_permanent_error(exc: BaseException) -> bool:
    """这次失败重试也没用吗？鉴权、参数、模型不存在都属于这一类。"""
    if isinstance(exc, PERMANENT_ERROR_TYPES):
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status in PERMANENT_STATUS_CODES


def _key_hint(api_key: str | None) -> str:
    return f"（{mask_key(api_key)}）" if api_key else ""


def _permanent_error(exc: BaseException, api_key: str | None = None) -> BusinessError:
    """把必然失败包装成对外错误：说清原因，别让学生一直点重试。

    学生自带密钥失败时给出的是另一套说法：**不回退**到学校共享密钥。悄悄退回
    去学生以为在用自己的额度，实际在烧学校的钱，真正的失败原因（密钥失效）也
    被藏起来，只会反复重试。所以这里直接告诉学生怎么办。
    """
    status = getattr(exc, "status_code", None)
    detail = f"（HTTP {status}）" if isinstance(status, int) else ""
    if api_key:
        return BusinessError(
            f"你填写的 AI 密钥不可用{detail}{_key_hint(api_key)}："
            "请在设置里清除它后改用学校共享密钥再试",
            code=50035,
        )
    return BusinessError(
        f"AI 服务当前不可用{detail}，请联系管理员检查模型与密钥配置", code=50031
    )


def _exhausted_error(api_key: str | None = None) -> BusinessError:
    """重试次数用完后的对外错误；带学生密钥时说清是这把密钥的事。"""
    if api_key:
        return BusinessError(
            f"用你填写的 AI 密钥{_key_hint(api_key)}调用模型失败，请稍后重试或清除该密钥",
            code=50035,
        )
    return BusinessError("AI 服务暂时不可用，请稍后重试", code=50031)


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


def _client_kwargs(api_key: str | None = None) -> dict[str, Any]:
    """同步与异步客户端共用同一套参数，避免两处各写一遍后慢慢跑偏。"""
    return {
        "api_key": api_key or settings.llm_api_key,
        "base_url": settings.llm_base_url,
        "timeout": settings.llm_timeout_seconds,
        # 关掉 SDK 自带重试：外层 _chat_with_retry 已经重试，两层叠加时
        # 最坏 2×3×timeout 秒，模块承诺的「一次调用 60 秒内结束」直接作废
        "max_retries": 0,
    }


def get_client() -> OpenAI:
    """懒加载 OpenAI 客户端（DeepSeek 完全兼容 OpenAI SDK）。

    这里只负责**学校共享密钥**的客户端：它来自环境变量，进程里长期存在是正常的。
    学生自带的密钥走 _sync_client 的临时客户端，不经过这个缓存。
    """
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


def _close_quietly(client: Any) -> None:
    """关掉一次性客户端。关不掉也不能盖住真正的业务错误。"""
    try:
        client.close()
    except Exception:  # 关闭失败只影响连接池回收，不该变成对学生的报错
        logger.debug("关闭一次性 AI 客户端时出错", exc_info=True)


@contextmanager
def _sync_client(api_key: str | None = None) -> Iterator[OpenAI]:
    """这一次调用要用的客户端。

    学校共享密钥照旧懒加载缓存；学生自带的密钥**不缓存**，每次调用新建、用完就
    立刻关闭。理由：
    1. 要缓存就得用密钥当缓存键，而密钥是学生的一次性凭据，「用完即弃」在缓存上
       就落不了地：进程里留得越久，被内存转储或调试日志带出去的机会越大；
    2. 键来自任意学生输入，缓存会被无限撑大，还能被当成「某个密钥是否来过」的探针。
    代价是每次多建一个 httpx 连接池（毫秒级），相对一次模型调用的秒级开销可以忽略。
    """
    if not api_key:
        yield get_client()
        return
    client = OpenAI(**_client_kwargs(api_key))
    try:
        yield client
    finally:
        _close_quietly(client)


@asynccontextmanager
async def _async_client(api_key: str | None = None) -> AsyncIterator[AsyncOpenAI]:
    """异步版 _sync_client；不缓存的理由同上。"""
    if not api_key:
        yield get_async_client()
        return
    client = AsyncOpenAI(**_client_kwargs(api_key))
    try:
        yield client
    finally:
        try:
            await client.close()
        except Exception:
            logger.debug("关闭一次性异步 AI 客户端时出错", exc_info=True)


def get_async_client() -> AsyncOpenAI:
    """异步客户端（长文档分块并发调用时使用），只管学校共享密钥。"""
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
    *,
    api_key: str | None = None,
) -> str:
    """统一模型调用入口，返回文本内容。

    json_mode=True 时要求模型返回 JSON 对象；
    thinking=True 用于版式诊断、格式校验等需要推理的复杂任务。
    api_key 是「本次调用用的密钥」：传了就用学生的，没传就用学校的共享密钥。
    它只在这个函数里活一次，不写进模块级变量，也不参与任何缓存键。
    """
    kwargs = _build_kwargs(messages, json_mode, thinking, temperature)
    with _sync_client(api_key) as client:
        slot = _acquire_slot(float(settings.llm_queue_wait_seconds))
        try:
            return _chat_with_retry(client, kwargs, api_key)
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


def _chat_with_retry(
    client: OpenAI, kwargs: dict[str, Any], api_key: str | None = None
) -> str:
    attempts = max(1, int(settings.llm_max_attempts))
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            response = client.chat.completions.create(**kwargs)
            return response.choices[0].message.content or ""
        except Exception as exc:  # 需要把各类 SDK 异常统一成重试
            if is_permanent_error(exc):
                logger.error(
                    "模型调用失败且重试无效，直接报错：%s", scrub_secret(str(exc), api_key)
                )
                raise _permanent_error(exc, api_key) from exc
            last_error = exc
            logger.warning(
                "模型调用失败（第 %d/%d 次）：%s",
                attempt,
                attempts,
                scrub_secret(str(exc), api_key),
            )

    raise _exhausted_error(api_key) from last_error


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
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """JSON 模式调用 + 解析容错。"""
    return extract_json(
        chat(
            messages,
            json_mode=True,
            thinking=thinking,
            temperature=temperature,
            api_key=api_key,
        )
    )


async def achat(
    messages: list[dict[str, Any]],
    json_mode: bool = False,
    thinking: bool = False,
    temperature: float | None = None,
    *,
    api_key: str | None = None,
) -> str:
    """异步版 chat，供 asyncio.gather 并发调用。

    异步调用也必须经过同步 chat 用的那道全局闸门，否则校对任务与体检端点
    各算各的并发，实际模型调用量会突破配置上限。api_key 的语义与 chat 一致。
    """
    kwargs = _build_kwargs(messages, json_mode, thinking, temperature)

    # 排队在别的线程里等，不卡事件循环；取消时名额也会被还掉；拿到的名额在 finally 归还
    slot = await _acquire_slot_async(float(settings.llm_queue_wait_seconds))
    try:
        attempts = max(1, int(settings.llm_max_attempts))
        last_error: Exception | None = None
        async with _async_client(api_key) as client:
            for attempt in range(1, attempts + 1):
                try:
                    response = await client.chat.completions.create(**kwargs)
                    return response.choices[0].message.content or ""
                except Exception as exc:  # 统一重试，但必然失败的不重试
                    if is_permanent_error(exc):
                        logger.error(
                            "异步模型调用失败且重试无效，直接报错：%s",
                            scrub_secret(str(exc), api_key),
                        )
                        raise _permanent_error(exc, api_key) from exc
                    last_error = exc
                    logger.warning(
                        "异步模型调用失败（第 %d/%d 次）：%s",
                        attempt,
                        attempts,
                        scrub_secret(str(exc), api_key),
                    )

        raise _exhausted_error(api_key) from last_error
    finally:
        _release_slot(slot)


async def achat_json(
    messages: list[dict[str, Any]],
    thinking: bool = False,
    temperature: float | None = None,
    *,
    api_key: str | None = None,
) -> dict[str, Any]:
    """异步 JSON 模式调用 + 解析容错。"""
    return extract_json(
        await achat(
            messages,
            json_mode=True,
            thinking=thinking,
            temperature=temperature,
            api_key=api_key,
        )
    )


# 可用性检查用的最小提示：让模型只回一个词，省时间也省钱。
KEY_CHECK_MESSAGES = [
    {"role": "user", "content": "这是一次连通性检查，请只回复 OK 两个字母，不要任何其他内容。"}
]


def verify_user_key(api_key: str) -> str:
    """用给定的学生密钥做一次最小调用，失败时抛出可直接展示给学生的中文原因。

    「成功」不看模型回了什么（各家回什么都有可能），只看这次调用有没有被拒：
    能拿到响应就说明密钥、额度、网络这条链路是通的。
    """
    return (chat(KEY_CHECK_MESSAGES, temperature=0.0, api_key=api_key) or "").strip()
