"""错别字与标点检查（阶段 5-C）。

长文档按段落分块（默认 2500 字，见 config/ai_rules.json），用 asyncio.gather 并发调用
（每个任务默认 2 路，两个校对任务并发正好占满全局 4 个名额），每块完成后更新进度供前端轮询。
严格「建议模式」：只报有把握的问题，必须人工确认后才可能进入修复。
"""

from __future__ import annotations

import asyncio
import json
import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any

from app.config import get_ai_rules
from app.database import SessionLocal
from app.services import task_registry
from app.services.doc_parser import get_cached_report, parse_document, save_report
from app.services.llm import achat_json
from app.utils.exceptions import BusinessError

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}

SYSTEM_PROMPT = """你是中文校对助手。请找出文本中的错别字和标点问题。

严格要求：
1. 只报告你非常有把握的错误，不确定的绝对不要报告
2. 不要改动专业术语、公式、人名、机构名、外文词汇
3. 不要因为风格偏好而提出修改
4. 每条必须给出原文片段与建议，供人工确认
5. 只输出合法 JSON，不要任何解释文字"""

OUTPUT_SCHEMA = {
    "issues": [
        {
            "id": "t1",
            "type": "typo",
            "severity": "high",
            "location": "第 2 段",
            "original": "实验结杲表明",
            "suggestion": "实验结果表明",
            "reason": "「杲」应为「果」，形近字错误",
        }
    ]
}

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="proofread")


def split_text(text: str, chunk_chars: int, min_chunk_chars: int) -> list[str]:
    """按行聚合分块；超长行按窗口切开，避免单块过大。"""
    chunks: list[str] = []
    buffer: list[str] = []
    size = 0

    def flush() -> None:
        nonlocal buffer, size
        content = "\n".join(buffer).strip()
        if len(content) >= min_chunk_chars:
            chunks.append(content)
        buffer = []
        size = 0

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line:
            continue
        while len(line) > chunk_chars:
            head, line = line[:chunk_chars], line[chunk_chars:]
            buffer.append(head)
            size += len(head)
            flush()
        buffer.append(line)
        size += len(line)
        if size >= chunk_chars:
            flush()
    flush()
    return chunks


def build_messages(chunk: str) -> list[dict[str, Any]]:
    user_content = (
        f"文本：\n{chunk}\n\n"
        f"输出格式（必须是合法 JSON，没有问题就返回空数组）：\n"
        f"{json.dumps(OUTPUT_SCHEMA, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def normalize_issues(raw: dict, chunk_index: int) -> list[dict]:
    items = raw.get("issues") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []

    issues: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        original = str(item.get("original") or "").strip()
        suggestion = str(item.get("suggestion") or "").strip()
        if not original or not suggestion or original == suggestion:
            continue  # 没有原文片段或没有实际改动的一律丢弃
        severity = str(item.get("severity") or "medium")
        issues.append(
            {
                "chunk": chunk_index + 1,
                "type": str(item.get("type") or "typo"),
                "severity": severity if severity in SEVERITY_ORDER else "medium",
                "location": str(item.get("location") or f"第 {chunk_index + 1} 段"),
                "original": original,
                "suggestion": suggestion,
                "reason": str(item.get("reason") or ""),
                "source": "ai",
            }
        )
    return issues


def merge_issues(chunk_results: list[list[dict]]) -> list[dict]:
    """合并各分块结果并去重（同一处原文只留一条）。"""
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()

    for issues in chunk_results:
        for issue in issues:
            key = (issue["original"], issue["suggestion"])
            if key in seen:
                continue
            seen.add(key)
            merged.append(issue)

    merged.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 9), item["chunk"]))
    for index, issue in enumerate(merged, start=1):
        issue["id"] = f"t{index}"
    return merged


async def _run_chunks(task_id: str, chunks: list[str]) -> list[list[dict]]:
    rules = get_ai_rules()["proofread"]
    semaphore = asyncio.Semaphore(int(rules.get("max_concurrency", 2)))

    async def run_one(index: int, chunk: str) -> list[dict]:
        async with semaphore:
            try:
                raw = await achat_json(build_messages(chunk))
                return normalize_issues(raw, index)
            except BusinessError as exc:
                logger.warning("第 %d 段校对失败：%s", index + 1, exc.message)
                task_registry.add_error(task_id, f"第 {index + 1} 段：{exc.message}")
                return []
            finally:
                state = task_registry.advance(task_id)
                if state is not None:
                    task_registry.update(
                        task_id, message=f"已完成 {state.done}/{state.total} 段"
                    )

    return list(await asyncio.gather(*(run_one(i, chunk) for i, chunk in enumerate(chunks))))


def _worker(task_id: str, file_key: str, session_factory=SessionLocal) -> None:
    """后台线程执行：解析 → 分块 → 并发校对 → 写缓存。"""
    db = session_factory()
    try:
        task_registry.update(task_id, status=task_registry.STATUS_RUNNING, message="正在解析文档…")
        cached = get_cached_report(db, file_key, "proofread")
        if cached is not None:
            task_registry.finish(task_id, cached, "命中缓存")
            return

        parsed = parse_document(db, file_key)
        rules = get_ai_rules()["proofread"]
        text = "\n".join(page.get("text", "") for page in parsed.get("pages", []))
        chunks = split_text(
            text, int(rules["chunk_chars"]), int(rules.get("min_chunk_chars", 50))
        )
        max_chunks = int(rules.get("max_chunks", 60))
        truncated = len(chunks) > max_chunks
        chunks = chunks[:max_chunks]

        if not chunks:
            report = {
                "file_key": file_key,
                "chunk_count": 0,
                "text_chars": len(text.strip()),
                "issues": [],
                "issue_count": 0,
                "by_severity": {"高": 0, "中": 0, "低": 0},
                "truncated": False,
                "errors": [],
                "summary": "文档内容太少，没有可检查的正文。",
                "generated_at": datetime.now().isoformat(timespec="seconds"),
            }
            save_report(db, file_key, report, "proofread")
            task_registry.finish(task_id, report)
            return

        task_registry.update(task_id, total=len(chunks), message=f"共 {len(chunks)} 段待检查")
        results = asyncio.run(_run_chunks(task_id, chunks))
        issues = merge_issues(results)
        task = task_registry.get_task(task_id)

        report = {
            "file_key": file_key,
            "chunk_count": len(chunks),
            "text_chars": len(text.strip()),
            "issues": issues,
            "issue_count": len(issues),
            "by_severity": {
                label: sum(1 for issue in issues if issue["severity"] == key)
                for key, label in (("high", "高"), ("medium", "中"), ("low", "低"))
            },
            "truncated": truncated,
            "errors": task.errors if task else [],
            "summary": (
                f"共检查 {len(chunks)} 段、{len(text.strip())} 字，"
                f"发现 {len(issues)} 处疑似错别字或标点问题（仅供参考，请逐条确认）。"
            ),
            "generated_at": datetime.now().isoformat(timespec="seconds"),
        }
        save_report(db, file_key, report, "proofread")
        task_registry.finish(task_id, report)
    except BusinessError as exc:
        task_registry.fail(task_id, exc.message)
    except Exception:
        logger.exception("错别字检查任务失败")
        task_registry.fail(task_id, "错别字检查失败，请稍后重试")
    finally:
        db.close()


def start_proofread(file_key: str, session_factory=SessionLocal) -> dict:
    """创建后台任务并立即返回 task_id，前端用轮询查看进度。"""
    if not file_key:
        raise BusinessError("缺少文件标识", code=40073)
    task = task_registry.create_task("proofread", message="任务已创建")
    _executor.submit(_worker, task.task_id, file_key, session_factory)
    return task.to_dict(with_result=False)


def get_progress(task_id: str) -> dict:
    task = task_registry.get_task(task_id)
    if task is None:
        raise BusinessError("任务不存在或已过期", code=40403)
    return task.to_dict()
