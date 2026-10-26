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
        # 下单时是否占到本时段名额：1=占到，取消/关单时才归还容量；0=没占到
        # （截单或满员时不加容量，取消也就不能减）。
        # 历史订单无法事后判断当时是否占到名额（截单与满员都不加容量，库里没有痕迹），
        # 默认取 0 即「不归还」：宁肯让老师手动重开时段，也不能再误还别人的名额造成超卖。
        # 模型（app/models/order.py）与两份 schema.sql 里都有这一列；三处必须同口径，
        # 迁移只负责给老库补上，新库由 create_all / schema.sql 直接建出来。
        "slot_taken": (
            "TINYINT NOT NULL DEFAULT 0 "
            "COMMENT '下单时是否占到本时段名额：1=占到，取消时才归还容量'"
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
    "payment_logs": {
        # 支付、现场收款、退款各用各的幂等键（pay: / onsite: / refund: + 订单 id），
        # 重复提交与并发重放由 (order_id, idempotency_key) 唯一索引挡掉。
        # 老库有这一列之前的所有流水都没有键，留 NULL；MySQL 唯一索引不对 NULL 去重，
        # 所以多行 NULL 可以共存，历史数据不会被这一列挡住。
        "idempotency_key": "VARCHAR(64) NULL COMMENT '同一订单内唯一的幂等键'",
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
    "payment_logs": {
        # 建索引写在补列之后：MIGRATION_COLUMNS 先执行，这里才引用得到这一列。
        "uq_payment_idem": (
            "ALTER TABLE `payment_logs` "
            "ADD UNIQUE KEY `uq_payment_idem` (`order_id`, `idempotency_key`)"
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

# 建索引前必须先规整历史脏数据。
#
# `uq_payment_idem` 是后加的：老库里旧版重复扣款已经留下了同一个 (order_id,
# idempotency_key) 的多行流水，直接建唯一索引会抛 1062（Duplicate entry），
# 而建索引跑在启动钩子里，等于整个服务起不来。所以建索引之前先把重复键洗一遍：
#   - 保留最早（id 最小）那条的键不动 —— 后续 pay:<order_id> 的去重保护照样生效；
#   - 其余按行 id 重写成 `<原键前 32 位>#dup:<id>`，足够区分、又能看出原始键；
#   - 一行都不删，历史流水必须留作对账依据。
# LEFT(...,32) 是为了不超 VARCHAR(64)：32 + 5 + BIGINT 最长 19 位 = 56。
DEDUPE_PAYMENT_IDEM_SQL = """
UPDATE `payment_logs` p
JOIN (
  SELECT order_id, idempotency_key, MIN(id) AS keep_id
  FROM `payment_logs`
  WHERE idempotency_key IS NOT NULL
  GROUP BY order_id, idempotency_key
  HAVING COUNT(*) > 1
) d
  ON d.order_id = p.order_id
 AND d.idempotency_key = p.idempotency_key
 AND p.id > d.keep_id
SET p.idempotency_key = CONCAT(LEFT(p.idempotency_key, 32), '#dup:', p.id)
"""

# 建索引之前要执行的清洗语句：表 -> 索引名 -> SQL。
# 只在该索引尚不存在时执行；语句本身也是幂等的（没有重复键时改 0 行），
# 所以迁移中途失败后重跑仍然安全。
MIGRATION_INDEX_PREP: dict[str, dict[str, str]] = {
    "payment_logs": {
        "uq_payment_idem": DEDUPE_PAYMENT_IDEM_SQL,
    },
}

# 老库文件没有归属行：上传归属表（uploaded_files）是后加的，迁移前上传、
# 仍被订单引用的文件在表里没有记录，下单与体检（ensure_files_owned_by）会
# 一律报 40404「文件不存在或不属于当前用户」，学生重新体检自己的老文件被拒。
# 所以按订单把这类文件的归属行补出来：
#   - 文件清单既可能落在 orders.file_key（单文件老单），也可能在 order_files；
#   - 同一文件被多张订单引用时取**最早订单**的用户，与「一个文件只属于一个用户」
#     的唯一索引 uq_uploaded_file_key 一致；
#   - 用 INSERT IGNORE 借唯一索引幂等，已有归属行（包括别人已认领的）绝不被改写。
#
# 两个来源要用 UNION ALL 拼成一个集合才能算「同一文件的最早订单」，但 UNION 要求
# 每个文本列在各分支上能统一到同一个排序规则，而这件事以前是**靠库的默认排序规则
# 隐式决定**的：orders 与 order_files 若一个来自 schema.sql（utf8mb4_unicode_ci）、
# 另一个来自 create_all（跟着库默认，可能是 utf8mb4_0900_ai_ci），MySQL 报
# 1271「Illegal mix of collations for operation 'UNION'」，整条迁移连同全量单测全红。
# 同一份代码在开发库能过、在测试库炸，就是因为它依赖了这种库级状态。
# 所以每个进入 UNION 的文本列都显式写死：先 CONVERT 成 utf8mb4（连 utf8mb3 / latin1
# 的老列也一并兜住，避免「COLLATION ... is not valid for CHARACTER SET」），再指定
# COLLATE utf8mb4_unicode_ci。这样任何字符集 / 排序规则组合的库上，结果都只由这句
# 话决定，不再看库的默认值。新增文本列进这条 SELECT 时必须照抄这两段。
BACKFILL_UPLOADED_FILES_SQL = """
INSERT IGNORE INTO `uploaded_files`
  (`user_id`, `file_key`, `file_name`, `size`, `content_type`, `created_at`)
SELECT src.user_id, src.file_key, src.file_name, 0, NULL, src.created_at
FROM (
  SELECT refs.user_id, refs.file_key, refs.file_name, refs.created_at,
         ROW_NUMBER() OVER (
           PARTITION BY refs.file_key ORDER BY refs.created_at, refs.order_id
         ) AS rn
  FROM (
    SELECT o.user_id AS user_id, o.id AS order_id,
           CONVERT(o.file_key USING utf8mb4) COLLATE utf8mb4_unicode_ci AS file_key,
           CONVERT(o.file_name USING utf8mb4) COLLATE utf8mb4_unicode_ci AS file_name,
           o.created_at AS created_at
    FROM `orders` o
    WHERE o.file_key IS NOT NULL AND o.file_key <> ''
    UNION ALL
    SELECT o.user_id, o.id,
           CONVERT(f.file_key USING utf8mb4) COLLATE utf8mb4_unicode_ci,
           CONVERT(f.file_name USING utf8mb4) COLLATE utf8mb4_unicode_ci,
           o.created_at
    FROM `order_files` f JOIN `orders` o ON o.id = f.order_id
    WHERE f.file_key IS NOT NULL AND f.file_key <> ''
  ) refs
) src
WHERE src.rn = 1
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
                # 建索引之前先洗掉会撞唯一约束的历史脏数据（见 MIGRATION_INDEX_PREP）
                prep = MIGRATION_INDEX_PREP.get(table, {}).get(index)
                if prep:
                    fixed = int(conn.execute(text(prep)).rowcount or 0)
                    if fixed:
                        applied.append(f"{table}.{index}.dedupe:{fixed}")
                        logger.info(
                            "迁移：建 %s.%s 之前已规整 %d 行重复数据", table, index, fixed
                        )
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

        # 老库文件的归属行：必须在文件清单回填之后跑，order_files 里才有行可读。
        # INSERT IGNORE 让「表里已经有这一行的文件」不会重复插入，重复执行改 0 行。
        # 两句都引用了 order_files：它由 create_all 建，缺表说明建表没跑完，
        # 这种库留给启动时报错，迁移这边跳过而不是抛「表不存在」。
        if _table_exists(conn, "uploaded_files") and _table_exists(conn, "order_files"):
            result = conn.execute(text(BACKFILL_UPLOADED_FILES_SQL))
            if result.rowcount:
                applied.append(f"uploaded_files.backfill:{result.rowcount}")
                logger.info("迁移：已为 %d 个老文件补出上传归属行", result.rowcount)

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
