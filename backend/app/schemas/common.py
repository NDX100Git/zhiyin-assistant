"""统一响应体模型，用于接口文档展示。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ApiResponse[T](BaseModel):
    """所有接口的统一返回结构。"""

    code: int = Field(default=0, description="0 表示成功，非 0 表示业务错误")
    message: str = Field(default="ok", description="可直接展示给用户的中文提示")
    data: T | None = Field(default=None, description="业务数据")
