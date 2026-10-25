"""文档修复（阶段 6）。

铁律：AI 只输出结构化修改指令（actions），真正的文档修改由 python-docx 执行，
绝不让 AI 直接生成文档；必须由用户勾选问题后才修复；修复的是副本，原文件保留。

支持的指令：
- set_line_spacing   设置行距
- set_font           设置中英文字体与字号
- remove_blank_page  删除空白页（连续空段落 / 多余分页符）
- fix_reference      修正参考文献条目（AI 必须给出修正后的完整条目文本）
"""

from __future__ import annotations

import json
import logging
import re
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt
from sqlalchemy.orm import Session

from app.config import BACKEND_DIR, get_format_rules
from app.models.user import User
from app.services.converter import pdf_to_docx_file
from app.services.doc_parser import get_cached_report, save_report
from app.services.file_service import record_generated_upload
from app.services.llm import chat_json
from app.services.storage import build_object_key, get_storage
from app.utils.exceptions import BusinessError
from app.utils.filetype import MIME_BY_EXT, normalize_ext

logger = logging.getLogger(__name__)

RUNTIME_TMP_DIR = BACKEND_DIR / "runtime" / "tmp"

ALLOWED_OPS = {"set_line_spacing", "set_font", "remove_blank_page", "fix_reference"}
MIN_LINE_SPACING, MAX_LINE_SPACING = 1.0, 3.0
MIN_FONT_SIZE, MAX_FONT_SIZE = 6.0, 72.0
MAX_ACTIONS = 50
MAX_REFERENCE_CHARS = 500

SYSTEM_PROMPT = """你是文档修复助手。请根据用户勾选的问题，输出结构化修改指令。

可用指令（只允许这四种）：
1. {"op":"set_line_spacing","target":"body","value":1.5}
2. {"op":"set_font","target":"body","value":{"cn":"宋体","size":12}}
3. {"op":"remove_blank_page","target":"blank_pages"}
4. {"op":"fix_reference","target":"参考文献 第 1 条","original":"原文片段","value":"修正后的完整条目"}

严格要求：
1. 只针对用户勾选的问题输出指令，不要输出未被勾选的问题
2. fix_reference 必须给出修正后的完整条目文本，不要改动专业术语、公式、人名
3. 如果缺少的信息（如页码、出版年、卷期）无法从原文推断，用 ⟨待补⟩ 占位，
   绝对不要编造具体数字，也不要写"起止页码"这类含糊描述
4. 只输出合法 JSON，格式为 {"actions":[...]}，不要任何解释文字"""

# 参考文献条目的序号前缀，如 [1] / ［1］
REFERENCE_LABEL_PATTERN = re.compile(r"^\s*[\[［]\s*(\d+)\s*[\]］]")


# ---------------------------------------------------------------- 指令生成
def _select_issues(report: dict, issue_ids: list[str]) -> list[dict]:
    wanted = {str(item) for item in issue_ids}
    issues = [issue for issue in report.get("issues", []) if str(issue.get("id")) in wanted]
    if not issues:
        raise BusinessError("没有找到要修复的问题，请重新勾选", code=40080)
    return issues


def build_messages(issues: list[dict], rules: dict, source_ext: str) -> list[dict]:
    payload = {
        "勾选的问题": issues,
        "格式规则": rules.get("typography", {}),
        "原文格式": source_ext,
    }
    schema = {
        "actions": [
            {"op": "set_line_spacing", "target": "body", "value": 1.5},
            {"op": "fix_reference", "target": "参考文献 第 1 条", "original": "原文片段", "value": "修正后的完整条目"},
        ]
    }
    user_content = (
        f"用户勾选的问题：\n{json.dumps(payload, ensure_ascii=False)}\n\n"
        f"输出格式：\n{json.dumps(schema, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def sanitize_actions(raw: Any) -> tuple[list[dict], list[dict]]:
    """校验模型给出的指令：非法指令一律丢弃并说明原因。"""
    actions = raw.get("actions") if isinstance(raw, dict) else None
    if not isinstance(actions, list):
        return [], [{"op": None, "reason": "模型没有返回可用的修改指令"}]

    valid: list[dict] = []
    skipped: list[dict] = []
    for item in actions[:MAX_ACTIONS]:
        if not isinstance(item, dict):
            skipped.append({"op": None, "reason": "指令格式不正确"})
            continue
        op = str(item.get("op") or "")
        if op not in ALLOWED_OPS:
            skipped.append({"op": op or None, "reason": "不支持的指令类型"})
            continue

        if op == "set_line_spacing":
            try:
                value = float(item.get("value"))
            except (TypeError, ValueError):
                skipped.append({"op": op, "reason": "行距取值不合法"})
                continue
            if not MIN_LINE_SPACING <= value <= MAX_LINE_SPACING:
                skipped.append({"op": op, "reason": f"行距需在 {MIN_LINE_SPACING}-{MAX_LINE_SPACING} 之间"})
                continue
            valid.append({"op": op, "target": str(item.get("target") or "body"), "value": round(value, 2)})

        elif op == "set_font":
            value = item.get("value")
            if not isinstance(value, dict):
                skipped.append({"op": op, "reason": "字体参数格式不正确"})
                continue
            cn = str(value.get("cn") or value.get("font") or "").strip()
            try:
                size = float(value.get("size") or 0)
            except (TypeError, ValueError):
                skipped.append({"op": op, "reason": "字号取值不合法"})
                continue
            if not cn or len(cn) > 32:
                skipped.append({"op": op, "reason": "字体名不合法"})
                continue
            if not MIN_FONT_SIZE <= size <= MAX_FONT_SIZE:
                skipped.append({"op": op, "reason": f"字号需在 {MIN_FONT_SIZE}-{MAX_FONT_SIZE} 之间"})
                continue
            valid.append(
                {"op": op, "target": str(item.get("target") or "body"), "value": {"cn": cn, "size": size}}
            )

        elif op == "remove_blank_page":
            valid.append({"op": op, "target": str(item.get("target") or "blank_pages")})

        else:  # fix_reference
            value = str(item.get("value") or "").strip()
            if not value or len(value) > MAX_REFERENCE_CHARS:
                skipped.append({"op": op, "reason": "修正后的条目文本为空或过长"})
                continue
            valid.append(
                {
                    "op": op,
                    "target": str(item.get("target") or "参考文献"),
                    "original": str(item.get("original") or "").strip(),
                    "value": value,
                }
            )
    return valid, skipped


# ---------------------------------------------------------------- 指令执行
def _iter_paragraphs(doc: Document):
    return list(doc.paragraphs)


def _apply_set_line_spacing(doc: Document, value: float) -> int:
    changed = 0
    for paragraph in _iter_paragraphs(doc):
        paragraph.paragraph_format.line_spacing = value
        changed += 1
    return changed


def _apply_set_font(doc: Document, cn: str, size: float) -> int:
    changed = 0
    for paragraph in _iter_paragraphs(doc):
        for run in paragraph.runs:
            run.font.name = cn
            run.font.size = Pt(size)
            rpr = run._element.get_or_add_rPr()
            rpr.get_or_add_rFonts().set(qn("w:eastAsia"), cn)
            changed += 1
    return changed


def _has_graphic(paragraph) -> bool:
    element = paragraph._element
    return bool(element.findall(f".//{qn('w:drawing')}")) or bool(
        element.findall(f".//{qn('w:pict')}")
    )


def _has_page_break(paragraph) -> bool:
    """这一段是否真的含分页符。

    只有 `w:type="page"` 才是分页；无 type 的 `<w:br/>` 是软换行（Shift+Enter）。
    把软换行也当分页符，会让「删除空白页」把正文里的换行留白整片压平。
    """
    for br in paragraph._element.findall(f".//{qn('w:br')}"):
        if (br.get(qn("w:type")) or "").strip().lower() == "page":
            return True
    # Word 另存的一种渲染标记，语义等同分页
    return bool(paragraph._element.findall(f".//{qn('w:lastRenderedPageBreak')}"))


def _apply_remove_blank_page(doc: Document) -> int:
    """删除空白页：连续空段落（保留第一个）与只含分页符的空段落。"""
    paragraphs = _iter_paragraphs(doc)
    removed = 0
    index = 0
    while index < len(paragraphs):
        paragraph = paragraphs[index]
        if paragraph.text.strip() or _has_graphic(paragraph):
            index += 1
            continue

        run_end = index
        while (
            run_end < len(paragraphs)
            and not paragraphs[run_end].text.strip()
            and not _has_graphic(paragraphs[run_end])
        ):
            run_end += 1

        has_page_break = _has_page_break(paragraph)
        if run_end - index >= 2 or has_page_break:
            for target in paragraphs[index:run_end]:
                target._element.getparent().remove(target._element)
                removed += 1
            index = run_end
        else:
            index += 1
    return removed


def _normalize_text(text: str) -> str:
    return "".join(text.split()).replace("，", ",").replace("。", ".")


def _with_reference_label(label: str, text: str) -> str:
    """模型给出的条目常常漏掉 [1] 这类序号，由代码补回来。"""
    if label and not REFERENCE_LABEL_PATTERN.match(text):
        return f"{label} {text}"
    return text


def _replace_paragraph_text(paragraph, new_text: str) -> None:
    if paragraph.runs:
        paragraph.runs[0].text = new_text
        for run in paragraph.runs[1:]:
            run.text = ""
    else:
        paragraph.add_run(new_text)


def _apply_fix_reference(doc: Document, original: str, new_text: str) -> bool:
    key = _normalize_text(original)
    paragraphs = _iter_paragraphs(doc)

    if key:
        for paragraph in paragraphs:
            if key in _normalize_text(paragraph.text):
                label = _reference_label(paragraph.text, original)
                _replace_paragraph_text(paragraph, _with_reference_label(label, new_text))
                return True

    # 退化匹配：原文被模型改写较多时，用前 12 个字符定位。
    # 必须同时校验长度接近：只做包含匹配的话，「3.1 方法」会把同前缀的
    # 「3.1 方法论及其局限……」整段替换掉。原文本身不足 12 字时不做退化，
    # 那个前缀等于全文，上面已经精确扫过一遍。
    head = key[:12]
    if len(key) >= 12:
        for paragraph in paragraphs:
            candidate = _normalize_text(paragraph.text)
            if head in candidate and abs(len(candidate) - len(key)) <= max(8, len(key) // 3):
                label = _reference_label(paragraph.text, original)
                _replace_paragraph_text(paragraph, _with_reference_label(label, new_text))
                return True
    return False


def _reference_label(paragraph_text: str, original: str) -> str:
    """取条目序号前缀：优先用文档里实际的，其次用原文片段里的。"""
    for source in (paragraph_text, original):
        match = REFERENCE_LABEL_PATTERN.match(source)
        if match:
            return match.group(0).strip()
    return ""


def apply_actions(doc: Document, actions: list[dict]) -> tuple[list[dict], list[dict]]:
    """执行指令，返回 (已执行, 未生效)。"""
    applied: list[dict] = []
    failed: list[dict] = []

    for action in actions:
        op = action["op"]
        if op == "set_line_spacing":
            changed = _apply_set_line_spacing(doc, float(action["value"]))
            if changed:
                applied.append({"op": op, "value": action["value"], "affected": changed})
            else:
                failed.append({"op": op, "reason": "文档中没有可设置的段落"})
        elif op == "set_font":
            value = action["value"]
            changed = _apply_set_font(doc, value["cn"], float(value["size"]))
            if changed:
                applied.append({"op": op, "value": value, "affected": changed})
            else:
                failed.append({"op": op, "reason": "文档中没有可设置字体的文字"})
        elif op == "remove_blank_page":
            removed = _apply_remove_blank_page(doc)
            if removed:
                applied.append({"op": op, "affected": removed})
            else:
                failed.append({"op": op, "reason": "没有找到可删除的空白页"})
        else:  # fix_reference
            ok = _apply_fix_reference(doc, action.get("original", ""), action["value"])
            if ok:
                applied.append(
                    {"op": op, "target": action.get("target"), "value": action["value"]}
                )
            else:
                failed.append({"op": op, "reason": "文档中未找到对应条目，已跳过"})
    return applied, failed


# ---------------------------------------------------------------- 对外入口
def fix_document(
    db: Session,
    file_key: str,
    issue_ids: list[str],
    file_name: str | None = None,
    *,
    user: User | None = None,
) -> dict:
    """按勾选的问题生成修复副本。

    传入 `user` 时把修复副本写进上传记录，归属当前用户；否则学生无法撤回它。
    """
    if not file_key:
        raise BusinessError("缺少文件标识", code=40073)
    if not issue_ids:
        raise BusinessError("请先勾选要修复的问题", code=40080)

    report = get_cached_report(db, file_key, "check")
    if report is None:
        raise BusinessError("请先做格式体检，再选择要修复的问题", code=40081)
    issues = _select_issues(report, issue_ids)

    ext = normalize_ext(file_key)
    if ext not in {"docx", "pdf"}:
        raise BusinessError("目前只支持修复 docx 与 PDF（PDF 会先转成 Word）", code=40082)

    rules = get_format_rules()
    actions: list[dict] = []
    skipped: list[dict] = []
    notes: list[str] = []
    try:
        raw = chat_json(build_messages(issues, rules, ext))
        actions, skipped = sanitize_actions(raw)
    except BusinessError as exc:
        notes.append(f"AI 未给出可用指令（{exc.message}），本次没有可执行的修复")

    RUNTIME_TMP_DIR.mkdir(parents=True, exist_ok=True)
    storage = get_storage()
    with tempfile.TemporaryDirectory(prefix="fix-", dir=RUNTIME_TMP_DIR) as tmp:
        work_dir = Path(tmp)
        source_name = file_name or f"fixed.{'docx' if ext == 'pdf' else ext}"
        converted = False

        if ext == "pdf":
            pdf_path = storage.download_to_file(file_key, work_dir / "source.pdf")
            docx_path = pdf_to_docx_file(pdf_path, work_dir / "converted.docx")
            converted = True
            notes.append("原文件是 PDF，已先用 pdf2docx 转成 Word 再修复")
        else:
            docx_path = storage.download_to_file(file_key, work_dir / "source.docx")

        document = Document(str(docx_path))
        applied, failed = apply_actions(document, actions)
        output_path = work_dir / "fixed.docx"
        document.save(str(output_path))

        new_key = build_object_key(source_name)
        size = output_path.stat().st_size
        with output_path.open("rb") as fp:
            storage.upload(new_key, fp, size, MIME_BY_EXT["docx"])
        signed_url = storage.signed_url(new_key)

    result = {
        "file_key": new_key,
        "file_name": source_name,
        "size": size,
        "content_type": MIME_BY_EXT["docx"],
        "signed_url": signed_url,
        "source_file_key": file_key,
        "converted_from_pdf": converted,
        "selected_issue_ids": [issue["id"] for issue in issues],
        "applied_actions": applied,
        "failed_actions": failed,
        "skipped_actions": skipped,
        "notes": notes,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if user is not None:
        record_generated_upload(
            db,
            user,
            file_key=new_key,
            file_name=source_name,
            size=size,
            content_type=MIME_BY_EXT["docx"],
        )
    save_report(db, new_key, result, "fix")
    logger.info("文档修复完成：%s -> %s（执行 %d 条指令）", file_key, new_key, len(applied))
    return result
