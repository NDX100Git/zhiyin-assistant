"""学生自带 AI 密钥的测试：用谁的 key、不落库、不进日志、不串用、不回显。

产品口径（已定）：学校有一把服务器上的大 key，学生可以另外填自己的 key；学生的 key
只在请求头 `X-LLM-Api-Key` 里带一次，后端用完即弃 —— 不写数据库、不写日志、不写任务
结果、不进 URL 查询参数、不进响应体；学生 key 优先于学校 key；学生 key 失效时报明确
错误，**不许静默回退**到学校 key。

这里的断言都盯真实行为：客户端确实是用哪把 key 建的、失败后到底调了几次模型、库里
和日志里有没有那串明文。
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import text

from app.config import settings
from app.models.base import Base
from app.routers import ai as ai_router
from app.services import llm, proofreader, task_registry
from app.utils.exceptions import BusinessError

STUDENT_KEY = "sk-student-0123456789abcdef"
OTHER_KEY = "sk-another-fedcba9876543210"
SCHOOL_KEY = "sk-school-shared-9999"


# ---------------------------------------------------------------- 替身客户端
class _Completions:
    """记录每次调用用的密钥，按需返回内容或抛错。"""

    def __init__(self, api_key: str, content: str, error_factory=None, delay: float = 0.0) -> None:
        self.api_key = api_key
        self.content = content
        self.error_factory = error_factory
        self.delay = delay
        self.calls = 0
        self._lock = threading.Lock()

    def create(self, **kwargs):
        with self._lock:
            self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        if self.error_factory is not None:
            raise self.error_factory()
        return type(
            "Response",
            (),
            {
                "choices": [
                    type("Choice", (), {"message": type("Msg", (), {"content": self.content})()})()
                ]
            },
        )()


class _Client:
    """和 OpenAI 客户端形状一致的最小替身；关闭与否也记下来。"""

    def __init__(self, completions: _Completions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _AsyncCompletions:
    def __init__(self, api_key: str, content: str) -> None:
        self.api_key = api_key
        self.content = content
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        return type(
            "Response",
            (),
            {
                "choices": [
                    type("Choice", (), {"message": type("Msg", (), {"content": self.content})()})()
                ]
            },
        )()


class _AsyncClient:
    def __init__(self, completions: _AsyncCompletions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()
        self.closed = False

    async def close(self) -> None:
        self.closed = True


@pytest.fixture()
def fake_openai(monkeypatch):
    """把 OpenAI 换成替身，记下每次是用哪把 key 建的客户端。"""

    def install(content: str = "OK", error_factory=None, delay: float = 0.0) -> list[_Client]:
        built: list[_Client] = []

        def factory(**kwargs) -> _Client:
            client = _Client(
                _Completions(kwargs["api_key"], content, error_factory, delay=delay)
            )
            built.append(client)
            return client

        monkeypatch.setattr(llm, "OpenAI", factory)
        return built

    return install


@pytest.fixture()
def school_key(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_key", SCHOOL_KEY)
    monkeypatch.setattr(settings, "llm_max_attempts", 2)
    monkeypatch.setattr(settings, "llm_queue_wait_seconds", 5.0)
    return SCHOOL_KEY


def _api_error(status: int) -> Exception:
    """和真实 SDK 一样带 status_code 的异常，不真的发请求。"""
    import httpx
    import openai

    request = httpx.Request("POST", "https://api.deepseek.com/chat/completions")
    response = httpx.Response(status, request=request)
    names = {401: "AuthenticationError", 403: "PermissionDeniedError", 500: "InternalServerError"}
    return getattr(openai, names.get(status, "APIStatusError"))(
        "Incorrect API key provided: " + STUDENT_KEY, response=response, body=None
    )


# ---------------------------------------------------------------- 用谁的 key
def test_带学生密钥时构造客户端用的是学生密钥(fake_openai, school_key) -> None:
    built = fake_openai("好的")

    assert llm.chat([{"role": "user", "content": "你好"}], api_key=STUDENT_KEY) == "好的"

    assert len(built) == 1
    assert built[0].chat.completions.api_key == STUDENT_KEY
    assert built[0].chat.completions.api_key != school_key


def test_不带学生密钥时用学校共享密钥(fake_openai, school_key) -> None:
    built = fake_openai("好的")

    llm.chat([{"role": "user", "content": "你好"}])

    assert [client.chat.completions.api_key for client in built] == [school_key]


def test_学生密钥失效时报明确错误且不回退学校密钥(fake_openai, school_key) -> None:
    """学生 key 失效：一次就报错，说清「清除后改用学校共享」，绝不拿学校 key 重试。"""
    built = fake_openai(error_factory=lambda: _api_error(401))

    with pytest.raises(BusinessError) as excinfo:
        llm.chat([{"role": "user", "content": "你好"}], api_key=STUDENT_KEY)

    assert excinfo.value.code == 50035
    assert "清除" in excinfo.value.message
    assert "学校共享密钥" in excinfo.value.message
    # 只建过一个客户端、只调过一次：回退的话会多出一把学校 key 的调用
    assert len(built) == 1
    assert [client.chat.completions.api_key for client in built] == [STUDENT_KEY]
    assert built[0].chat.completions.calls == 1


def test_学生密钥带来的异步调用同样不回退(monkeypatch, school_key) -> None:
    import asyncio

    built: list[_AsyncClient] = []

    def factory(**kwargs) -> _AsyncClient:
        client = _AsyncClient(_AsyncCompletions(kwargs["api_key"], "好的"))
        built.append(client)
        return client

    monkeypatch.setattr(llm, "AsyncOpenAI", factory)

    async def scenario() -> str:
        return await llm.achat([{"role": "user", "content": "你好"}], api_key=STUDENT_KEY)

    assert asyncio.run(scenario()) == "好的"
    assert [client.chat.completions.api_key for client in built] == [STUDENT_KEY]
    assert built[0].closed is True, "用完即弃：异步客户端也要关掉"


def test_学生密钥的客户端不缓存且用完即关(fake_openai, school_key) -> None:
    """同一个小 key 连用两次也要建两个客户端、两个都关掉。

    缓存意味着进程里长期留着一把学生凭据，而且缓存键得是任意学生输入的字符串。
    """
    built = fake_openai("好的")

    for _ in range(2):
        llm.chat([{"role": "user", "content": "你好"}], api_key=STUDENT_KEY)

    assert len(built) == 2
    assert all(client.closed for client in built)


def test_并发两个请求各带不同学生密钥互不串用(fake_openai, school_key) -> None:
    """两把密钥同时在跑：各自用自己的，不会因为共享状态串到对方头上。"""
    built: list[_Client] = []
    lock = threading.Lock()

    def factory(**kwargs) -> _Client:
        completions = _Completions(kwargs["api_key"], f"echo:{kwargs['api_key']}", delay=0.2)
        client = _Client(completions)
        with lock:
            built.append(client)
        return client

    llm.OpenAI = factory  # type: ignore[assignment]

    results: dict[str, str] = {}

    def worker(name: str, key: str) -> None:
        results[name] = llm.chat([{"role": "user", "content": "你好"}], api_key=key)

    threads = [
        threading.Thread(target=worker, args=("a", STUDENT_KEY)),
        threading.Thread(target=worker, args=("b", OTHER_KEY)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert results == {"a": f"echo:{STUDENT_KEY}", "b": f"echo:{OTHER_KEY}"}
    assert sorted(client.chat.completions.api_key for client in built) == sorted(
        [STUDENT_KEY, OTHER_KEY]
    )


# ---------------------------------------------------------------- 脱敏
def test_脱敏只留前四后四且太短就全遮() -> None:
    assert llm.mask_key(STUDENT_KEY) == "sk-s****cdef"
    assert llm.mask_key("sk-1234") == "*******", "长度不足 4+4 一律全遮"
    assert llm.mask_key("") == ""
    assert llm.mask_key(None) == ""


def test_错误信息与日志里只有脱敏后的密钥(fake_openai, school_key, caplog) -> None:
    """SDK 的报错原文会把 key 完整带出来，日志与对外错误都必须换成前 4 后 4。"""
    fake_openai(error_factory=lambda: _api_error(401))

    with caplog.at_level(logging.INFO):
        with pytest.raises(BusinessError) as excinfo:
            llm.chat([{"role": "user", "content": "你好"}], api_key=STUDENT_KEY)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert STUDENT_KEY not in text, f"日志里出现了明文密钥：{text}"
    assert llm.mask_key(STUDENT_KEY) in text
    assert STUDENT_KEY not in excinfo.value.message
    assert llm.mask_key(STUDENT_KEY) in excinfo.value.message


def test_可重试失败后日志里的密钥也是脱敏的(fake_openai, school_key, caplog) -> None:
    """500 这类会重试的失败同样会把 SDK 原文写进警告日志。"""
    fake_openai(error_factory=lambda: _api_error(500))

    with caplog.at_level(logging.INFO):
        with pytest.raises(BusinessError):
            llm.chat([{"role": "user", "content": "你好"}], api_key=STUDENT_KEY)

    text = "\n".join(record.getMessage() for record in caplog.records)
    assert STUDENT_KEY not in text
    assert llm.mask_key(STUDENT_KEY) in text


# ---------------------------------------------------------------- 请求头与接口
def _login(client, device_id: str) -> dict[str, str]:
    body = client.post("/api/auth/login", json={"device_id": device_id}).json()
    assert body["code"] == 0, body
    return {"Authorization": f"Bearer {body['data']['token']}"}


def _pdf_bytes(path: Path) -> bytes:
    """一页带正文的最小 PDF，够体检解析出页数与文本。"""
    import fitz

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((90, 100), "Abstract")
        page.insert_text((90, 130), "This paper studies formatting.")
        page.insert_text((90, 160), "Keywords: formatting")
        document.save(str(path))
    return path.read_bytes()


@pytest.fixture()
def uploaded_pdf(client, test_storage, tmp_path):
    """上传一份真 PDF 并返回 (headers, file_key)：请求头生效要经过真实入口。"""
    headers = _login(client, "student-key-owner")
    body = client.post(
        "/api/file/upload",
        files={"file": ("论文.pdf", _pdf_bytes(tmp_path / "论文.pdf"), "application/pdf")},
        headers=headers,
    ).json()
    assert body["code"] == 0, body
    return headers, body["data"]["file_key"]


def test_体检接口把请求头里的学生密钥传到模型调用(
    client, uploaded_pdf, fake_openai, school_key
) -> None:
    headers, file_key = uploaded_pdf
    built = fake_openai('{"issues": []}')

    body = client.post(
        "/api/ai/check",
        json={"file_key": file_key, "force": True},
        headers={**headers, llm.USER_KEY_HEADER: STUDENT_KEY},
    ).json()

    assert body["code"] == 0, body
    assert [item.chat.completions.api_key for item in built] == [STUDENT_KEY] * len(built)
    assert built, "体检至少要调一次模型"
    assert STUDENT_KEY not in str(body), "响应体里不能出现密钥"


def test_学生密钥不落数据库也不进报告与任务结果(
    client, uploaded_pdf, fake_openai, school_key, db_session
) -> None:
    """一次带密钥的体检跑完，库里所有表都查不到这串明文。"""
    headers, file_key = uploaded_pdf
    fake_openai('{"issues": []}')

    body = client.post(
        "/api/ai/check",
        json={"file_key": file_key, "force": True},
        headers={**headers, llm.USER_KEY_HEADER: STUDENT_KEY},
    ).json()
    assert body["code"] == 0, body

    db_session.expire_all()
    leaked: list[str] = []
    with db_session.get_bind().connect() as conn:
        for table in Base.metadata.sorted_tables:
            for row in conn.execute(text(f"SELECT * FROM `{table.name}`")).all():
                if STUDENT_KEY in repr(row):
                    leaked.append(table.name)
    assert leaked == [], f"这些表里出现了明文密钥：{leaked}"


def test_key_check成功时不回显密钥(client, fake_openai, school_key) -> None:
    headers = _login(client, "student-key-check-ok")
    built = fake_openai("OK")

    body = client.post(
        "/api/ai/key-check",
        headers={**headers, llm.USER_KEY_HEADER: STUDENT_KEY},
    ).json()

    assert body["code"] == 0, body
    assert body["data"]["ok"] is True
    assert len(built) == 1
    assert built[0].chat.completions.api_key == STUDENT_KEY
    # 连脱敏形式都不回：响应里只有「通 / 不通 + 原因」
    assert STUDENT_KEY not in str(body)
    assert llm.mask_key(STUDENT_KEY) not in str(body)


def test_key_check失败时给出明确中文原因(client, fake_openai, school_key) -> None:
    headers = _login(client, "student-key-check-fail")
    fake_openai(error_factory=lambda: _api_error(401))

    body = client.post(
        "/api/ai/key-check",
        headers={**headers, llm.USER_KEY_HEADER: STUDENT_KEY},
    ).json()

    assert body["code"] == 50035, body
    assert "清除" in body["message"]
    assert "学校共享密钥" in body["message"]
    assert STUDENT_KEY not in body["message"]
    assert llm.mask_key(STUDENT_KEY) in body["message"]


def test_key_check没带密钥时报错(client) -> None:
    headers = _login(client, "student-key-check-missing")

    body = client.post("/api/ai/key-check", headers=headers).json()

    assert body["code"] == 50036, body
    assert llm.USER_KEY_HEADER in body["message"]


def test_key_check的模型调用不阻塞事件循环(monkeypatch) -> None:
    """用 0.2 秒同步等待模拟模型调用，事件循环应照常在 0.05 秒时拿到控制权。"""
    monkeypatch.setattr(settings, "llm_user_key_enabled", True)
    monkeypatch.setattr(
        ai_router,
        "verify_user_key",
        lambda key: time.sleep(0.2) or "OK",
    )

    async def scenario():
        started = time.monotonic()
        task = asyncio.create_task(ai_router.ai_key_check(None, STUDENT_KEY, None))
        await asyncio.sleep(0.05)
        ticked_after = time.monotonic() - started
        body = await task
        return ticked_after, body

    ticked_after, body = asyncio.run(scenario())

    assert ticked_after < 0.15, f"密钥检查阻塞事件循环 {ticked_after:.3f} 秒"
    assert body["data"]["ok"] is True


def test_密钥格式明显不对时就地拒绝不打模型(
    client, uploaded_pdf, fake_openai, school_key
) -> None:
    headers, file_key = uploaded_pdf
    built = fake_openai('{"issues": []}')

    body = client.post(
        "/api/ai/check",
        json={"file_key": file_key, "force": True},
        headers={**headers, llm.USER_KEY_HEADER: "sk-1"},
    ).json()

    assert body["code"] == 50036, body
    assert built == [], "格式不对的密钥不该拿去打模型"


def test_关掉开关后忽略请求头仍用学校共享密钥(
    client, uploaded_pdf, fake_openai, school_key, monkeypatch
) -> None:
    headers, file_key = uploaded_pdf
    monkeypatch.setattr(settings, "llm_user_key_enabled", False)
    built = fake_openai('{"issues": []}')

    body = client.post(
        "/api/ai/check",
        json={"file_key": file_key, "force": True},
        headers={**headers, llm.USER_KEY_HEADER: STUDENT_KEY},
    ).json()

    assert body["code"] == 0, body
    assert [item.chat.completions.api_key for item in built] == [school_key] * len(built)


def test_关掉开关后key_check明确说清不再接受自带密钥(
    client, monkeypatch, school_key
) -> None:
    headers = _login(client, "student-key-check-off")
    monkeypatch.setattr(settings, "llm_user_key_enabled", False)

    body = client.post(
        "/api/ai/key-check",
        headers={**headers, llm.USER_KEY_HEADER: STUDENT_KEY},
    ).json()

    assert body["code"] == 50037, body
    assert STUDENT_KEY not in body["message"]


# ---------------------------------------------------------------- 校对（后台线程）
def test_校对任务把学生密钥交给后台线程且不写进任务状态(
    db_session, session_factory, test_storage, monkeypatch
) -> None:
    """校对是「先返回 task_id、后台线程再调模型」，密钥必须跟着任务进线程。

    但任务状态是要给前端轮询的，绝不能把密钥放进去。
    """
    seen: list[dict] = []

    async def fake_achat_json(messages, thinking=False, temperature=None, *, api_key=None):
        seen.append({"api_key": api_key, "messages": messages})
        return {"issues": []}

    parsed = {
        "page_count": 1,
        "pages": [{"index": 1, "text": "这是一段用来测试分块的中文正文。" * 20}],
    }
    monkeypatch.setattr(proofreader, "parse_document", lambda db, key, force=False: parsed)
    monkeypatch.setattr(proofreader, "achat_json", fake_achat_json)

    task = proofreader.start_proofread(
        "files/2026/10/key.pdf", session_factory, owner="1", api_key=STUDENT_KEY
    )

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        state = task_registry.get_task(task["task_id"])
        if state is not None and state.status in {"done", "failed"}:
            break
        time.sleep(0.05)
    else:  # pragma: no cover - 只在任务卡死时走到
        raise AssertionError("校对任务没有在 10 秒内结束")

    assert seen, "后台线程至少要调一次模型"
    assert {item["api_key"] for item in seen} == {STUDENT_KEY}

    polled = state.to_dict()
    assert STUDENT_KEY not in str(polled), "轮询结果里不能出现密钥"
    assert STUDENT_KEY not in str(state.errors)
