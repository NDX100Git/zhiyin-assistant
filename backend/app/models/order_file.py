"""订单与文件的当前关系表：一张订单可以有多份文件。

`orders.file_key` / `orders.file_name` 仍保留为「第一份文件」的兼容字段，
真正权威的当前文件清单在本表。换文件只改对应 `file_index` 的行，
历史元数据继续写 `order_file_versions`，本表始终只表示当前生效的文件。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OrderFile(Base):
    __tablename__ = "order_files"
    __table_args__ = (
        Index("uq_order_file_index", "order_id", "file_index", unique=True),
        Index("idx_order_file_key", "file_key"),
        {"comment": "订单当前文件清单，一单多文件按 file_index 排序"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 从 1 开始，表示老师在队列里看到的文件顺序
    file_index: Mapped[int] = mapped_column(Integer, nullable=False)
    file_key: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 该文件按当前打印参数算出的行金额快照（分），订单总额是各行之和
    amount_cents: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
