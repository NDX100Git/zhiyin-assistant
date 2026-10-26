"""老师端出入参模型。"""

from __future__ import annotations

from pydantic import BaseModel, Field


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class CompleteRequest(BaseModel):
    amount_cents: int | None = Field(
        default=None,
        ge=0,
        description="不传则沿用订单当前成交价；订单还没有成交价时才按 pricing.json 计算",
    )


class BatchCompleteRequest(BaseModel):
    order_ids: list[int] = Field(min_length=1, description="要批量完成的订单 id")


class AbnormalRequest(BaseModel):
    reason: str | None = Field(default=None, description="异常原因，如卡纸、缺墨、学生有异议")
    refund_cents: int | None = Field(
        default=None,
        ge=0,
        description=(
            "关闭异常单时的退款金额（分）：不传按成交价全额退，但不能超过实际已收金额"
        ),
    )


class FileChangeConfirmRequest(BaseModel):
    """老师确认或驳回换文件请求。amount_cents 不传时按系统重算价。"""

    approve: bool = Field(description="true=通过并替换文件，false=驳回")
    amount_cents: int | None = Field(
        default=None, ge=0, description="确认后的最终价（分）；不传则用系统重算价"
    )


class SessionSwitchRequest(BaseModel):
    date: str = Field(description="日期，格式 YYYY-MM-DD")
    session: str = Field(description="时段：am / pm")


class NoticeCreateRequest(BaseModel):
    content: str = Field(description="公告内容")


class VerifyCodeRequest(BaseModel):
    pickup_code: str = Field(description="取件码，如 B-12")
