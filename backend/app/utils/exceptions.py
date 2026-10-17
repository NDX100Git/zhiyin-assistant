"""业务异常与全局异常处理。

约定（V1）：业务错误与参数校验错误统一返回 HTTP 200 + 非 0 code，
便于前端只解析 {code, message, data} 结构；未预期的服务端异常返回 HTTP 500，
响应体仍是同一套统一格式。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.utils.response import fail, ok

logger = logging.getLogger(__name__)

# Starlette 内置的英文提示统一替换为中文，保证 message 可直接展示给用户
HTTP_STATUS_MESSAGES = {
    400: "请求参数有误",
    401: "未登录或登录已过期",
    403: "没有访问权限",
    404: "接口不存在",
    405: "请求方式不正确",
    409: "当前状态不允许该操作",
    413: "文件或请求过大",
    415: "不支持的内容类型",
    422: "参数校验失败",
    429: "操作太频繁，请稍后再试",
    500: "服务端开小差了，请稍后重试",
    502: "服务暂时不可用",
    503: "服务暂时不可用",
}

GENERIC_DETAILS = {"Not Found", "Method Not Allowed", "Unauthorized", "Forbidden"}


class BusinessError(Exception):
    """业务异常。业务代码抛出本异常，由全局处理器转成统一响应。"""

    def __init__(self, message: str, code: int = 40000, data: object = None) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.data = data


def register_exception_handlers(app: FastAPI) -> None:
    """注册全局异常处理器。"""

    @app.exception_handler(BusinessError)
    async def handle_business_error(_: Request, exc: BusinessError) -> JSONResponse:
        return JSONResponse(status_code=200, content=fail(exc.code, exc.message, exc.data))

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        # 把 Pydantic 的校验错误压成一句中文提示
        details = []
        for err in exc.errors():
            loc = ".".join(str(item) for item in err.get("loc", []) if item != "body")
            details.append(f"{loc}: {err.get('msg', '参数不合法')}" if loc else err.get("msg", "参数不合法"))
        message = "参数校验失败：" + "；".join(details) if details else "参数校验失败"
        return JSONResponse(status_code=200, content=fail(40001, message))

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        detail = exc.detail if isinstance(exc.detail, str) else ""
        if detail and detail not in GENERIC_DETAILS:
            message = detail
        else:
            message = HTTP_STATUS_MESSAGES.get(exc.status_code, "请求失败")
        return JSONResponse(status_code=200, content=fail(exc.status_code * 100, message))

    @app.exception_handler(Exception)
    async def handle_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("未预期的服务端异常: %s", exc)
        return JSONResponse(status_code=500, content=fail(50000, "服务端开小差了，请稍后重试"))


__all__ = ["BusinessError", "fail", "ok", "register_exception_handlers"]
