"""格式校验与版式诊断服务（阶段 5-B）。

架构：规则引擎（format_rules_engine，确定性事实）+ 大模型（语义判断）混合。
- 规则引擎：空白页、图片超出页边距、页边距偏差、结构要素缺失/乱序
- 大模型：参考文献著录规范（GB/T 7714）、结构问题表述、版式问题与目录页码一致性

铁律：AI 结果只作建议，绝不自动修改用户文档；修复必须由用户勾选后再执行。
"""

from __future__ import annotations

import copy
import json
import logging
import re
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_format_rules
from app.services.doc_parser import get_cached_report, parse_document, save_report
from app.services.format_rules_engine import collect_facts
from app.services.llm import chat_json
from app.utils.exceptions import BusinessError
from app.utils.filetype import normalize_ext

logger = logging.getLogger(__name__)

SEVERITY_ORDER = {"high": 0, "medium": 1, "low": 2}
SEVERITY_LABELS = {"high": "高", "medium": "中", "low": "低"}
MAX_REFERENCE_ITEMS = 60
MAX_REFERENCE_CHARS = 300

# 论文 / 演示文稿 / 表格使用不同的检查口径：
# 论文套用 GB/T 7713.1 结构规则；PPT 和表格只做版式检查，不能报"缺摘要/缺参考文献"
SLIDE_EXTS = {"ppt", "pptx"}
SHEET_EXTS = {"xls", "xlsx"}

# 模型对"整篇文档"的位置描述不固定，统一归一到「全文」，避免与规则结果重复
GLOBAL_LOCATIONS = {"全文", "整篇", "整篇论文", "论文整体", "论文整体结构", "文档整体", "全文结构"}
SECTION_PATTERN = re.compile(r"[「『]([^」』]+)[」』]")

STRUCTURE_SYSTEM_PROMPT = (
    "你是高校学位论文格式校验助手。请严格依据给出的校验规则检查论文，只报告确定存在的问题，"
    "不确定的不要报告。不要修改专业术语、公式、人名。输出必须是合法 JSON。"
)

LAYOUT_SYSTEM_PROMPT = (
    "你是文档排版检查助手。根据给定的页面结构数据，判断是否存在版式问题。仅报告有明确依据的问题。"
)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _issue(
    issue_type: str,
    severity: str,
    location: str,
    original: str,
    suggestion: str,
    reason: str,
    source: str,
) -> dict:
    return {
        "type": issue_type,
        "severity": severity if severity in SEVERITY_ORDER else "medium",
        "location": location,
        "original": original,
        "suggestion": suggestion,
        "reason": reason,
        "source": source,
    }


# ---------------------------------------------------------------- 大模型任务
def build_structure_messages(rules: dict, parsed: dict) -> list[dict]:
    """任务一：结构完整性与参考文献著录（5.4 提示词模板）。"""
    references = [
        {"index": item.get("index"), "text": str(item.get("text", ""))[:MAX_REFERENCE_CHARS]}
        for item in parsed.get("references", [])[:MAX_REFERENCE_ITEMS]
    ]
    payload = {
        "检测到的章节": parsed.get("sections", []),
        "参考文献条目": references,
        "页数": parsed.get("page_count"),
        "参考文献条目总数": len(parsed.get("references", [])),
    }
    output_schema = {
        "issues": [
            {
                "id": "r1",
                "type": "reference",
                "severity": "high",
                "location": "参考文献 第 3 条",
                "original": "[1] 张三. 论文题目[J]. 某期刊, 2020, 12(3).",
                "suggestion": "缺少页码，应为 2020, 12(3): 45-52",
                "reason": "GB/T 7714 要求期刊文献标注卷(期):起止页码",
            }
        ],
        "summary": "一句话总结",
    }
    user_content = (
        f"校验规则：\n{_dump(rules)}\n\n"
        f"论文结构信息：\n{_dump(payload)}\n\n"
        f"输出格式（必须是合法 JSON，issues 可以为空数组）：\n{_dump(output_schema)}"
    )
    return [
        {"role": "system", "content": STRUCTURE_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def build_layout_messages(rules: dict, parsed: dict, facts: list[dict]) -> list[dict]:
    """任务二：版式问题诊断（5.4 提示词模板），并把规则事实作为客观依据喂给模型。"""
    pages_summary = [
        {
            "页": page["index"],
            "字数": page.get("char_count"),
            "图片数": page.get("image_count"),
            "空白页": page.get("blank"),
            "图片位置cm": [
                {
                    "x": image.get("x_cm"),
                    "y": image.get("y_cm"),
                    "宽": image.get("width_cm"),
                    "高": image.get("height_cm"),
                }
                for image in page.get("images", [])
            ],
        }
        for page in parsed.get("pages", [])
    ]
    payload = {
        "页数": parsed.get("page_count"),
        "页面尺寸cm": parsed.get("page_size_cm"),
        "规则页边距": rules.get("page", {}),
        "每页结构": pages_summary,
        "规则引擎已确认的客观事实": facts,
    }
    output_schema = {
        "issues": [
            {
                "id": "l1",
                "type": "layout",
                "severity": "medium",
                "location": "第 3 页",
                "original": "图片右边缘超出页边距约 1.2cm",
                "suggestion": "建议缩小图片或调整页边距",
                "reason": "打印时该区域可能被裁切",
            }
        ],
        "summary": "一句话总结",
    }
    user_content = (
        f"页面结构数据：\n{_dump(payload)}\n\n"
        "检查项：图片是否超出页边距、是否存在异常空白页、页边距是否与规则不符、"
        "目录页码与实际页码是否可能不一致。\n"
        "注意：页边距是否合规已由规则引擎按容差判断（见客观事实），你不需要重复报告页边距问题。\n"
        f"输出格式（必须是合法 JSON，issues 可以为空数组）：\n{_dump(output_schema)}"
    )
    return [
        {"role": "system", "content": LAYOUT_SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def normalize_ai_issues(raw: dict, default_type: str) -> list[dict]:
    """把模型返回的问题统一成报告字段（缺字段补默认值，避免脏数据进入报告）。"""
    items = raw.get("issues") if isinstance(raw, dict) else None
    if not isinstance(items, list):
        return []

    normalized: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        normalized.append(
            _issue(
                str(item.get("type") or default_type),
                str(item.get("severity") or "medium"),
                str(item.get("location") or "全文"),
                str(item.get("original") or ""),
                str(item.get("suggestion") or ""),
                str(item.get("reason") or ""),
                "ai",
            )
        )
    return normalized


def _normalize_location(location: str) -> str:
    text = location.strip()
    return "全文" if text in GLOBAL_LOCATIONS else text


def merge_issues(rule_issues: list[dict], ai_issues: list[dict]) -> list[dict]:
    """合并规则与模型结果：同一位置同一类型以规则引擎为准，避免重复报同一问题。"""
    merged: list[dict] = []
    seen: set[tuple[str, str]] = set()

    # 规则引擎已确认缺失的结构要素，避免模型换个说法重复报一次
    covered_sections: set[str] = set()
    for issue in rule_issues:
        if issue["type"] == "structure":
            covered_sections.update(SECTION_PATTERN.findall(f"{issue['original']}{issue['suggestion']}"))

    # 规则结果全部保留；只有模型结果需要做"同一结构要素已报过"的跳过判断
    for issue in rule_issues:
        key = (issue["type"], _normalize_location(issue["location"]))
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(issue))

    for issue in ai_issues:
        if issue["type"] == "structure" and covered_sections:
            text = f"{issue['original']}{issue['suggestion']}{issue['reason']}"
            if any(name in text for name in covered_sections):
                continue

        key = (issue["type"], _normalize_location(issue["location"]))
        if key in seen:
            continue
        seen.add(key)
        merged.append(dict(issue))

    merged.sort(key=lambda item: (SEVERITY_ORDER.get(item["severity"], 9), item["location"]))
    for index, issue in enumerate(merged, start=1):
        issue["id"] = f"i{index}"
    return merged


def _summary_text(issues: list[dict]) -> str:
    if not issues:
        return "未发现明确问题，文档格式基本规范。"
    high = sum(1 for issue in issues if issue["severity"] == "high")
    ai_count = sum(1 for issue in issues if issue["source"] == "ai")
    rule_count = len(issues) - ai_count
    return (
        f"共发现 {len(issues)} 个问题，其中高优先级 {high} 个"
        f"（规则引擎确认 {rule_count} 个，AI 建议 {ai_count} 个）。"
    )


def detect_profile(file_key: str) -> str:
    ext = normalize_ext(file_key)
    if ext in SLIDE_EXTS:
        return "slide"
    if ext in SHEET_EXTS:
        return "sheet"
    return "paper"


def effective_rules(rules: dict, profile: str) -> dict:
    """按文档类型调整规则：非论文不检查结构/参考文献/页边距/正文排版。"""
    if profile == "paper":
        return rules

    adjusted = copy.deepcopy(rules)
    adjusted["structure"] = {"required": [], "order": [], "_source": "not_applicable"}
    adjusted["page"] = {}
    adjusted["typography"] = {}
    adjusted["reference"] = {"standard": rules.get("reference", {}).get("standard"), "checks": []}
    return adjusted


PROFILE_NOTES = {
    "paper": "",
    "slide": "演示文稿按版式检查：不套用论文的结构与参考文献规范，重点看图片越界与空白页。",
    "sheet": "表格文档按基础版式检查：不套用论文结构与参考文献规范。",
}


# ---------------------------------------------------------------- 对外入口
def check_format(db: Session, file_key: str, force: bool = False) -> dict:
    """对文档做格式与版式体检，返回结构化问题清单（只作建议）。"""
    if not file_key:
        raise BusinessError("缺少文件标识", code=40073)
    if not force:
        cached = get_cached_report(db, file_key, "check")
        if cached is not None:
            logger.info("命中体检缓存：%s", file_key)
            return cached

    profile = detect_profile(file_key)
    rules = effective_rules(get_format_rules(), profile)
    parsed = parse_document(db, file_key)

    facts = collect_facts(parsed, rules)
    ai_issues: list[dict] = []
    ai_errors: list[str] = []

    tasks = []
    if profile == "paper":
        tasks.append(("格式合规校验", build_structure_messages(rules, parsed), "reference"))
    if profile in {"paper", "slide"}:
        tasks.append(("版式问题诊断", build_layout_messages(rules, parsed, facts), "layout"))
    for task_name, messages, issue_type in tasks:
        try:
            raw = chat_json(messages, thinking=True)
            ai_issues.extend(normalize_ai_issues(raw, issue_type))
        except BusinessError as exc:
            # 模型不可用时降级：仍然返回规则引擎结果，不阻断学生查看报告
            logger.warning("%s 模型调用失败：%s", task_name, exc.message)
            ai_errors.append(f"{task_name}：{exc.message}")

    issues = merge_issues(facts, ai_issues)
    by_severity = {
        label: sum(1 for issue in issues if issue["severity"] == key)
        for key, label in SEVERITY_LABELS.items()
    }
    report = {
        "file_key": file_key,
        "profile": profile,
        "profile_note": PROFILE_NOTES.get(profile, ""),
        "page_count": parsed.get("page_count"),
        "sections": parsed.get("sections", []),
        "reference_count": len(parsed.get("references", [])),
        "issues": issues,
        "rule_issue_count": sum(1 for issue in issues if issue["source"] == "rule"),
        "ai_issue_count": sum(1 for issue in issues if issue["source"] == "ai"),
        "by_severity": by_severity,
        "summary": _summary_text(issues),
        "ai_errors": ai_errors,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_report(db, file_key, report, "check")
    return report
