"""数据库连接与会话管理（SQLAlchemy 2.x + MySQL 8）。

货币金额一律使用「分」为单位的整数列，禁止浮点数。
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings
from app.models.base import Base

logger = logging.getLogger(__name__)

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_recycle=3600,
    pool_size=settings.db_pool_size,
    max_overflow=settings.db_max_overflow,
    pool_timeout=settings.db_pool_timeout,
    echo=settings.db_echo,
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Iterator[Session]:
    """FastAPI 依赖：请求级数据库会话。"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    """按 SQLAlchemy 模型建表，并补齐已有库缺失的列与索引。

    `create_all` 只建缺失的表，不会给已有表加列；换文件功能新增的
    `orders.requeue_at` 由 run_migrations 幂等补上，新库则直接建全。
    """
    # 先导入全部模型，保证 Base.metadata 中已注册所有表
    from app import models  # noqa: F401
    from app.migrations import run_migrations

    Base.metadata.create_all(bind=engine)
    applied = run_migrations(engine)
    if applied:
        logger.info("数据库结构已补齐：%s", "、".join(applied))
    logger.info("数据库表已就绪，共 %d 张表", len(Base.metadata.tables))
