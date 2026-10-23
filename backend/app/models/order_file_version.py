"""订单文件版本表：记录「这一单当时打的是哪份文件」。

只存元数据，不复制文件本体。文件本体留在对象存储里，订单表继续保留当前文件标识，
版本表负责追溯。一单同一时刻只能有一个生效版本。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OrderFileVersion(Base):
    __tablename__ = "order_file_versions"
    __table_args__ = (
        Index("uq_order_file_version", "order_id", "version_no", unique=True),
        Index("idx_file_version_key", "file_key"),
        Index("idx_file_version_current", "order_id", "is_current"),
        {"comment": "订单文件版本，只存元数据，不复制文件本体"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 这份文件在订单文件清单里的序号（从 1 开始）；老订单迁移回填为 1
    file_index: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    # 从 1 递增，每次换文件加一；同一订单内全局递增，便于追溯操作顺序
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    file_key: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    page_count: Mapped[int] = mapped_column(Integer, nullable=False)
    # 该版本对应的价格快照，避免单价改了以后对不上账
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    # 内容指纹，留给后续去重与「换的是不是同一个文件」判断
    file_sha256: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 每个 file_index 各有一个生效版本；多文件订单会有多行 is_current=1
    is_current: Mapped[int] = mapped_column(TINYINT, nullable=False, default=1, server_default="1")
    # student / admin / system（迁移回填）
    operator: Mapped[str | None] = mapped_column(String(32), nullable=True)
    note: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
