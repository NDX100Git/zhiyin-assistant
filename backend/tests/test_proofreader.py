"""错别字检查（阶段 5-C）测试：分块、清洗、合并、异步任务与进度。"""

from __future__ import annotations

import time

from app.services import proofreader, task_registry


def test_按行聚合分块且不超长():
    text = "\n".join(f"第{i}行内容" + "字" * 60 for i in range(200))
    chunks = proofreader.split_text(text, chunk_chars=500, min_chunk_chars=50)

    assert len(chunks) > 1
    assert all(len(chunk) <= 560 for chunk in chunks)  # 允许一行溢出
    assert sum(len(chunk) for chunk in chunks) >= 0.8 * len(text.replace("\n", ""))


def test_超长单行按窗口切开():
    text = "字" * 1200
    chunks = proofreader.split_text(text, chunk_chars=500, min_chunk_chars=50)
    assert len(chunks) == 3
    assert all(len(chunk) <= 500 for chunk in chunks)


def test_丢弃没有原文或没有实际改动的条目():
    raw = {
        "issues": [
            {"type": "typo", "severity": "high", "original": "实验结杲表明", "suggestion": "实验结果表明", "reason": "形近字"},
            {"type": "typo", "severity": "high", "original": "", "suggestion": "改一下", "reason": "空原文"},
            {"type": "typo", "severity": "low", "original": "同一句话", "suggestion": "同一句话", "reason": "无改动"},
            {"type": "punctuation", "severity": "weird", "original": "中文,逗号", "suggestion": "中文，逗号", "reason": "全角标点"},
        ]
    }
    issues = proofreader.normalize_issues(raw, chunk_index=0)

    assert len(issues) == 2
    assert issues[0]["original"] == "实验结杲表明"
    assert issues[0]["severity"] == "high"
    assert issues[1]["severity"] == "medium"  # 非法严重程度回退
    assert all(issue["chunk"] == 1 for issue in issues)


def test_合并去重并排序():
    chunk_results = [
        [
            {"chunk": 1, "type": "typo", "severity": "medium", "location": "第1段", "original": "A", "suggestion": "a", "reason": "", "source": "ai"},
        ],
        [
            {"chunk": 2, "type": "typo", "severity": "high", "location": "第9段", "original": "B", "suggestion": "b", "reason": "", "source": "ai"},
            {"chunk": 2, "type": "typo", "severity": "medium", "location": "第1段", "original": "A", "suggestion": "a", "reason": "", "source": "ai"},
        ],
    ]
    merged = proofreader.merge_issues(chunk_results)

    assert len(merged) == 2
    assert merged[0]["original"] == "B"  # high 排在前
    assert [item["id"] for item in merged] == ["t1", "t2"]


def test_异步任务返回进度与结果(db_session, session_factory, monkeypatch):
    parsed = {
        "page_count": 2,
        "pages": [{"index": 1, "text": "实验结杲表明，该方法有效。" * 30, "images": []}],
    }
    monkeypatch.setattr("app.services.proofreader.parse_document", lambda db, key: parsed)

    async def fake_achat_json(messages, thinking=False, temperature=None):
        return {
            "issues": [
                {
                    "type": "typo",
                    "severity": "high",
                    "location": "第 1 段",
                    "original": "实验结杲表明",
                    "suggestion": "实验结果表明",
                    "reason": "「杲」应为「果」",
                }
            ]
        }

    monkeypatch.setattr("app.services.proofreader.achat_json", fake_achat_json)

    started = proofreader.start_proofread("files/2026/10/proof.pdf", session_factory=session_factory)
    assert started["task_id"]
    assert started["status"] in {"pending", "running"}

    task = None
    for _ in range(60):
        progress = proofreader.get_progress(started["task_id"])
        if progress["status"] in {task_registry.STATUS_DONE, task_registry.STATUS_FAILED}:
            task = progress
            break
        time.sleep(0.2)

    assert task is not None, "任务未在超时时间内结束"
    assert task["status"] == task_registry.STATUS_DONE, task
    assert task["percent"] == 100

    result = task["result"]
    assert result["issue_count"] == 1
    assert result["issues"][0]["original"] == "实验结杲表明"
    assert result["by_severity"]["高"] == 1
    assert "共检查" in result["summary"]


def test_查询不存在的任务报业务错误():
    import pytest

    from app.utils.exceptions import BusinessError

    with pytest.raises(BusinessError):
        proofreader.get_progress("not-a-real-task")


def test_校对分块并发与全局闸门口径对齐():
    """配置必须钉住「两个校对任务 × 每任务 2 路 = 全局 4 个名额」。"""
    from app.config import get_ai_rules, settings

    rules = get_ai_rules()
    assert "max_concurrency" not in rules  # 顶层重复配置已删，只留 proofread 下这一份
    assert rules["proofread"]["max_concurrency"] == 2
    assert settings.llm_max_concurrency == 4
    assert rules["proofread"]["max_concurrency"] * 2 == settings.llm_max_concurrency
    # 校对任务本身最多 2 个同时跑，乘每任务 2 路正好是全局 4 个名额
    assert proofreader._executor._max_workers == 2


def test_两个校对任务并发时不超过全局闸门(monkeypatch):
    """两个任务同时跑，分块请求叠加也不超过全局名额；单任务不超过配置路数。"""
    import asyncio
    import threading
    from collections import defaultdict

    from app.config import get_ai_rules, settings

    rules = get_ai_rules()
    per_task_limit = int(rules["proofread"]["max_concurrency"])
    chunks = [f"第{i}段正文" + "字" * 60 for i in range(4)]
    lock = threading.Lock()
    active = 0
    peak = 0
    per_active: dict[str, int] = defaultdict(int)
    per_peak: dict[str, int] = defaultdict(int)

    async def fake_achat_json(messages, thinking=False, temperature=None):
        nonlocal active, peak
        name = threading.current_thread().name
        with lock:
            active += 1
            peak = max(peak, active)
            per_active[name] += 1
            per_peak[name] = max(per_peak[name], per_active[name])
        try:
            await asyncio.sleep(0.05)
            return {"issues": []}
        finally:
            with lock:
                active -= 1
                per_active[name] -= 1

    monkeypatch.setattr("app.services.proofreader.achat_json", fake_achat_json)

    tasks = [task_registry.create_task("proofread") for _ in range(2)]
    threads = [
        threading.Thread(
            target=lambda task_id=task.task_id: asyncio.run(
                proofreader._run_chunks(task_id, chunks)
            ),
            name=f"proof-{index}",
        )
        for index, task in enumerate(tasks)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert all(value <= per_task_limit for value in per_peak.values())
    assert peak <= settings.llm_max_concurrency
    assert peak >= 2  # 两个任务确实同时跑起来了，不是串行
