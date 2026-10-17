"""格式校验（阶段 5-B）测试：规则引擎、合并排序、缓存、接口。"""

from __future__ import annotations

from sqlalchemy import func, select

from app.models.ai_report import AiReport
from app.services.format_checker import (
    build_structure_messages,
    check_format,
    merge_issues,
)
from app.services.format_rules_engine import collect_facts, typography_issues


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
