"""收单时段设置表（上午 / 下午）。关闭只影响新订单，不影响已排队订单。"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime

from sqlalchemy import BigInteger, Date, DateTime, Integer, String, UniqueConstraint
from sqlalchemy.dialects.mysql import TINYINT
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class QueueSession(Base):
    __tablename__ = "queue_sessions"
    __table_args__ = (UniqueConstraint("date", "session", name="uk_date_session"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    date: Mapped[date_type] = mapped_column(Date, nullable=False)
    session: Mapped[str] = mapped_column(String(8), nullable=False)  # am / pm
    is_open: Mapped[int] = mapped_column(TINYINT, default=1, server_default="1")
    capacity: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    used: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
