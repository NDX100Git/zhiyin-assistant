"""订单相关的出入参模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrderCreateRequest(BaseModel):
    file_key: str = Field(description="上传接口返回的对象 key")
    file_name: str | None = Field(default=None, description="展示用文件名")
    page_count: int = Field(ge=1, description="页数")
    print_side: int = Field(default=2, description="1=单面 2=双面")
    color_mode: int = Field(default=0, description="0=黑白 1=彩色")
    copies: int = Field(default=1, ge=1, description="份数")
    paper_size: str = Field(default="A4", description="纸张规格")
    remark: str | None = Field(default=None, description="备注，如加急、装订")


class OrderCancelRequest(BaseModel):
    reason: str | None = Field(default=None, description="取消原因")


class OrderFileReplaceRequest(BaseModel):
    """学生换文件请求。订单标识走路径参数，这里只描述新文件。"""

    file_key: str = Field(description="新文件的对象 key（上传接口返回）")
    file_name: str | None = Field(default=None, description="新文件展示名")
    page_count: int = Field(ge=1, description="新文件页数")
    reason: str | None = Field(default=None, description="换文件原因，如传错版本")
