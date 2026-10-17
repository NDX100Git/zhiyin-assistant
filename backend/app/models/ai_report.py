"""AI 体检报告缓存表。中间结果缓存 7 天后由定时任务清除。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AiReport(Base):
    __tablename__ = "ai_reports"
    __table_args__ = (Index("idx_file_key", "file_key"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    file_key: Mapped[str] = mapped_column(String(255), nullable=False)
    report_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
