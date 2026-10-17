"""规则引擎：产出确定性的格式与版式事实（阶段 5-B 的规则侧）。

只做可形式化的判断，不依赖模型，因此结论可直接作为高置信度问题写入体检报告。
大模型负责语义层面的检查（参考文献著录、目录页码一致性等），两者在
format_checker 中合并。规则数值全部来自 config/format_rules.json。
"""

from __future__ import annotations

import re

# 上边距容差放宽：页眉与标题的段前间距会把首行位置往下推，按文本块推算会偏大
MARGIN_TOLERANCE_BY_EDGE = {"上": 1.0, "下": 0.5, "左": 0.5, "右": 0.5}
MAX_MARGIN_ISSUES = 5
FONT_SIZE_TOLERANCE_PT = 0.6
LINE_SPACING_TOLERANCE = 0.3


def _issue(
    issue_type: str,
    severity: str,
    location: str,
    original: str,
    suggestion: str,
    reason: str,
) -> dict:
    return {
        "type": issue_type,
        "severity": severity,
        "location": location,
        "original": original,
        "suggestion": suggestion,
        "reason": reason,
        "source": "rule",
    }


def blank_page_issues(parsed: dict) -> list[dict]:
    """空白页：文本极少且无图片。"""
    return [
        _issue(
            "layout",
            "medium",
            f"第 {index} 页",
            "该页文本极少且没有图片",
            "建议删除空白页",
            "空白页会浪费纸张并影响装订效果",
        )
        for index in parsed.get("blank_pages", [])
    ]


def image_overflow_issues(parsed: dict, rules: dict) -> list[dict]:
    """图片是否超出规则页边距（按图片外框与规则边距比较）。"""
    page_rule = rules.get("page", {})
    top = float(page_rule.get("margin_top_cm", 0))
    bottom_rule = float(page_rule.get("margin_bottom_cm", 0))
    left = float(page_rule.get("margin_left_cm", 0))
    right_rule = float(page_rule.get("margin_right_cm", 0))

    issues: list[dict] = []
    for page in parsed.get("pages", []):
        width = float(page.get("width_cm") or 0)
        height = float(page.get("height_cm") or 0)
        for image in page.get("images", []):
            x0 = float(image.get("x_cm") or 0)
            y0 = float(image.get("y_cm") or 0)
            x1 = x0 + float(image.get("width_cm") or 0)
            y1 = y0 + float(image.get("height_cm") or 0)

            overs: list[str] = []
            if x0 < left:
                overs.append(f"左边缘超出约 {round(left - x0, 2)}cm")
            if x1 > width - right_rule:
                overs.append(f"右边缘超出约 {round(x1 - (width - right_rule), 2)}cm")
            if y0 < top:
                overs.append(f"上边缘超出约 {round(top - y0, 2)}cm")
            if y1 > height - bottom_rule:
                overs.append(f"下边缘超出约 {round(y1 - (height - bottom_rule), 2)}cm")
            if not overs:
                continue

            issues.append(
                _issue(
                    "layout",
                    "high" if len(overs) > 1 else "medium",
                    f"第 {page['index']} 页",
                    f"图片{'、'.join(overs)}"
                    f"（规则页边距 左{left}/右{right_rule}/上{top}/下{bottom_rule}cm）",
                    "建议缩小图片或调整图片位置，使其落在页边距内",
                    "打印时超出页边距的区域可能被裁切",
                )
            )
    return issues


def margin_issues(parsed: dict, rules: dict) -> list[dict]:
    """页边距与规则不符：多数页偏差按"全文"汇总，个别页偏差逐页报。"""
    page_rule = rules.get("page", {})
    key_map = {
        "top_cm": ("margin_top_cm", "上"),
        "bottom_cm": ("margin_bottom_cm", "下"),
        "left_cm": ("margin_left_cm", "左"),
        "right_cm": ("margin_right_cm", "右"),
    }
    edge_labels = {"上": "上边距", "下": "下边距", "左": "左边距", "右": "右边距"}
    pages = parsed.get("pages", [])

    deviating: list[tuple[int, str, float, float]] = []
    for page in pages:
        margins = page.get("margins_cm")
        if not margins:
            continue
        for cm_key, (rule_key, label) in key_map.items():
            expected = page_rule.get(rule_key)
            actual = margins.get(cm_key)
            if expected is None or actual is None:
                continue
            if abs(float(actual) - float(expected)) > MARGIN_TOLERANCE_BY_EDGE.get(label, 0.5):
                deviating.append((page["index"], label, float(actual), float(expected)))

    if not deviating:
        return []

    if len(deviating) > max(1, len(pages) // 2):
        _, label, actual, expected = deviating[0]
        return [
            _issue(
                "layout",
                "medium",
                "全文",
                f"{edge_labels[label]}实测约 {actual}cm，规则要求 {expected}cm"
                f"（超出 ±{MARGIN_TOLERANCE_BY_EDGE.get(label, 0.5)}cm 容差）",
                f"建议在 Word 页面设置中把{edge_labels[label]}调整为 {expected}cm",
                "页边距为高校通行参考值（非强制国家标准），仅作建议；偏差过大可能影响裁切与装订",
            )
        ]

    return [
        _issue(
            "layout",
            "low",
            f"第 {page_index} 页",
            f"{edge_labels[label]}实测约 {actual}cm，规则要求 {expected}cm",
            f"建议把该页{edge_labels[label]}调整为 {expected}cm",
            "页边距为通行参考值（非强制标准），该页与参考值不一致",
        )
        for page_index, label, actual, expected in deviating[:MAX_MARGIN_ISSUES]
    ]


def structure_issues(parsed: dict, rules: dict) -> list[dict]:
    """必需结构要素缺失、章节顺序与规范不一致。"""
    structure = rules.get("structure", {})
    required = list(structure.get("required", []))
    found = list(parsed.get("sections", []))
    source = structure.get("_source", "GB/T 7713.1")
    issues: list[dict] = []

    for name in required:
        if name not in found:
            issues.append(
                _issue(
                    "structure",
                    "high",
                    "全文",
                    f"未检测到「{name}」",
                    f"建议补充{name}部分并保留规范标题",
                    f"依据 {source}，学位论文应包含该结构要素",
                )
            )

    order = [name for name in structure.get("order", []) if name in found]
    indices = [found.index(name) for name in order]
    if indices != sorted(indices):
        issues.append(
            _issue(
                "structure",
                "medium",
                "全文",
                f"检测到的章节顺序为：{' → '.join(found)}",
                f"建议调整为：{' → '.join(structure.get('order', []))}",
                "结构顺序不符合规范要求，影响评审与检索",
            )
        )
    return issues


def _font_matches(expected: str, actual: str) -> bool:
    left = re.sub(r"[\s\-_]", "", expected).lower()
    right = re.sub(r"[\s\-_]", "", actual).lower()
    return bool(left) and bool(right) and (left in right or right in left)


def typography_issues(parsed: dict, rules: dict) -> list[dict]:
    """正文字体、字号、行距与参考值比对（typography 规则来源为 common，只作建议）。"""
    stats = parsed.get("typography") or {}
    if not stats.get("available"):
        return []

    body_rule = (rules.get("typography") or {}).get("body") or {}
    rule_source = (rules.get("typography") or {}).get("_source", "common")
    caveat = f"该项为高校通行参考值（{rule_source}），非强制国家标准，仅作建议"
    issues: list[dict] = []

    expected_size = body_rule.get("size_pt")
    actual_size = stats.get("body_size_pt")
    if expected_size and actual_size and abs(float(actual_size) - float(expected_size)) > FONT_SIZE_TOLERANCE_PT:
        issues.append(
            _issue(
                "typography",
                "low",
                "全文",
                f"正文字号实测约 {actual_size}pt，参考值 {expected_size}pt",
                f"建议把正文字号统一调整为 {expected_size}pt",
                caveat,
            )
        )

    expected_font = str(body_rule.get("font") or "")
    actual_font = str(stats.get("body_font") or "")
    if expected_font and actual_font and not _font_matches(expected_font, actual_font):
        issues.append(
            _issue(
                "typography",
                "low",
                "全文",
                f"正文字体实测为「{actual_font}」，参考值为「{expected_font}」",
                f"建议把正文中文字体统一改为{expected_font}",
                caveat,
            )
        )

    expected_spacing = body_rule.get("line_spacing")
    actual_spacing = stats.get("line_spacing")
    if (
        expected_spacing
        and actual_spacing
        and abs(float(actual_spacing) - float(expected_spacing)) > LINE_SPACING_TOLERANCE
    ):
        issues.append(
            _issue(
                "typography",
                "low",
                "全文",
                f"正文行距实测约 {actual_spacing} 倍，参考值 {expected_spacing} 倍",
                f"建议把正文行距统一调整为 {expected_spacing} 倍",
                caveat,
            )
        )
    return issues


def collect_facts(parsed: dict, rules: dict) -> list[dict]:
    """规则引擎产出的全部确定性事实。"""
    return [
        *blank_page_issues(parsed),
        *image_overflow_issues(parsed, rules),
        *margin_issues(parsed, rules),
        *structure_issues(parsed, rules),
        *typography_issues(parsed, rules),
    ]
