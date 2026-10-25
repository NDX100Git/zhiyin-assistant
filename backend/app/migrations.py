"""幂等结构迁移：给已有开发库补新列、新表与索引。

`Base.metadata.create_all` 只建缺失的表，不会给已有表加列。换文件功能给 orders
增加了 `requeue_at`，所以启动时在建表之后跑一遍本模块；新库什么都不用补，老库
会自动补上。同一份逻辑也可以单独执行（`python -m app.migrations` 或
`python scripts/migrate_schema.py`），重复执行不会报错。

只做加法：不删列、不改已有列类型。需要破坏性变更时另写迁移脚本。
"""

from __future__ import annotations

import logging
import re

from sqlalchemy import text
from sqlalchemy.engine import Engine

logger = logging.getLogger(__name__)

# 表名与列名要拼进 ALTER 语句（标识符无法用参数绑定），拼之前必须过白名单。
# 当前取值全部是模块常量，属加固：将来若有人把库名/表名改成从配置或请求里读，
# 这一道检查能拦住整条注入口子。
IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


class UnsafeIdentifierError(ValueError):
    """表名或列名不是合法标识符（可能被拿来拼注入语句）。"""


def quote_identifier(name: str) -> str:
    """校验并引用一个 SQL 标识符（表名 / 列名），非法时直接报错。"""
    if not isinstance(name, str) or not IDENTIFIER_PATTERN.match(name):
        raise UnsafeIdentifierError(
            f"非法的 SQL 标识符：{name!r}（只允许字母、数字、下划线，且不以数字开头）"
        )
    return f"`{name}`"

# 需要补的列：表 -> 列 -> 列定义
MIGRATION_COLUMNS: dict[str, dict[str, str]] = {
    "ai_reports": {
        # 体检报告缓存改成按「账号 + 文件」存一份（产品口径：各账号互不共用）。
        # 老库里的历史行无法判断归属，留 NULL，由读取时就地认领。
        "user_id": "BIGINT NULL COMMENT '报告归属账号；为空表示迁移前的老行'",
    },
    "orders": {
        "requeue_at": "DATETIME NULL COMMENT '换文件后重新排队时间，为空时按创建时间排队'",
        "file_changed_at": "DATETIME NULL COMMENT '当前文件最近一次被替换的时间'",
        "files_seen_at": "DATETIME NULL COMMENT '老师确认看到替换后文件的时间'",
        "print_page_count": (
            "INT NOT NULL DEFAULT 0 "
            "COMMENT '整单打印页数 = Σ 各份 page_count × copies'"
        ),
    },
    "order_files": {
        "print_side": "TINYINT NULL COMMENT '1=单面 2=双面；为空沿用订单级'",
        "color_mode": "TINYINT NULL COMMENT '0=黑白 1=彩色；为空沿用订单级'",
        "paper_size": "VARCHAR(8) NULL COMMENT '纸张规格；为空沿用订单级'",
        "copies": "INT NULL COMMENT '该份份数；为空沿用订单级'",
        "print_pages": (
            "INT NOT NULL DEFAULT 0 COMMENT '该份打印页数 = page_count × copies'"
        ),
    },
    "order_file_versions": {
        "file_index": "INT NOT NULL DEFAULT 1 COMMENT '文件在订单清单里的序号'",
    },
    "order_file_changes": {
        "file_index": "INT NOT NULL DEFAULT 1 COMMENT '要替换第几份文件'",
        "old_file_page_count": "INT NULL COMMENT '被替换文件自己的页数'",
        "old_file_amount_cents": "INT NULL COMMENT '被替换文件自己的行金额（分）'",
        "new_file_page_count": "INT NULL COMMENT '新文件自己的页数'",
        "new_file_amount_cents": "INT NULL COMMENT '新文件自己的行金额（分）'",
    },
}

# 需要补的索引：表 -> 索引名 -> 建索引语句
MIGRATION_INDEXES: dict[str, dict[str, str]] = {
    "ai_reports": {
        "idx_report_owner": (
            "ALTER TABLE `ai_reports` ADD KEY `idx_report_owner` (`user_id`, `file_key`)"
        ),
    },
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

# 老订单没有文件清单行，按单文件字段回填第 1 份文件。
# 只补完全没有清单行的订单，重复执行不会重复插入。
BACKFILL_ORDER_FILES_SQL = """
INSERT INTO `order_files`
  (`order_id`, `file_index`, `file_key`, `file_name`, `page_count`, `amount_cents`,
   `created_at`)
SELECT o.id, 1, o.file_key, o.file_name, COALESCE(o.page_count, 0),
       COALESCE(o.amount_cents, 0), COALESCE(o.created_at, CURRENT_TIMESTAMP)
FROM `orders` o
WHERE o.file_key IS NOT NULL
  AND o.file_key <> ''
  AND NOT EXISTS (
    SELECT 1 FROM `order_files` f WHERE f.order_id = o.id
  )
"""

# 老订单没有版本行，回填一条「当前生效版本」，让清理与追溯看到完整的版本表。
# 只补完全没有版本的订单，重复执行不会重复插入。
BACKFILL_VERSIONS_SQL = """
INSERT INTO `order_file_versions`
  (`order_id`, `file_index`, `version_no`, `file_key`, `file_name`, `page_count`,
   `amount_cents`, `is_current`, `operator`, `note`, `created_at`)
SELECT o.id, 1, 1, o.file_key, o.file_name, COALESCE(o.page_count, 0),
       COALESCE(o.amount_cents, 0), 1, 'system', '迁移回填：订单当前文件',
       COALESCE(o.created_at, CURRENT_TIMESTAMP)
FROM `orders` o
WHERE o.file_key IS NOT NULL
  AND o.file_key <> ''
  AND NOT EXISTS (
    SELECT 1 FROM `order_file_versions` v WHERE v.order_id = o.id
  )
"""

# 逐份打印参数：老行没有取值时按订单级同名字段回填，保证「行上有值」与订单一致。
BACKFILL_FILE_PARAMS_SQL = """
UPDATE `order_files` f JOIN `orders` o ON o.id = f.order_id
SET f.print_side = COALESCE(f.print_side, o.print_side),
    f.color_mode = COALESCE(f.color_mode, o.color_mode),
    f.paper_size = COALESCE(f.paper_size, o.paper_size),
    f.copies = COALESCE(f.copies, o.copies)
WHERE f.print_side IS NULL
   OR f.color_mode IS NULL
   OR f.paper_size IS NULL
   OR f.copies IS NULL
"""

# 该份打印页数 = page_count × copies（回填后恒等于这一口径）。
BACKFILL_PRINT_PAGES_SQL = """
UPDATE `order_files` f JOIN `orders` o ON o.id = f.order_id
SET f.print_pages = f.page_count * COALESCE(f.copies, o.copies, 1)
WHERE f.print_pages <> f.page_count * COALESCE(f.copies, o.copies, 1)
"""

# 整单打印页数 = Σ 各份打印页数。
BACKFILL_ORDER_PRINT_PAGES_SQL = """
UPDATE `orders` o
JOIN (
  SELECT order_id, SUM(print_pages) AS total_print_pages
  FROM `order_files`
  GROUP BY order_id
) s ON s.order_id = o.id
SET o.print_page_count = s.total_print_pages
WHERE o.print_page_count <> s.total_print_pages
"""

# 没有任何文件清单行的老订单，按订单自身的页数与份数直接算。
BACKFILL_ORDER_PRINT_PAGES_LEGACY_SQL = """
UPDATE `orders` o
SET o.print_page_count = COALESCE(o.page_count, 0) * COALESCE(o.copies, 1)
WHERE o.print_page_count = 0
  AND COALESCE(o.page_count, 0) > 0
  AND NOT EXISTS (SELECT 1 FROM `order_files` f WHERE f.order_id = o.id)
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
                conn.execute(
                    text(
                        f"ALTER TABLE {quote_identifier(table)} "
                        f"ADD COLUMN {quote_identifier(column)} {ddl}"
                    )
                )
                applied.append(f"{table}.{column}")
                logger.info("迁移：已为 %s 增加列 %s", table, column)

        for table, indexes in MIGRATION_INDEXES.items():
            if not _table_exists(conn, table):
                continue
            for index, ddl in indexes.items():
                if _index_exists(conn, table, index):
                    continue
                # 建索引的语句本身是模块常量，这里只再过一道标识符检查
                quote_identifier(index)
                conn.execute(text(ddl))
                applied.append(f"{table}.{index}")
                logger.info("迁移：已为 %s 增加索引 %s", table, index)

    with engine.begin() as conn:
        # 先补当前文件清单，再补版本行；两句都带 NOT EXISTS，重复执行不会重复插入。
        # 不做「表里有数据就跳过」的判断：迁移中途失败时，下次启动还能把没补的订单补上。
        if _table_exists(conn, "order_files"):
            result = conn.execute(text(BACKFILL_ORDER_FILES_SQL))
            if result.rowcount:
                applied.append(f"order_files.backfill:{result.rowcount}")
                logger.info("迁移：已为 %d 张老订单回填当前文件清单", result.rowcount)

        if _table_exists(conn, "order_file_versions"):
            result = conn.execute(text(BACKFILL_VERSIONS_SQL))
            if result.rowcount:
                applied.append(f"order_file_versions.backfill:{result.rowcount}")
                logger.info("迁移：已为 %d 张老订单回填文件版本", result.rowcount)

        # 逐份打印参数与页数：先补参数，再算该份打印页数，最后汇总整单打印页数。
        # 三句都是「按当前数据重新算出正确值」的幂等更新，跑两遍第二遍改 0 行。
        if _table_exists(conn, "order_files"):
            for name, sql in (
                ("order_files.print_params", BACKFILL_FILE_PARAMS_SQL),
                ("order_files.print_pages", BACKFILL_PRINT_PAGES_SQL),
                ("orders.print_page_count", BACKFILL_ORDER_PRINT_PAGES_SQL),
                (
                    "orders.print_page_count_legacy",
                    BACKFILL_ORDER_PRINT_PAGES_LEGACY_SQL,
                ),
            ):
                result = conn.execute(text(sql))
                changed = int(result.rowcount or 0)
                if changed > 0:
                    applied.append(f"{name}:{changed}")
                    logger.info("迁移：%s 回填 %d 行", name, changed)

    return applied


if __name__ == "__main__":  # pragma: no cover - 手工执行入口
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    from app import models  # noqa: F401  注册全部表
    from app.database import engine
    from app.models.base import Base

    Base.metadata.create_all(bind=engine)
    actions = run_migrations(engine)
    print("迁移完成：", "、".join(actions) if actions else "结构已是最新")
