"""支付流水幂等键的迁移测试（老库升级）。

模型里给 `payment_logs` 加了 `idempotency_key` 列与 `(order_id, idempotency_key)`
唯一索引，`create_all` 不会给已存在的表补列，所以老库（含本机历史测试库）必须靠
`run_migrations` 补齐。这里从三个角度钉住它：

1. 老库缺列缺索引时，跑一遍迁移补齐、第二遍无事可做（幂等）；
2. 唯一约束真的在库里生效（不是只往 information_schema 里写了名字）；
3. 两份 schema.sql 与迁移配置不漂移（新库建表也带这一列）。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.migrations import MIGRATION_COLUMNS, MIGRATION_INDEXES, run_migrations
from app.models.payment_log import PaymentLog

BACKEND_DIR = Path(__file__).resolve().parents[1]
SCHEMA_FILES = [BACKEND_DIR / "schema.sql", BACKEND_DIR.parent / "database" / "schema.sql"]

TABLE = "payment_logs"
COLUMN = "idempotency_key"
INDEX = "uq_payment_idem"


def _column_exists(conn) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND COLUMN_NAME = :c"
            ),
            {"t": TABLE, "c": COLUMN},
        ).scalar_one()
    )


def _index_exists(conn) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT COUNT(*) FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = :t AND INDEX_NAME = :i"
            ),
            {"t": TABLE, "i": INDEX},
        ).scalar_one()
    )


def _drop_idempotency(engine) -> None:
    """把库退回老版本的样子：先删唯一索引（它是列的依赖），再删列。"""
    with engine.begin() as conn:
        if _index_exists(conn):
            conn.execute(text(f"ALTER TABLE `{TABLE}` DROP INDEX `{INDEX}`"))
        if _column_exists(conn):
            conn.execute(text(f"ALTER TABLE `{TABLE}` DROP COLUMN `{COLUMN}`"))


def test_老库缺幂等键时两遍迁移补齐且第二遍无事可做(engine):
    """老库缺列缺索引 → 第一遍补齐；第二遍返回空列表（脚本据此打印「结构已是最新」）。"""
    _drop_idempotency(engine)
    try:
        with engine.begin() as conn:
            assert not _column_exists(conn), "前置条件：列必须已经删掉"
            assert not _index_exists(conn), "前置条件：唯一索引必须已经删掉"

        first = run_migrations(engine)
        assert f"{TABLE}.{COLUMN}" in first
        assert f"{TABLE}.{INDEX}" in first

        with engine.begin() as conn:
            assert _column_exists(conn)
            assert _index_exists(conn)

        second = run_migrations(engine)
        assert second == [], f"第二遍不该再有动作，实际：{second}"
    finally:
        # 用例失败也要把结构还原，否则同一个 session 的后续用例全缺列
        run_migrations(engine)


def test_幂等键唯一约束在库上真的生效(db_session):
    """同一单同一幂等键写两次，第二次必须被唯一索引拦住（重复支付不能落两行）。"""
    key = "pay:999"
    db_session.add(
        PaymentLog(order_id=999, amount_cents=630, method="mock", idempotency_key=key)
    )
    db_session.commit()

    db_session.add(
        PaymentLog(order_id=999, amount_cents=630, method="mock", idempotency_key=key)
    )
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # 不同幂等键（支付 / 现场收款 / 退款各用各的）互不冲突
    db_session.add(
        PaymentLog(order_id=999, amount_cents=-630, method="refund",
                   idempotency_key="refund:999")
    )
    db_session.commit()


def test_幂等键允许为空的多行(db_session):
    """老数据与未带键的流水可以有多个 NULL：唯一索引不能把它们当重复值。"""
    for _ in range(2):
        db_session.add(PaymentLog(order_id=1000, amount_cents=100, method="mock"))
    db_session.commit()

    rows = db_session.query(PaymentLog).filter(PaymentLog.order_id == 1000).all()
    assert len(rows) == 2
    assert {row.idempotency_key for row in rows} == {None}


def test_迁移配置里写着支付流水的幂等键():
    assert COLUMN in MIGRATION_COLUMNS.get(TABLE, {}), "迁移里必须补这一列"
    ddl = MIGRATION_INDEXES.get(TABLE, {}).get(INDEX, "")
    assert "UNIQUE" in ddl.upper()
    assert "`order_id`" in ddl and f"`{COLUMN}`" in ddl, "唯一索引必须是 (order_id, idempotency_key)"


@pytest.mark.parametrize("path", SCHEMA_FILES, ids=lambda p: p.name)
def test_两份schema_sql都带支付流水幂等键(path: Path):
    """新库按 schema.sql 建表也要带这一列与唯一索引，不能只靠迁移兜底。"""
    text_sql = path.read_text(encoding="utf-8")
    start = text_sql.find(f"CREATE TABLE IF NOT EXISTS `{TABLE}`")
    assert start >= 0, f"{path} 里找不到 {TABLE} 建表语句"
    block = text_sql[start : text_sql.find(";", start)]
    assert f"`{COLUMN}`" in block, f"{path} 的 {TABLE} 缺 {COLUMN} 列"
    assert f"`{INDEX}`" in block, f"{path} 的 {TABLE} 缺 {INDEX} 唯一索引"
