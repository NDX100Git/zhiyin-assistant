"""格式校验（阶段 5-B）测试：规则引擎、合并排序、缓存、接口。"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.ai_report import AiReport
from app.services.format_checker import (
    build_structure_messages,
    check_format,
    detect_profile,
    effective_rules,
    merge_issues,
)
from app.services.format_rules_engine import (
    collect_facts,
    image_resolution_issues,
    sheet_count_info,
    small_font_issues,
    trailing_blank_issues,
    typography_issues,
)


def _rules() -> dict:
    return {
        "structure": {
            "required": ["摘要", "关键词", "目录", "正文", "参考文献"],
            "order": ["摘要", "关键词", "目录", "正文", "参考文献", "致谢"],
            "_source": "GB/T 7713.1",
        },
        "page": {
            "margin_top_cm": 2.5,
            "margin_bottom_cm": 2.5,
            "margin_left_cm": 3.0,
            "margin_right_cm": 2.0,
        },
        "reference": {"standard": "GB/T 7714", "checks": ["期刊文献是否标注卷(期):页码"]},
        "typography": {
            "body": {"font": "宋体", "size_pt": 12, "line_spacing": 1.5},
            "_source": "common",
        },
    }


def _parsed() -> dict:
    """一份"有问题的论文"的解析结果：缺目录、空白页、图片超边距、左边距偏小。"""
    return {
        "page_count": 3,
        "sections": ["摘要", "关键词", "参考文献"],
        "blank_pages": [2],
        "pages": [
            {
                "index": 1,
                "width_cm": 21.0,
                "height_cm": 29.7,
                "char_count": 500,
                "blank": False,
                "image_count": 1,
                "images": [{"x_cm": 1.0, "y_cm": 2.0, "width_cm": 19.0, "height_cm": 8.0}],
                "margins_cm": {
                    "top_cm": 2.5,
                    "bottom_cm": 2.5,
                    "left_cm": 1.0,
                    "right_cm": 2.0,
                },
            },
            {
                "index": 2,
                "width_cm": 21.0,
                "height_cm": 29.7,
                "char_count": 0,
                "blank": True,
                "image_count": 0,
                "images": [],
                "margins_cm": None,
            },
            {
                "index": 3,
                "width_cm": 21.0,
                "height_cm": 29.7,
                "char_count": 300,
                "blank": False,
                "image_count": 0,
                "images": [],
                "margins_cm": {
                    "top_cm": 2.5,
                    "bottom_cm": 2.5,
                    "left_cm": 1.0,
                    "right_cm": 2.0,
                },
            },
        ],
        "references": [{"index": 1, "text": "张三. 论文题目[J]. 某期刊, 2020, 12(3)."}],
    }


def test_规则引擎检出空白页图片超边距与缺失章节():
    facts = collect_facts(_parsed(), _rules())
    locations = {(item["type"], item["location"]) for item in facts}

    assert ("layout", "第 2 页") in locations  # 空白页
    assert ("layout", "第 1 页") in locations  # 图片超边距 / 页边距偏差
    assert ("structure", "全文") in locations  # 缺目录
    assert all(item["source"] == "rule" for item in facts)

    missing = [item for item in facts if "目录" in item["original"]]
    assert missing and missing[0]["severity"] == "high"


def test_合并时规则优先且按严重程度排序():
    rule_issues = [
        {"type": "layout", "severity": "medium", "location": "第 2 页", "original": "规则", "suggestion": "", "reason": "", "source": "rule"}
    ]
    ai_issues = [
        {"type": "layout", "severity": "medium", "location": "第 2 页", "original": "模型", "suggestion": "", "reason": "", "source": "ai"},
        {"type": "reference", "severity": "high", "location": "参考文献 第 1 条", "original": "模型", "suggestion": "", "reason": "", "source": "ai"},
    ]

    merged = merge_issues(rule_issues, ai_issues)
    assert len(merged) == 2  # 同一位置同一类型被去重
    assert merged[0]["severity"] == "high"
    assert merged[0]["source"] == "ai"
    assert merged[1]["original"] == "规则"
    assert [item["id"] for item in merged] == ["i1", "i2"]


def test_同页多条规则问题不被去重吞掉():
    """图片越界与分辨率过低可能落在同一页，规则结果必须都保留。"""
    rule_issues = [
        {
            "type": "layout",
            "severity": "medium",
            "location": "第 1 页",
            "original": "图片右边缘超出页边距约 1.2cm",
            "suggestion": "",
            "reason": "",
            "source": "rule",
        },
        {
            "type": "layout",
            "severity": "medium",
            "location": "第 1 页",
            "original": "图片有效分辨率约 58 DPI，低于打印阈值 150 DPI",
            "suggestion": "",
            "reason": "",
            "source": "rule",
        },
    ]

    merged = merge_issues(rule_issues, [])
    assert len(merged) == 2
    assert {item["original"] for item in merged} == {
        "图片右边缘超出页边距约 1.2cm",
        "图片有效分辨率约 58 DPI，低于打印阈值 150 DPI",
    }
    assert all(item["source"] == "rule" for item in merged)


def test_规则结构问题不会被模型的同义表述挤掉():
    rule_issues = [
        {
            "type": "structure",
            "severity": "high",
            "location": "全文",
            "original": "未检测到「目录」",
            "suggestion": "建议补充目录部分并保留规范标题",
            "reason": "依据 GB/T 7713.1",
            "source": "rule",
        }
    ]
    ai_issues = [
        {
            "type": "structure",
            "severity": "high",
            "location": "论文整体结构",
            "original": "论文缺少目录",
            "suggestion": "请在摘要之后补充目录",
            "reason": "规范要求包含目录",
            "source": "ai",
        }
    ]

    merged = merge_issues(rule_issues, ai_issues)
    assert len(merged) == 1
    assert merged[0]["source"] == "rule"
    assert merged[0]["location"] == "全文"


def test_结构任务提示词包含规则与章节信息():
    messages = build_structure_messages(_rules(), _parsed())
    assert messages[0]["role"] == "system"
    user = messages[1]["content"]
    assert "GB/T 7714" in user
    assert "摘要" in user and "参考文献" in user


def test_体检结果合并规则与模型并存并写入缓存(db_session, monkeypatch):
    monkeypatch.setattr("app.services.format_checker.parse_document", lambda db, key: _parsed())
    monkeypatch.setattr(
        "app.services.format_checker.chat_json",
        lambda messages, thinking=False: {
            "issues": [
                {
                    "id": "r1",
                    "type": "reference",
                    "severity": "high",
                    "location": "参考文献 第 1 条",
                    "original": "张三. 论文题目[J]. 某期刊, 2020, 12(3).",
                    "suggestion": "缺少页码，应补充 45-52",
                    "reason": "GB/T 7714 要求期刊文献标注卷(期):起止页码",
                }
            ],
            "summary": "参考文献著录不规范",
        },
    )

    report = check_format(db_session, "files/2026/10/demo.pdf")

    assert report["page_count"] == 3
    assert report["ai_issue_count"] >= 1
    assert report["rule_issue_count"] >= 3
    assert "共发现" in report["summary"]
    assert report["by_severity"]["高"] >= 1
    assert report["issues"][0]["severity"] == "high"

    # 二次调用命中缓存
    again = check_format(db_session, "files/2026/10/demo.pdf")
    assert again["generated_at"] == report["generated_at"]
    rows = db_session.execute(select(func.count()).select_from(AiReport)).scalar_one()
    assert rows == 1


def test_模型不可用时降级返回规则结果(db_session, monkeypatch):
    from app.utils.exceptions import BusinessError

    monkeypatch.setattr("app.services.format_checker.parse_document", lambda db, key: _parsed())

    def boom(messages, thinking=False):
        raise BusinessError("AI 服务暂时不可用，请稍后重试", code=50031)

    monkeypatch.setattr("app.services.format_checker.chat_json", boom)
    report = check_format(db_session, "files/2026/10/offline.pdf")

    assert report["ai_issue_count"] == 0
    assert report["rule_issue_count"] >= 3
    assert report["ai_errors"]


def test_排版属性偏离参考值时给出建议():
    parsed = {
        "typography": {
            "available": True,
            "sampled_chars": 1200,
            "body_font": "黑体",
            "body_size_pt": 14.0,
            "line_spacing": 1.0,
        }
    }
    issues = typography_issues(parsed, _rules())

    kinds = [issue["original"] for issue in issues]
    assert any("字号实测约 14.0pt" in text for text in kinds)
    assert any("字体实测为「黑体」" in text for text in kinds)
    assert any("行距实测约 1.0 倍" in text for text in kinds)
    assert all(issue["severity"] == "low" for issue in issues)
    assert all("非强制国家标准" in issue["reason"] for issue in issues)


def test_排版属性符合参考值时不报问题():
    parsed = {
        "typography": {
            "available": True,
            "sampled_chars": 1200,
            "body_font": "宋体",
            "body_size_pt": 12.0,
            "line_spacing": 1.5,
        }
    }
    assert typography_issues(parsed, _rules()) == []


def test_正文字数不足时不做排版判断():
    parsed = {"typography": {"available": False, "sampled_chars": 20}}
    assert typography_issues(parsed, _rules()) == []


def test_按文档类型选择检查口径():
    assert detect_profile("files/2026/10/a.pdf") == "paper"
    assert detect_profile("files/2026/10/a.docx") == "paper"
    assert detect_profile("files/2026/10/a.pptx") == "slide"
    assert detect_profile("files/2026/10/a.xlsx") == "sheet"

    slide_rules = effective_rules(_rules(), "slide")
    assert slide_rules["structure"]["required"] == []
    assert slide_rules["page"] == {}
    assert slide_rules["typography"] == {}
    # 原始规则不被修改
    assert _rules()["structure"]["required"]


def test_演示文稿不报缺摘要缺参考文献(db_session, monkeypatch):
    monkeypatch.setattr("app.services.format_checker.parse_document", lambda db, key: _parsed())
    calls: list[dict] = []

    def fake_chat(messages, thinking=False):
        calls.append({"messages": messages})
        return {
            "issues": [
                {
                    "type": "layout",
                    "severity": "medium",
                    "location": "第 1 页",
                    "original": "图片超出幻灯片边界",
                    "suggestion": "建议缩小图片",
                    "reason": "演示时可能被裁切",
                }
            ]
        }

    monkeypatch.setattr("app.services.format_checker.chat_json", fake_chat)

    report = check_format(db_session, "files/2026/10/deck.pptx")
    assert report["profile"] == "slide"
    assert report["profile_note"]
    assert len(calls) == 1  # 只跑版式诊断，不跑结构与参考文献
    assert not any(issue["type"] in {"structure", "reference"} for issue in report["issues"])


# ---------------------------------------------------------------- 四项规则

def test_表格末尾空行空列超过阈值时提示():
    parsed = {
        "spreadsheet": {
            "sheets": [
                {
                    "title": "汇总",
                    "trailing_blank_rows": 120,
                    "trailing_blank_cols": 15,
                }
            ]
        }
    }
    issues = trailing_blank_issues(parsed, _rules())

    assert len(issues) == 1
    assert issues[0]["source"] == "rule"
    assert issues[0]["location"] == "工作表「汇总」"
    assert "120 个空行" in issues[0]["original"]
    assert "15 个空列" in issues[0]["original"]


def test_表格末尾空行空列在阈值内不提示():
    parsed = {
        "spreadsheet": {
            "sheets": [
                {
                    "title": "汇总",
                    "trailing_blank_rows": 3,
                    "trailing_blank_cols": 1,
                }
            ]
        }
    }
    assert trailing_blank_issues(parsed, _rules()) == []
    assert trailing_blank_issues({}, _rules()) == []


def test_表格打印字号低于可读阈值时提示():
    parsed = {"spreadsheet": {"print_font_pt": 6.2, "min_readable_font_pt": 8.0}}
    issues = small_font_issues(parsed, _rules())

    assert len(issues) == 1
    assert issues[0]["source"] == "rule"
    assert "6.2pt" in issues[0]["original"]
    assert "8pt" in issues[0]["original"]


def test_表格打印字号达到阈值不提示():
    parsed = {"spreadsheet": {"print_font_pt": 9.5, "min_readable_font_pt": 8.0}}
    assert small_font_issues(parsed, _rules()) == []
    assert small_font_issues({}, _rules()) == []


def _parsed_with_image(pixel_width: int, width_cm: float) -> dict:
    return {
        "pages": [
            {
                "index": 1,
                "images": [
                    {
                        "pixel_width": pixel_width,
                        "pixel_height": pixel_width,
                        "width_cm": width_cm,
                        "height_cm": width_cm,
                        "x_cm": 3.0,
                        "y_cm": 3.0,
                    }
                ],
            }
        ]
    }


def test_图片有效分辨率过低时提示():
    # 200 像素铺到 10cm：约 51 DPI，远低于 150 DPI
    issues = image_resolution_issues(_parsed_with_image(200, 10.0), _rules())

    assert len(issues) == 1
    assert issues[0]["source"] == "rule"
    assert issues[0]["location"] == "第 1 页"
    assert "DPI" in issues[0]["original"]


def test_图片有效分辨率足够时不提示():
    # 2400 像素铺到 10cm：约 610 DPI
    assert image_resolution_issues(_parsed_with_image(2400, 10.0), _rules()) == []


def test_缺少像素信息时不误报分辨率():
    parsed = {
        "pages": [
            {
                "index": 1,
                "images": [{"width_cm": 10.0, "height_cm": 10.0, "x_cm": 3.0, "y_cm": 3.0}],
            }
        ]
    }
    assert image_resolution_issues(parsed, _rules()) == []


def test_单双面张数换算():
    info = sheet_count_info({"page_count": 5})
    assert info["page_count"] == 5
    assert info["single_sided_sheets"] == 5
    assert info["double_sided_sheets"] == 3
    assert "页数不等于纸张数" in info["note"]

    even = sheet_count_info({"page_count": 6})
    assert even["single_sided_sheets"] == 6
    assert even["double_sided_sheets"] == 3

    unknown = sheet_count_info({})
    assert unknown["page_count"] is None
    assert unknown["single_sided_sheets"] is None
    assert unknown["double_sided_sheets"] is None


def test_四项新规则并入规则事实清单():
    parsed = _parsed()
    parsed["spreadsheet"] = {
        "print_font_pt": 6.0,
        "min_readable_font_pt": 8.0,
        "sheets": [
            {"title": "汇总", "trailing_blank_rows": 200, "trailing_blank_cols": 20}
        ],
    }
    parsed["pages"][0]["images"][0].update(
        {"pixel_width": 100, "pixel_height": 100, "width_cm": 10.0, "height_cm": 10.0}
    )

    facts = collect_facts(parsed, _rules())
    originals = [item["original"] for item in facts]

    assert any("空行" in text and "空列" in text for text in originals)
    assert any("中位字号" in text for text in originals)
    assert any("DPI" in text for text in originals)
    assert all(item["source"] == "rule" for item in facts)


def test_体检报告带单双面张数且新规则标记来源(db_session, monkeypatch):
    parsed = _parsed()
    parsed["spreadsheet"] = {
        "print_font_pt": 6.0,
        "min_readable_font_pt": 8.0,
        "sheets": [
            {"title": "汇总", "trailing_blank_rows": 200, "trailing_blank_cols": 20}
        ],
    }
    monkeypatch.setattr("app.services.format_checker.parse_document", lambda db, key: parsed)
    monkeypatch.setattr("app.services.format_checker.chat_json", lambda messages, thinking=False: {"issues": []})

    report = check_format(db_session, "files/2026/10/rules.pdf")

    assert report["sheet_info"]["double_sided_sheets"] == 2
    assert report["spreadsheet"]["print_font_pt"] == 6.0
    rule_texts = [issue["original"] for issue in report["issues"] if issue["source"] == "rule"]
    assert any("空行" in text for text in rule_texts)
    assert any("中位字号" in text for text in rule_texts)
