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


# ---------------------------------------------------------------- 过期清理
def test_过期任务连同结果一起消失():
    """已完成的任务带着结果（论文原文片段）驻留内存，过期必须连结果一起清掉。

    早期实现「没超过上限就直接 return」，几十条任务的小规模下 TTL 等于没生效。
    """
    task = task_registry.create_task("proofread", owner="42")
    task_registry.finish(task.task_id, {"issues": [{"original": "论文原文片段"}]})
    stale = task_registry.get_task(task.task_id)
    assert stale is not None and stale.result is not None

    stale.updated_at -= task_registry.TASK_TTL_SECONDS + 1
    # 新建任务会顺带触发一次清理
    task_registry.create_task("proofread", owner="43")

    assert task_registry.get_task(task.task_id) is None
    assert task_registry.get_task(task.task_id, "42") is None


def test_没过期的任务与结果都留着():
    task = task_registry.create_task("proofread", owner="42")
    task_registry.finish(task.task_id, {"issues": [{"original": "刚跑完"}]})

    task_registry.create_task("proofread", owner="43")  # 触发清理

    kept = task_registry.get_task(task.task_id, "42")
    assert kept is not None
    assert kept.result == {"issues": [{"original": "刚跑完"}]}
