"""统一接口返回格式（铁律第 8 条）。

成功：{"code": 0, "message": "ok", "data": {...}}
失败：{"code": <非0>, "message": "<可直接展示给用户的中文提示>", "data": null}
"""

from __future__ import annotations

from typing import Any

from fastapi.responses import JSONResponse


def ok(data: Any = None, message: str = "ok") -> dict[str, Any]:
    """成功响应。"""
    return {"code": 0, "message": message, "data": data}


def ok_json(data: Any = None, message: str = "ok") -> JSONResponse:
    """成功响应，直接给出 JSON 响应对象。

    返回 dict 时框架会把整棵树逐层过一遍 jsonable_encoder，再序列化一次；
    轮询类的读接口（学生订单列表、老师接单队列）每秒要几千次，这一趟白跑就很贵。
    只给「返回值全是 dict / list / str / int」的读接口用，字段口径与 ok() 完全一致。
    """
    return JSONResponse(content={"code": 0, "message": message, "data": data})


def fail(code: int, message: str, data: Any = None) -> dict[str, Any]:
    """失败响应。code 非 0，message 为中文提示。"""
    return {"code": code, "message": message, "data": data}
