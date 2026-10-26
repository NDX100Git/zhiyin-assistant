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
        Index("uq_pickup_code", "pickup_code", unique=True),
        {"comment": "订单主表，状态机见交接文档 3.1"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 序号日（年+日）自带，跨年不会重号，所以两张号都能上唯一索引
    order_no: Mapped[str] = mapped_column(String(16), nullable=False, unique=True)  # 261016-007
    pickup_code: Mapped[str] = mapped_column(String(20), nullable=False)  # 261017-A-01
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    file_key: Mapped[str | None] = mapped_column(String(255), nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 整单打印页数 = Σ 各份打印页数（page_count × copies）；page_count 仍是文档页数之和
    print_page_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
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
    # 换文件后重新排队的时间。队列排序优先用它，为空时回落到 created_at；
    # 不改 order_no 与 created_at，保证订单号、取件码与下单时间都可追溯。
    requeue_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # 当前文件最近一次被替换的时间；下单时为 NULL。老师确认看到后写 files_seen_at，
    # 两者一起表达「文件已替换，但老师还没重新下载确认」。
    file_changed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    files_seen_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    printed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    picked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    remark: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cancel_reason: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 预留字段（第九部分 9.2）：多文印点与支付渠道扩展
    site_id: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    pay_channel: Mapped[str | None] = mapped_column(String(16), nullable=True)
    external_pay_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    printer_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # 下单时是否占到本时段名额：1=占到，取消/关单时才归还容量；0=没占到（截单或
    # 满员时不加容量，取消也就不能减）。历史订单无法事后判断，默认 0 即「不归还」。
    # 与 app/migrations.py 的同名列、两份 schema.sql 里的建表语句同口径。
    slot_taken: Mapped[int] = mapped_column(TINYINT, nullable=False, default=0, server_default="0")
