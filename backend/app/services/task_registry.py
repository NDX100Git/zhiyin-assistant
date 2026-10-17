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


def create_task(kind: str, total: int = 0, message: str = "") -> TaskState:
    task = TaskState(task_id=uuid.uuid4().hex, kind=kind, total=total, message=message)
    with _lock:
        _tasks[task.task_id] = task
        _purge_locked()
    return task


def get_task(task_id: str) -> TaskState | None:
    with _lock:
        return _tasks.get(task_id)


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
    """清理过期任务，避免内存无限增长。"""
    if len(_tasks) <= MAX_KEEP_TASKS:
        return
    now = time.time()
    expired = [
        task_id
        for task_id, task in _tasks.items()
        if now - task.updated_at > TASK_TTL_SECONDS
    ]
    for task_id in expired:
        _tasks.pop(task_id, None)

    if len(_tasks) > MAX_KEEP_TASKS:
        oldest = sorted(_tasks.values(), key=lambda item: item.updated_at)
        for task in oldest[: len(_tasks) - MAX_KEEP_TASKS]:
            _tasks.pop(task.task_id, None)
