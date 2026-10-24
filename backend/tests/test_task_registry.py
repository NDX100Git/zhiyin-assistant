"""任务进度表（task_registry）的归属校验。

校对结果里是论文原文片段，轮询接口只能让发起任务的本人看到：拿到别人的 task_id
也必须读不到，且与「任务不存在」返回同一结果，避免泄露他人任务是否存在。

纯内存实现，不依赖数据库与对象存储。
"""

from __future__ import annotations

from app.services import task_registry


def test_本人能取到自己的任务():
    task = task_registry.create_task("proofread", owner="42")
    assert task_registry.get_task(task.task_id, "42") is task


def test_他人取不到别人的任务():
    task = task_registry.create_task("proofread", owner="42")
    assert task_registry.get_task(task.task_id, "43") is None
    assert task_registry.get_task("not-a-real-task", "43") is None


def test_内部流程不传owner仍可取到():
    """后台 worker 自己更新进度时不带 owner。"""
    task = task_registry.create_task("proofread", owner="42")
    assert task_registry.get_task(task.task_id) is task


def test_没记owner的任务不被具体用户读到():
    task = task_registry.create_task("proofread")
    assert task_registry.get_task(task.task_id, "42") is None


def test_对外输出不含owner():
    task = task_registry.create_task("proofread", owner="42")
    assert "owner" not in task.to_dict()
