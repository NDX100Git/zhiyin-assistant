"""统一接口返回格式（铁律第 8 条）。

成功：{"code": 0, "message": "ok", "data": {...}}
失败：{"code": <非0>, "message": "<可直接展示给用户的中文提示>", "data": null}
"""

from __future__ import annotations

from typing import Any


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """成功响应。"""
    return {"code": 0, "message": message, "data": data}


def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """失败响应。code 非 0，message 为中文提示。"""
    return {"code": code, "message": message, "data": data}
