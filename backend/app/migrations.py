"""幂等结构迁移：给已有开发库补新列、新表与索引。

`Base.metadata.create_all` 只建缺失的表，不会给已有表加列。换文件功能给 orders
增加了 `requeue_at`，所以启动时在建表之后跑一遍本模块；新库什么都不用补，老库
会自动补上。同一份逻辑也可以单独执行（`python -m app.migrations` 或
`python scripts/migrate_schema.py`），重复执行不会报错。

只做加法：不删列、不改已有列类型。需要破坏性变更时另写迁移脚本。
"""

from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# 需要补的列：表 -> 列 -> 列定义
MIGRATION_COLUMNS: dict[str, dict[str, str]] = {
    "orders": {
        "requeue_at": "DATETIME NULL COMMENT '换文件后重新排队时间，为空时按创建时间排队'",
    },
}

# 需要补的索引：表 -> 索引名 -> 建索引语句
MIGRATION_INDEXES: dict[str, dict[str, str]] = {
    "order_file_changes": {
        "uq_file_change_pending": (
            "ALTER TABLE `order_file_changes` "
            "ADD UNIQUE KEY `uq_file_change_pending` (`order_id`, `pending_flag`)"
        ),
    },
    "order_file_versions": {
        "uq_order_file_version": (
            "ALTER TABLE `order_file_versions` "
            "ADD UNIQUE KEY `uq_order_file_version` (`order_id`, `version_no`)"
        ),
        "idx_file_version_key": (
            "ALTER TABLE `order_file_versions` ADD KEY `idx_file_version_key` (`file_key`)"
        ),
        "idx_file_version_current": (
            "ALTER TABLE `order_file_versions` "
            "ADD KEY `idx_file_version_current` (`order_id`, `is_current`)"
        ),
    },
}

# 老订单没有版本行，回填一条「当前生效版本」，让清理与追溯看到完整的版本表。
# 只补完全没有版本的订单，重复执行不会重复插入。
BACKFILL_VERSIONS_SQL = """
INSERT INTO `order_file_versions`
  (`order_id`, `version_no`, `file_key`, `file_name`, `page_count`, `amount_cents`,
   `is_current`, `operator`, `note`, `created_at`)
SELECT o.id, 1, o.file_key, o.file_name, COALESCE(o.page_count, 0),
       COALESCE(o.amount_cents, 0), 1, 'system', '迁移回填：订单当前文件',
       COALESCE(o.created_at, CURRENT_TIMESTAMP)
FROM `orders` o
WHERE o.file_key IS NOT NULL
  AND o.file_key <> ''
  AND NOT EXISTS (
    SELECT 1 FROM `order_file_versions` v WHERE v.order_id = o.id
  )
"""


def _column_exists(conn, table: str, column: str) -> bool:
    count = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table "
            "AND COLUMN_NAME = :column"
        ),
        {"table": table, "column": column},
    ).scalar_one()
    return int(count) > 0


def _index_exists(conn, table: str, index: str) -> bool:
    count = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.STATISTICS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table "
            "AND INDEX_NAME = :index"
        ),
        {"table": table, "index": index},
    ).scalar_one()
    return int(count) > 0


def _table_exists(conn, table: str) -> bool:
    count = conn.execute(
        text(
            "SELECT COUNT(*) FROM information_schema.TABLES "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :table"
        ),
        {"table": table},
    ).scalar_one()
    return int(count) > 0


def run_migrations(engine: Engine) -> list[str]:
    """补齐结构并回填老数据，返回本次实际执行的动作（空列表表示已是最新）。"""
    applied: list[str] = []

    with engine.begin() as conn:
        for table, columns in MIGRATION_COLUMNS.items():
            if not _table_exists(conn, table):
                # 新库由 create_all 建表，不应该走到这里；缺表说明建表失败，留给启动报错
                logger.warning("迁移跳过 %s：表不存在，请确认已执行建表", table)
                continue
            for column, ddl in columns.items():
                if _column_exists(conn, table, column):
                    continue
                conn.execute(text(f"ALTER TABLE `{table}` ADD COLUMN `{column}` {ddl}"))
                applied.append(f"{table}.{column}")
                logger.info("迁移：已为 %s 增加列 %s", table, column)

        for table, indexes in MIGRATION_INDEXES.items():
            if not _table_exists(conn, table):
                continue
            for index, ddl in indexes.items():
                if _index_exists(conn, table, index):
                    continue
                conn.execute(text(ddl))
                applied.append(f"{table}.{index}")
                logger.info("迁移：已为 %s 增加索引 %s", table, index)

    with engine.connect() as conn:
        if not _table_exists(conn, "order_file_versions"):
            return applied
        # 版本表一旦有数据就不再全表回填，避免每次启动都扫一遍 orders。
        # 老库第一次上线时版本表为空，这一轮会把历史订单补齐。
        existing_versions = int(
            conn.execute(text("SELECT COUNT(*) FROM `order_file_versions`")).scalar_one()
        )
    if existing_versions:
        return applied

    with engine.begin() as conn:
        result = conn.execute(text(BACKFILL_VERSIONS_SQL))
        if result.rowcount:
            applied.append(f"order_file_versions.backfill:{result.rowcount}")
            logger.info("迁移：已为 %d 张老订单回填文件版本", result.rowcount)

    return applied


if __name__ == "__main__":  # pragma: no cover - 手工执行入口
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app import models  # noqa: F401  注册全部表
    from app.database import engine
    from app.models.base import Base

    Base.metadata.create_all(bind=engine)
    actions = run_migrations(engine)
    print("迁移完成：", "、".join(actions) if actions else "结构已是最新")
