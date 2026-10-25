"""内存任务进度表（V1 简化实现）。

长耗时 AI 任务（如错别字检查）在前端用轮询查看进度，不使用 WebSocket。
V1 是单进程内存实现；V2 若要多实例部署，按同样接口换成 Redis 即可，业务代码不用改。
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_DONE = "done"
STATUS_FAILED = "failed"

MAX_KEEP_TASKS = 50
TASK_TTL_SECONDS = 3600


@dataclass
class TaskState:
    task_id: str
    kind: str
    # 发起人标识（str(user.id)）。校对结果里是论文原文片段，只能让本人看到，
    # 所以轮询接口必须校验 owner；to_dict 不输出它，避免把用户标识回给前端。
    owner: str = ""
    status: str = STATUS_PENDING
    total: int = 0
    done: int = 0
    message: str = ""
    result: dict[str, Any] | None = None
    error: str | None = None
    errors: list[str] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)

    @property
    def percent(self) -> int:
        if self.status == STATUS_DONE:
            return 100
        if self.total <= 0:
            return 0
        return min(99, int(self.done * 100 / self.total))

    def to_dict(self, with_result: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "task_id": self.task_id,
            "kind": self.kind,
            "status": self.status,
            "total": self.total,
            "done": self.done,
            "percent": self.percent,
            "message": self.message,
        }
        if with_result and self.result is not None:
            data["result"] = self.result
        if self.error:
            data["error"] = self.error
        if self.errors:
            data["errors"] = self.errors
        return data


_tasks: dict[str, TaskState] = {}
_lock = threading.Lock()


def create_task(kind: str, total: int = 0, message: str = "", owner: str = "") -> TaskState:
    task = TaskState(
        task_id=uuid.uuid4().hex, kind=kind, owner=owner, total=total, message=message
    )
    with _lock:
        _tasks[task.task_id] = task
        _purge_locked()
    return task


def get_task(task_id: str, owner: str | None = None) -> TaskState | None:
    """按 task_id 取任务。

    传了 owner 就校验归属，不匹配时返回 None（与「不存在」同一结果，避免泄露
    别人的任务是否存在）。不传 owner 表示内部流程（如后台 worker 自己更新进度）。
    """
    with _lock:
        task = _tasks.get(task_id)
    if task is None:
        return None
    if owner is not None and task.owner != owner:
        return None
    return task


def update(task_id: str, **fields: Any) -> TaskState | None:
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return None
        for key, value in fields.items():
            setattr(task, key, value)
        task.updated_at = time.time()
        return task


def advance(task_id: str, message: str | None = None) -> TaskState | None:
    """完成一个分块，进度 +1。"""
    with _lock:
        task = _tasks.get(task_id)
        if task is None:
            return None
        task.done += 1
        task.status = STATUS_RUNNING
        if message:
            task.message = message
        task.updated_at = time.time()
        return task


def add_error(task_id: str, message: str) -> None:
    with _lock:
        task = _tasks.get(task_id)
        if task is not None:
            task.errors.append(message)
            task.updated_at = time.time()


def finish(task_id: str, result: dict[str, Any], message: str = "检查完成") -> None:
    update(task_id, status=STATUS_DONE, result=result, message=message)


def fail(task_id: str, error: str) -> None:
    update(task_id, status=STATUS_FAILED, error=error, message=error)


def _purge_locked() -> None:
    """按 TTL 清理过期任务，总量超上限时再淘汰最久未更新的。

    早期实现是「任务数还没超过 MAX_KEEP_TASKS 就直接 return，且过期判断还要
    同时满足」——几十条任务的小规模下 TTL 等于没生效，已完成的任务带着
    result（校对结果里有论文原文片段）一直驻留到进程重启。
    """
    now = time.time()
    for task_id in [
        task_id for task_id, task in _tasks.items()
        if now - task.updated_at > TASK_TTL_SECONDS
    ]:
        _tasks.pop(task_id, None)

    overflow = len(_tasks) - MAX_KEEP_TASKS
    if overflow <= 0:
        return
    # 只淘汰已结束的任务，运行中的不动（它们的数量由并发上限约束）
    idle = sorted(
        (task.updated_at, task_id)
        for task_id, task in _tasks.items()
        if task.status in (STATUS_DONE, STATUS_FAILED)
    )
    for _, task_id in idle[:overflow]:
        _tasks.pop(task_id, None)

    if len(_tasks) > MAX_KEEP_TASKS:
        oldest = sorted(_tasks.values(), key=lambda item: item.updated_at)
        for task in oldest[: len(_tasks) - MAX_KEEP_TASKS]:
            _tasks.pop(task.task_id, None)
