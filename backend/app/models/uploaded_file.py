"""上传记录表：记住每个上传文件是谁传的。

学生撤回「还没下单的上传文件」时，服务端靠本表做归属校验；
对象存储本身不保存上传者，只有本表能回答「这个文件是谁的」。
清理任务物理删除过期对象时，同步删掉对应记录，避免表无限增长。
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Index, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class UploadedFile(Base):
    __tablename__ = "uploaded_files"
    __table_args__ = (
        Index("uq_uploaded_file_key", "file_key", unique=True),
        Index("idx_uploaded_file_user", "user_id", "created_at"),
        {"comment": "上传记录，用于学生撤回未下单文件的归属校验"},
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    file_key: Mapped[str] = mapped_column(String(255), nullable=False)
    file_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    size: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    content_type: Mapped[str | None] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
