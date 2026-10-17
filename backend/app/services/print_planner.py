"""打印方案生成（阶段 5-C）。

模型负责给出推荐方案与理由；纸张数与金额由服务端按 pricing.json 计算（单位：分），
不让模型算钱。方案只作建议，学生可自行调整。
"""

from __future__ import annotations

import json
import logging
import math
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from app.config import get_ai_rules
from app.services.doc_parser import get_cached_report, parse_document, save_report
from app.services.llm import chat_json
from app.services.pricing import unit_price_cents
from app.utils.exceptions import BusinessError
from app.utils.filetype import normalize_ext

logger = logging.getLogger(__name__)

PPT_EXTS = {"ppt", "pptx"}
# 一张纸放几页文档（讲义模式）
PAGES_PER_SIDE = {"single": 1, "handout_2in1": 2, "handout_4in1": 4}

SYSTEM_PROMPT = "你是打印方案优化助手。根据文档特征给出最经济合理的打印方案。"


def extract_features(parsed: dict, ext: str, options: dict) -> dict:
    """文档特征：页数、类型、是否含彩色图表、图片占比。"""
    page_count = int(parsed.get("page_count") or 0)
    pages_with_images = len(parsed.get("pages_with_images", []))
    has_color = any(
        bool(image.get("color"))
        for page in parsed.get("pages", [])
        for image in page.get("images", [])
    )
    return {
        "page_count": page_count,
        "doc_type": "ppt" if ext in PPT_EXTS else "paper",
        "has_color": has_color,
        "image_ratio": round(pages_with_images / page_count, 2) if page_count else 0.0,
        "paper_size": str(options.get("paper_size") or "A4"),
    }


def _messages(features: dict, options: dict) -> list[dict[str, Any]]:
    allowed = get_ai_rules()["print_plan"]
    schema = {
        "recommend": {
            "print_side": 2,
            "color_mode": 0,
            "layout": "handout_2in1",
            "scale": 100,
        },
        "reason": "该文档为 PPT，采用双面讲义 2 合 1 可显著减少纸张。",
    }
    user_content = (
        f"文档特征：\n{json.dumps(features, ensure_ascii=False)}\n\n"
        f"用户偏好：\n{json.dumps(options, ensure_ascii=False)}\n\n"
        f"可用方案：单双面(print_side:1 单面/2 双面)、"
        f"讲义模式(layout:{'/'.join(allowed['allowed_layouts'])}，"
        f"handout_2in1 表示一页纸排 2 页文档)、"
        f"缩放(scale:{'/'.join(str(item) for item in allowed['allowed_scales'])})、"
        "黑白/彩色(color_mode:0 黑白/1 彩色)\n\n"
        f"只输出 JSON（纸张数与金额由系统计算，你不用给）：\n"
        f"{json.dumps(schema, ensure_ascii=False)}"
    )
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]


def fallback_recommend(features: dict) -> dict:
    """模型不可用或返回不合法时的兜底方案（规则决定）。"""
    if features["doc_type"] == "ppt":
        layout = "handout_2in1"
    else:
        layout = "single"
    return {
        "print_side": 2,
        "color_mode": 1 if features["has_color"] else 0,
        "layout": layout,
        "scale": 100,
    }


def sanitize_recommend(recommend: dict | None, features: dict) -> dict:
    """校验模型返回的方案，非法取值一律回退，颜色按文档实际情况兜底。"""
    allowed = get_ai_rules()["print_plan"]
    base = fallback_recommend(features)
    if not isinstance(recommend, dict):
        return base

    print_side = int(recommend.get("print_side") or base["print_side"])
    if print_side not in (1, 2):
        print_side = base["print_side"]

    color_mode = int(recommend.get("color_mode") or base["color_mode"])
    if color_mode not in (0, 1):
        color_mode = base["color_mode"]

    layout = str(recommend.get("layout") or base["layout"])
    if layout not in allowed["allowed_layouts"]:
        layout = base["layout"]

    scale = int(recommend.get("scale") or base["scale"])
    if scale not in allowed["allowed_scales"]:
        scale = base["scale"]

    return {"print_side": print_side, "color_mode": color_mode, "layout": layout, "scale": scale}


def calc_numbers(features: dict, recommend: dict, copies: int) -> dict:
    """按「分」计算纸张数与金额（服务端计算，模型不参与）。"""
    page_count = max(1, int(features["page_count"]))
    per_side = PAGES_PER_SIDE[recommend["layout"]]
    sides = 2 if recommend["print_side"] == 2 else 1

    impressions = math.ceil(page_count / per_side)          # 实际打印页（impression）
    sheets_per_copy = math.ceil(impressions / sides)         # 每份纸张数
    sheets_needed = sheets_per_copy * copies
    original_sheets = page_count * copies                    # 单面不缩放所需纸张

    amount_cents = unit_price_cents(recommend["print_side"], recommend["color_mode"]) * impressions * copies
    original_amount_cents = unit_price_cents(1, recommend["color_mode"]) * page_count * copies

    return {
        "sheets_needed": sheets_needed,
        "original_sheets": original_sheets,
        "saved_sheets": max(0, original_sheets - sheets_needed),
        "impressions": impressions * copies,
        "amount_cents": amount_cents,
        "original_amount_cents": original_amount_cents,
        "saved_cents": max(0, original_amount_cents - amount_cents),
    }


def plan_print(db: Session, file_key: str, options: dict, force: bool = False) -> dict:
    """生成打印方案（含省纸与省钱计算）。"""
    if not file_key:
        raise BusinessError("缺少文件标识", code=40073)

    options = {key: value for key, value in (options or {}).items() if value is not None}
    copies = max(1, int(options.get("copies") or 1))
    options["copies"] = copies

    if not force:
        cached = get_cached_report(db, file_key, "print_plan")
        if cached is not None and cached.get("options") == options:
            logger.info("命中打印方案缓存：%s", file_key)
            return cached

    parsed = parse_document(db, file_key)
    features = extract_features(parsed, normalize_ext(file_key), options)

    reason = ""
    errors: list[str] = []
    recommend: dict | None = None
    try:
        raw = chat_json(_messages(features, options))
        recommend = raw.get("recommend") if isinstance(raw, dict) else None
        reason = str(raw.get("reason") or "") if isinstance(raw, dict) else ""
    except BusinessError as exc:
        logger.warning("打印方案模型调用失败：%s", exc.message)
        errors.append(f"打印方案：{exc.message}")

    final_recommend = sanitize_recommend(recommend, features)

    # 用户显式指定的颜色/份数优先
    if options.get("color_mode") in (0, 1):
        final_recommend["color_mode"] = int(options["color_mode"])
    elif not features["has_color"]:
        final_recommend["color_mode"] = 0

    numbers = calc_numbers(features, final_recommend, copies)
    report = {
        "file_key": file_key,
        "options": options,
        "features": features,
        "recommend": final_recommend,
        "reason": reason or "按文档特征自动选择最省纸的打印方式。",
        **numbers,
        "from_model": recommend is not None,
        "errors": errors,
        "note": "纸张数与金额由服务端按 pricing.json 计算，模型只给推荐与理由",
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    save_report(db, file_key, report, "print_plan")
    return report
