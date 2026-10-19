"""AI 调用的隔离测试：并发闸门、早失败、重试次数。

模型接口慢，一个请求能占住线程几十秒。不设闸门时，几十个人同时点体检会把登录、
下单这些快接口一起拖死——这类问题在压测里表现为「全站超时」，但根因只有一个。
这里用替身客户端把闸门行为钉住，不花模型的钱。
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

from app.config import settings
from app.services import llm
from app.utils.exceptions import BusinessError


class _Counter:
    """同步与异步假客户端共用的在途计数，用来验证它们共用同一道闸门。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.active = 0
        self.peak = 0

    def enter(self) -> None:
        with self._lock:
            self.active += 1
            self.peak = max(self.peak, self.active)

    def exit(self) -> None:
        with self._lock:
            self.active -= 1


class _FakeCompletions:
    def __init__(self, delay: float, fail_times: int = 0, counter: _Counter | None = None) -> None:
        self.delay = delay
        self.fail_times = fail_times
        self.counter = counter or _Counter()
        self.calls = 0
        self._lock = threading.Lock()
        self.peak = 0
        self._active = 0

    def create(self, **kwargs):
        with self._lock:
            self.calls += 1
            self._active += 1
            self.peak = max(self.peak, self._active)
            attempt = self.calls
        self.counter.enter()
        try:
            time.sleep(self.delay)
            if attempt <= self.fail_times:
                raise RuntimeError("模拟模型接口失败")
            return type(
                "Response",
                (),
                {"choices": [type("Choice", (), {"message": type("Msg", (), {"content": "好的"})()})()]},
            )()
        finally:
            self.counter.exit()
            with self._lock:
                self._active -= 1


class _FakeAsyncCompletions:
    def __init__(self, delay: float, counter: _Counter) -> None:
        self.delay = delay
        self.counter = counter
        self.calls = 0

    async def create(self, **kwargs):
        self.calls += 1
        self.counter.enter()
        try:
            await asyncio.sleep(self.delay)
            return type(
                "Response",
                (),
                {"choices": [type("Choice", (), {"message": type("Msg", (), {"content": "好的"})()})()]},
            )()
        finally:
            self.counter.exit()


class _FakeClient:
    def __init__(self, completions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()


class _FakeAsyncClient:
    def __init__(self, completions) -> None:
        self.chat = type("Chat", (), {"completions": completions})()


@pytest.fixture()
def fake_client(monkeypatch):
    def install(delay: float = 0.0, fail_times: int = 0) -> _FakeCompletions:
        completions = _FakeCompletions(delay=delay, fail_times=fail_times)
        monkeypatch.setattr(llm, "get_client", lambda: _FakeClient(completions))
        monkeypatch.setattr(llm, "is_configured", lambda: True)
        return completions

    return install


def _messages() -> list[dict[str, str]]:
    return [{"role": "user", "content": "你好"}]


def test_正常调用返回内容(fake_client) -> None:
    fake_client()
    assert llm.chat(_messages()) == "好的"


def test_并发不超过闸门大小(monkeypatch, fake_client) -> None:
    completions = fake_client(delay=0.15)
    monkeypatch.setattr(settings, "llm_max_concurrency", 2)
    monkeypatch.setattr(settings, "llm_queue_wait_seconds", 5.0)

    threads = [threading.Thread(target=llm.chat, args=(_messages(),)) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert completions.calls == 6
    assert completions.peak <= 2


def test_闸门满且等待超时直接拒绝(monkeypatch, fake_client) -> None:
    fake_client(delay=0.6)
    monkeypatch.setattr(settings, "llm_max_concurrency", 1)
    monkeypatch.setattr(settings, "llm_queue_wait_seconds", 0.1)

    errors: list[BusinessError] = []

    def worker() -> None:
        try:
            llm.chat(_messages())
        except BusinessError as error:
            errors.append(error)

    first = threading.Thread(target=worker)
    first.start()
    time.sleep(0.15)  # 让第一个请求先拿到名额
    second = threading.Thread(target=worker)
    second.start()

    first.join()
    second.join()

    assert len(errors) == 1
    assert errors[0].code == 50034
    assert "稍后重试" in str(errors[0])


def test_失败按配置次数重试后报错(monkeypatch, fake_client) -> None:
    completions = fake_client(fail_times=99)
    monkeypatch.setattr(settings, "llm_max_attempts", 2)

    with pytest.raises(BusinessError) as excinfo:
        llm.chat(_messages())

    assert completions.calls == 2
    assert excinfo.value.code == 50031


def test_重试成功后不再报错(monkeypatch, fake_client) -> None:
    completions = fake_client(fail_times=1)
    monkeypatch.setattr(settings, "llm_max_attempts", 2)

    assert llm.chat(_messages()) == "好的"
    assert completions.calls == 2


def test_闸门名额用完会归还(monkeypatch, fake_client) -> None:
    fake_client()
    monkeypatch.setattr(settings, "llm_max_concurrency", 1)

    for _ in range(5):
        assert llm.chat(_messages()) == "好的"


def test_同步与异步共用同一道闸门(monkeypatch) -> None:
    counter = _Counter()
    sync_completions = _FakeCompletions(delay=0.2, counter=counter)
    async_completions = _FakeAsyncCompletions(delay=0.2, counter=counter)
    monkeypatch.setattr(llm, "get_client", lambda: _FakeClient(sync_completions))
    monkeypatch.setattr(llm, "get_async_client", lambda: _FakeAsyncClient(async_completions))
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(settings, "llm_max_concurrency", 2)
    monkeypatch.setattr(settings, "llm_queue_wait_seconds", 5.0)

    async def main() -> None:
        tasks = [asyncio.create_task(llm.achat(_messages())) for _ in range(2)]
        await asyncio.sleep(0)
        threads = [threading.Thread(target=llm.chat, args=(_messages(),)) for _ in range(2)]
        for thread in threads:
            thread.start()
        await asyncio.gather(*tasks)
        for thread in threads:
            thread.join()

    asyncio.run(main())

    assert sync_completions.calls + async_completions.calls == 4
    assert counter.peak <= 2

def test_异步闸门满时也拒绝(monkeypatch) -> None:
    counter = _Counter()
    async_completions = _FakeAsyncCompletions(delay=0.4, counter=counter)
    monkeypatch.setattr(llm, "get_async_client", lambda: _FakeAsyncClient(async_completions))
    monkeypatch.setattr(llm, "is_configured", lambda: True)
    monkeypatch.setattr(settings, "llm_max_concurrency", 1)
    monkeypatch.setattr(settings, "llm_queue_wait_seconds", 0.1)

    async def main() -> None:
        first = asyncio.create_task(llm.achat(_messages()))
        await asyncio.sleep(0.05)
        with pytest.raises(BusinessError) as excinfo:
            await llm.achat(_messages())
        assert excinfo.value.code == 50034
        await first

    asyncio.run(main())
