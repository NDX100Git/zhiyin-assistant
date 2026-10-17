"""订单核心表。订单号、取件码、状态、金额全部由服务端生成与推进。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Order(Base):
    __tablename__ = "orders"
    __table_args__ = (
        Index("idx_status_created", "status", "created_at"),
        Index("idx_pickup_code", "pickup_code"),
        {"comment": "订单主表，状态机见交接文档 3.1"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_no: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)  # 1016-007
    pickup_code: Mapped[str] = mapped_column(String(8), nullable=False)  # B-12，按当日顺序
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    file_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    print_side: Mapped[int | None] = mapped_column(TINYINT, nullable=True)  # 1=单面 2=双面
    color_mode: Mapped[int | None] = mapped_column(TINYINT, nullable=True)  # 0=黑白 1=彩色
    copies: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    paper_size: Mapped[str] = mapped_column(String(8), default="A4", server_default="A4")
    amount_cents: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # 单位：分
    paid: Mapped[int] = mapped_column(TINYINT, default=0, server_default="0")
    pay_method: Mapped[str | None] = mapped_column(String(16), nullable=True)  # mock / onsite
    paid_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
    printed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    picked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 预留字段（第九部分 9.2）：多文印点与支付渠道扩展
    site_id: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    pay_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    external_pay_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    printer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
