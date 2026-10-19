"""换文件变更请求表：已付款订单换文件时，先挂一条待老师确认的请求。

`pending_flag` 只在待确认时为 1，确认或驳回后置 NULL；配合唯一索引
`(order_id, pending_flag)` 保证「一张单同时只有一个待确认请求」。
MySQL 的唯一索引允许多个 NULL，所以已处理的历史请求不会互相冲突。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base

CHANGE_PENDING = "pending"
CHANGE_APPROVED = "approved"
CHANGE_REJECTED = "rejected"
CHANGE_CANCELLED = "cancelled"


class OrderFileChange(Base):
    __tablename__ = "order_file_changes"
    __table_args__ = (
        Index("idx_file_change_order_status", "order_id", "status"),
        Index("uq_file_change_pending", "order_id", "pending_flag", unique=True),
        {"comment": "换文件变更请求，一张单同时只允许一个待确认"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, default=CHANGE_PENDING, server_default=CHANGE_PENDING
    )
    # 待确认时固定为 1，处理完置 NULL；只用于唯一索引，不对外暴露
    pending_flag: Mapped[int | None] = mapped_column(TINYINT, nullable=True)
    # 要替换订单里的第几份文件（从 1 开始）；单文件订单固定为 1
    file_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )

    # 提交时订单上的快照，便于老师对照与事后追溯。
    # old_page_count / old_amount_cents 是「订单总额与总页数」，与单文件时代语义一致。
    old_file_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    old_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 被替换的那一份文件自己的页数与行金额，多文件确认弹窗要对照它
    old_file_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    old_file_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # 学生提交的新文件
    new_file_key: Mapped[str] = mapped_column(String(255), nullable=False)
    new_file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 新文件自己的页数；new_page_count 是新订单总页数（与 old_page_count 对称）
    new_file_page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    new_page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 系统按当前计费规则重算的订单总价（老师确认弹窗的预填值）
    new_amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    # 新文件自己的行金额，便于追溯
    new_file_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 老师确认后的最终价；不传时等于 new_amount_cents
    final_amount_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)

    reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    requested_by: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(64), nullable=True)
    review_note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
