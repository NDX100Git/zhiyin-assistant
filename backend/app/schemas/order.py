"""订单相关的出入参模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class OrderFileItem(BaseModel):
    """下单文件清单中的一项。file_index 不传时按数组顺序自动编号。"""

    file_key: str = Field(description="上传接口返回的对象 key")
    file_name: str | None = Field(default=None, description="展示用文件名")
    page_count: int = Field(ge=1, description="该文件的页数")
    file_index: int | None = Field(
        default=None, ge=1, description="文件顺序，从 1 开始；不传时按数组顺序"
    )


class OrderCreateRequest(BaseModel):
    # 单文件兼容写法：不传 files 时用这三个字段，行为与旧版完全一致
    file_key: str | None = Field(default=None, description="上传接口返回的对象 key")
    file_name: str | None = Field(default=None, description="展示用文件名")
    page_count: int | None = Field(default=None, ge=1, description="页数")
    # 多文件写法：传 files 时以清单为准，file_key 等单文件字段被忽略
    files: list[OrderFileItem] | None = Field(
        default=None, description="文件清单，支持一单多文件；不传时按单文件处理"
    )
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
    file_index: int | None = Field(
        default=None,
        ge=1,
        description="要替换第几份文件；多文件订单必填，单文件订单可省略",
    )
    reason: str | None = Field(default=None, description="换文件原因，如传错版本")
