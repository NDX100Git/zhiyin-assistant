"""AI 体检报告缓存表。中间结果随上传文件同寿命，由定时清理跟着文件一起删。

产品口径：**体检报告按账号各存一份，不同账号之间不共用缓存**。所以缓存键是
「账号 + 文件」，而不是只有文件：报告里有整篇正文与问题清单，不能因为两个人
拿到同一个 file_key 就互相看到对方的报告。`user_id` 为空只出现在迁移前的老行上，
读取时会被改记到当前账号（见 doc_parser.get_cached_report）。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class AiReport(Base):
    __tablename__ = "ai_reports"
    __table_args__ = (
        Index("idx_file_key", "file_key"),
        Index("idx_report_owner", "user_id", "file_key"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    # 报告归属账号。为空表示迁移前写入的老行（那时缓存只按 file_key 存一份）。
    # 不设 NOT NULL：老库补列时无法推断历史行属于谁，只能留空再由读取时就地认领。
    user_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    file_key: Mapped[str] = mapped_column(String(255), nullable=False)
    report_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
