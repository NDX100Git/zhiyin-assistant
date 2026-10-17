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
