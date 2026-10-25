"""页数校验的回归用例。

背景：conftest 的 autouse fixture 默认把 `order_service._actual_page_count`
打桩成恒返回 None，好让几十个用假 file_key 的接口用例不必去 mock 文档解析。
代价是「真实实现」那段代码（解析失败、页数为 0、模型/解析器返回脏值）
从来没被执行过——它坏了测试也全绿。

这里用 `@pytest.mark.real_page_count` 把桩关掉，直接对实现与校验函数下断言。
只 mock 最外层的 parse_document，不碰数据库与对象存储。
"""

from __future__ import annotations

import pytest

from app.services import order_service
from app.utils.exceptions import BusinessError

pytestmark = pytest.mark.real_page_count


def _patch_parse(monkeypatch, report) -> None:
    monkeypatch.setattr("app.services.doc_parser.parse_document", lambda db, key: report)


def test_真实实现返回解析出的页数(monkeypatch):
    _patch_parse(monkeypatch, {"page_count": 42})
    assert order_service._actual_page_count(None, "files/a.pdf") == 42


@pytest.mark.parametrize(
    "report",
    [
        {"page_count": 0},        # 解析器没读到页
        {},                       # 缺字段
        {"page_count": None},     # 显式空值
        {"page_count": "十二"},   # 脏值，int() 会抛
    ],
)
def test_真实实现在页数不可用时抛40054(monkeypatch, report):
    _patch_parse(monkeypatch, report)
    with pytest.raises(BusinessError) as exc:
        order_service._actual_page_count(None, "files/a.pdf")
    assert exc.value.code == 40054


def test_页数一致时校验放行(monkeypatch):
    monkeypatch.setattr(order_service, "_actual_page_count", lambda db, key: 42)
    order_service.validate_declared_page_counts(
        None, [{"file_key": "files/a.pdf", "page_count": 42, "file_index": 1}]
    )


def test_多文件时能指出是第几份文件不一致(monkeypatch):
    pages = {"files/a.pdf": 10, "files/b.pdf": 7}
    monkeypatch.setattr(order_service, "_actual_page_count", lambda db, key: pages[key])

    with pytest.raises(BusinessError) as exc:
        order_service.validate_declared_page_counts(
            None,
            [
                {"file_key": "files/a.pdf", "page_count": 10, "file_index": 1},
                {"file_key": "files/b.pdf", "page_count": 9, "file_index": 2},
            ],
        )
    assert exc.value.code == 40054
    assert "第 2 个文件" in exc.value.message


def test_校验函数跳过返回None的实现(monkeypatch):
    """_actual_page_count 返回 None 表示「无法核对」，此时不拦（历史兼容路径）。"""
    monkeypatch.setattr(order_service, "_actual_page_count", lambda db, key: None)
    order_service.validate_declared_page_counts(
        None, [{"file_key": "files/a.pdf", "page_count": 999, "file_index": 1}]
    )
