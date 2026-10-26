"""墙钟无关性的手动验证插件：把「当前时间」固定到当天的某个时刻。

用途只有一个——证明 `pytest -q` 的绿不依赖「跑用例时几点」。正常跑测试不需要它，
也不会被自动加载（文件名不是 test_*.py，只有显式 `-p` 才会 import）。

用法（在 backend/ 目录）：

    # 默认把当前时间固定为当天 23:30（收单时段 08:00-12:00、12:00-18:00 之外）
    PYTHONPATH=tests python -m pytest -p clock_shift_plugin -q

    # 也可以指定别的时刻
    CLOCK_SHIFT_TIME=09:15 PYTHONPATH=tests python -m pytest -p clock_shift_plugin -q

做法：
1. 把 app.* / tests.* 里 `from datetime import datetime` 绑定到的 `datetime` 换成子类，
   其 `now()` / `today()` / `utcnow()` 返回当天 `CLOCK_SHIFT_TIME`。不动 stdlib 本身。
   子类的元类放行 `isinstance(x, datetime)` 的真实 datetime 实例——库里读出来的对象
   是真实类，业务代码里的类型判断不能因此把它们全跳过。
2. 同时替换 `jwt.api_jwt` / `jwt.api_jws` 里的 `datetime`：pyjwt 按自己的
   `datetime.now()` 校验 iat/exp，不换就会把「未来签发的 token」判成无效，
   整片接口用例会以 40101 假红。

注意：只固定 Python 侧的「现在」，数据库的 `NOW()` / `CURRENT_TIMESTAMP` 仍是真实时间
（除非另改 `@@session.time_zone`）。所以选定的时刻应**不晚于**真实时间，否则靠
数据库默认时间戳落库的行会「来自未来」，把它们当历史样本查询的用例会假红。
默认的 23:30 满足这一点（除非本地已经过了 23:30，那就换 06:00 这类更早的时刻）。
"""

from __future__ import annotations

import datetime as _dt
import os
import sys

_DEFAULT_TIME = "23:30"
_MATCHED_ROOTS = ("app", "tests")
_EXTRA_MODULES = ("jwt.api_jwt", "jwt.api_jws")


def _target_hour_minute() -> tuple[int, int]:
    raw = os.environ.get("CLOCK_SHIFT_TIME", _DEFAULT_TIME)
    hour, minute = raw.split(":")
    return int(hour), int(minute)


def _fake_local_naive() -> _dt.datetime:
    """当天的目标时刻（用真实日期的年/月/日）。"""
    hour, minute = _target_hour_minute()
    real = _dt.datetime.now()
    return _dt.datetime(real.year, real.month, real.day, hour, minute, 0, 0)


class _FakeMeta(type):
    """放行真实 datetime 实例与子类的 isinstance / issubclass 判断。"""

    def __instancecheck__(cls, obj) -> bool:
        return isinstance(obj, _dt.datetime)

    def __subclasscheck__(cls, sub) -> bool:
        return issubclass(sub, _dt.datetime)


class _FakeDatetime(_dt.datetime, metaclass=_FakeMeta):
    @classmethod
    def now(cls, tz=None):
        fake = _fake_local_naive()
        if tz is None:
            return fake
        return fake.astimezone().astimezone(tz)

    @classmethod
    def today(cls):
        return _fake_local_naive()

    @classmethod
    def utcnow(cls):
        return _fake_local_naive().astimezone(_dt.UTC).replace(tzinfo=None)


def _patch() -> int:
    """把已 import 的业务/测试模块里的 datetime 换成假时钟，返回替换的模块数。"""
    patched = 0
    for name, module in list(sys.modules.items()):
        if module is None:
            continue
        root = name.split(".")[0]
        if not (name in _EXTRA_MODULES or root in _MATCHED_ROOTS or root == "conftest"):
            continue
        if getattr(module, "datetime", None) is _dt.datetime:
            module.datetime = _FakeDatetime
            patched += 1
    return patched


def pytest_configure(config) -> None:
    _patch()


def pytest_collection_finish(session) -> None:
    # 收集阶段会 import 新的测试模块
    _patch()


def pytest_runtest_setup(item) -> None:
    # 兜住用例执行期才 import 的业务模块
    _patch()
