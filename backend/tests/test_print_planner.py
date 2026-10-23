"""打印方案（阶段 5-C）测试：特征提取、方案校验、省纸省钱计算、缓存。"""

from __future__ import annotations

from app.services.print_planner import (
    calc_numbers,
    extract_features,
    plan_print,
    sanitize_recommend,
)


def _parsed(page_count: int = 42, pages_with_images: tuple[int, ...] = (), color: bool = False) -> dict:
    pages = []
    for index in range(1, page_count + 1):
        has_image = index in pages_with_images
        pages.append(
            {
                "index": index,
                "text": "内容" * 20,
                "images": (
                    [{"x_cm": 3, "y_cm": 3, "width_cm": 10, "height_cm": 5, "color": color}]
                    if has_image
                    else []
                ),
                "image_count": 1 if has_image else 0,
            }
        )
    return {"page_count": page_count, "pages": pages, "pages_with_images": list(pages_with_images)}


def test_PPT采用双面讲义2合1时省纸省钱正确():
    features = extract_features(_parsed(42), "pptx", {})
    assert features["doc_type"] == "ppt"

    numbers = calc_numbers(
        features,
        {"print_side": 2, "color_mode": 0, "layout": "handout_2in1", "scale": 100},
        copies=1,
    )
    assert numbers["impressions"] == 21
    assert numbers["sheets_needed"] == 11
    assert numbers["original_sheets"] == 42
    assert numbers["saved_sheets"] == 31
    # 黑白 15 分/页：原方案 42×15=630，新方案 21×15=315
    assert numbers["original_amount_cents"] == 630
    assert numbers["amount_cents"] == 315
    assert numbers["saved_cents"] == 315


def test_彩色文档与多份计算():
    features = extract_features(_parsed(10, pages_with_images=(1,), color=True), "docx", {})
    assert features["has_color"] is True
    assert features["image_ratio"] == 0.1

    numbers = calc_numbers(
        features,
        {"print_side": 2, "color_mode": 1, "layout": "single", "scale": 100},
        copies=2,
    )
    # impressions 是含份数的总打印页数：单页版式 10 页 × 2 份 = 20
    assert numbers["impressions"] == 20
    assert numbers["sheets_needed"] == 10  # 5 张 × 2 份
    assert numbers["original_amount_cents"] == 80 * 10 * 2  # 彩色 80 分/页 × 10 页 × 2 份


def test_非法方案取值回退():
    features = extract_features(_parsed(20), "pdf", {})
    fixed = sanitize_recommend(
        {"print_side": 9, "color_mode": 7, "layout": "handout_9in1", "scale": 42}, features
    )
    assert fixed["print_side"] == 2
    assert fixed["color_mode"] == 0
    assert fixed["layout"] == "single"
    assert fixed["scale"] == 100

    assert sanitize_recommend(None, features)["print_side"] == 2


def test_无彩色图片时强制黑白且模型失败可兜底(db_session, monkeypatch):
    from app.utils.exceptions import BusinessError

    monkeypatch.setattr("app.services.print_planner.parse_document", lambda db, key: _parsed(42))

    def boom(messages):
        raise BusinessError("AI 服务暂时不可用，请稍后重试", code=50031)

    monkeypatch.setattr("app.services.print_planner.chat_json", boom)

    plan = plan_print(db_session, "files/2026/10/plan.pdf", {"use_ai": True})
    assert plan["from_model"] is False
    assert plan["recommend"]["color_mode"] == 0
    assert plan["errors"]
    assert plan["saved_sheets"] == 21  # pdf 走单页版式：42 → 21 张


def test_方案写入缓存且带用户偏好(db_session, monkeypatch):
    monkeypatch.setattr("app.services.print_planner.parse_document", lambda db, key: _parsed(42))
    monkeypatch.setattr(
        "app.services.print_planner.chat_json",
        lambda messages: {
            "recommend": {"print_side": 2, "color_mode": 0, "layout": "handout_4in1", "scale": 85},
            "reason": "PPT 采用 4 合 1 更省纸",
        },
    )

    first = plan_print(db_session, "files/2026/10/cache.pdf", {"copies": 2, "use_ai": True})
    assert first["from_model"] is True
    assert first["recommend"]["layout"] == "handout_4in1"
    assert first["options"]["copies"] == 2

    second = plan_print(db_session, "files/2026/10/cache.pdf", {"copies": 2, "use_ai": True})
    assert second["generated_at"] == first["generated_at"]  # 命中缓存


def test_用户手动选择单双面颜色与版式(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.print_planner.parse_document",
        lambda db, key: _parsed(42),
    )
    monkeypatch.setattr(
        "app.services.print_planner.chat_json",
        lambda messages: {
            "recommend": {"print_side": 2, "color_mode": 0, "layout": "handout_2in1", "scale": 100},
            "reason": "默认推荐双面讲义",
        },
    )

    plan = plan_print(
        db_session,
        "files/2026/10/manual.pptx",
        {"copies": 1, "print_side": 1, "color_mode": 1, "layout": "handout_4in1", "use_ai": True},
    )

    assert plan["recommend"]["print_side"] == 1
    assert plan["recommend"]["color_mode"] == 1
    assert plan["recommend"]["layout"] == "handout_4in1"
    # 42 页 4 合 1 单面：11 张纸，彩色 80 分/页 × 11 页
    assert plan["sheets_needed"] == 11
    assert plan["amount_cents"] == 880

    invalid = plan_print(
        db_session,
        "files/2026/10/manual2.pptx",
        {"print_side": 9, "layout": "handout_9in1", "use_ai": True},
    )
    assert invalid["recommend"]["print_side"] in (1, 2)
    assert invalid["recommend"]["layout"] in {"single", "handout_2in1", "handout_4in1"}


def test_不使用AI时走规则方案且不调用模型(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.print_planner.parse_document",
        lambda db, key: _parsed(42),
    )

    def should_not_be_called(messages):
        raise AssertionError("use_ai=false 时不应调用大模型")

    monkeypatch.setattr("app.services.print_planner.chat_json", should_not_be_called)

    plan = plan_print(db_session, "files/2026/10/basic.pdf", {"use_ai": False})
    assert plan["from_model"] is False
    assert "未使用 AI" in plan["reason"]
    assert plan["recommend"]["print_side"] == 2
    assert plan["sheets_needed"] == 21


def test_不传AI开关时默认不调用模型(db_session, monkeypatch):
    """接口只传文件标识时，不能静默走模型花钱。"""
    monkeypatch.setattr(
        "app.services.print_planner.parse_document",
        lambda db, key: _parsed(10),
    )

    def should_not_be_called(messages):
        raise AssertionError("不传 use_ai 时应按关闭处理")

    monkeypatch.setattr("app.services.print_planner.chat_json", should_not_be_called)

    plan = plan_print(db_session, "files/2026/10/default-off.pdf", {})
    assert plan["from_model"] is False
    assert plan["errors"] == []
    assert "未使用 AI" in plan["reason"]


def test_打印方案给出总页数与单双面张数(db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.print_planner.parse_document",
        lambda db, key: _parsed(5),
    )
    plan = plan_print(db_session, "files/2026/10/sheets.pdf", {})

    assert plan["sheet_info"]["page_count"] == 5
    assert plan["sheet_info"]["single_sided_sheets"] == 5
    assert plan["sheet_info"]["double_sided_sheets"] == 3
    assert "页数不等于纸张数" in plan["sheet_info"]["note"]


def test_打印方案请求模型默认关闭AI开关():
    """接口层默认值也要是关，避免前端漏传时静默调用模型。"""
    from app.schemas.ai import PrintPlanOptions

    assert PrintPlanOptions().use_ai is False
