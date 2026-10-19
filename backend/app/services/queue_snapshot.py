"""排队快照的短时缓存：列表请求复用同一份队列位置与前序页数。

为什么需要：一万单排队时，每个列表请求都要把整队（一万行）读一遍、再在 Python 里
算前缀和，实测单实例只有 61.4 次/秒（见 scripts/perf_results/）。把整队位置做成
一份快照在请求之间复用，队列长度就不再计入单次请求的成本。

有效期（`config/order_rules.json` 的 `wait.queue_snapshot_seconds`，默认 1.5 秒）
只是兜底。会改变队列的动作（下单、支付、取消、换文件确认、开始打印、完成打印、
异常单恢复排队）都必须调 `invalidate()`，旧快照立即作废，学生不会看到过期位置。
"""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass

from app.config import get_order_rules

DEFAULT_TTL_SECONDS = 1.5


@dataclass(frozen=True)
class QueueSnapshot:
    """某一刻的队列快照，构建后不再改动，可安全地被多个线程共享。"""

    # 订单 id -> 前面还有多少单（不含自己）
    positions: Mapping[int, int]
    # 前缀和：第 i 单之前（不含第 i 单）的累计页数，长度 = 队列长度 + 1
    prefix_pages: tuple[int, ...]
    total: int
    # 近 N 单的「秒/页」；没有历史数据时为 None，按配置的默认单均耗时估算
    seconds_per_page: float | None
    # 读库开始那一刻的 monotonic 时间，有效期从这里算起
    built_at: float

    @property
    def total_pages(self) -> int:
        """整队页数合计，新下单的预计等待用它。"""
        return self.prefix_pages[-1]

    def position_of(self, order_id: int) -> int | None:
        """排在 order_id 前面的单数；不在当前队列里返回 None。"""
        return self.positions.get(order_id)


_lock = threading.Lock()
_snapshot: QueueSnapshot | None = None
_generation = 0


def ttl_seconds() -> float:
    """快照有效期，单位秒；配 0 表示每次都重新读库。"""
    wait = get_order_rules().get("wait") or {}
    return float(wait.get("queue_snapshot_seconds", DEFAULT_TTL_SECONDS))


def current(now: float | None = None) -> QueueSnapshot | None:
    """取仍在有效期内的快照；没有或已过期返回 None。"""
    moment = time.monotonic() if now is None else now
    with _lock:
        snapshot = _snapshot
    if snapshot is None:
        return None
    if moment - snapshot.built_at > ttl_seconds():
        return None
    return snapshot


def generation() -> int:
    """当前的失效代数。构建快照前记下，写回时用它判断构建期间有没有失效过。"""
    with _lock:
        return _generation


def store(snapshot: QueueSnapshot, built_generation: int) -> bool:
    """写回快照；构建期间发生过失效就丢弃，避免把旧队列又缓存一遍。

    读队列的那条 SQL 在事务里看到的是提交后的数据，而失效发生在写事务提交之前，
    所以「先失效、后写回」的旧快照必须丢掉，否则会在有效期里继续被读到。
    """
    global _snapshot
    with _lock:
        if built_generation != _generation:
            return False
        _snapshot = snapshot
        return True


def invalidate() -> None:
    """队列有变化时调用：旧快照立刻作废，下一个请求重新读库。"""
    global _snapshot, _generation
    with _lock:
        _snapshot = None
        _generation += 1


def reset() -> None:
    """测试用：丢掉快照并清空代数。"""
    global _snapshot, _generation
    with _lock:
        _snapshot = None
        _generation = 0
