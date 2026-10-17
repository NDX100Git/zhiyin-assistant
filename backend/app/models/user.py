"""用户表。V1 仅用设备标识 / 取件码作为身份标识，不收集学号等敏感信息。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 微信 openid；APP 端为设备标识。具体取值方式由 AuthProvider 决定
    openid: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)
    phone_tail: Mapped[str | None] = mapped_column(String(4), nullable=True)
    noshow_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    # 预留字段（第九部分 9.2）：用户来源；学号哈希仅在将来合规且必要时启用，V1 不采集
    auth_provider: Mapped[str] = mapped_column(
        String(16), default="device", server_default="device"
    )
    student_no_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
