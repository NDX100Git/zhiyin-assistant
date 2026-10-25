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


# ---------------------------------------------------------------- 老库重复幂等键：建索引前必须先清洗

DUPLICATE_KEY = "pay:777"


def _drop_index_only(engine) -> None:
    """只删唯一索引、保留列：模拟「上一版跑过一部分迁移」的老库。"""
    with engine.begin() as conn:
        if _index_exists(conn):
            conn.execute(text(f"ALTER TABLE `{TABLE}` DROP INDEX `{INDEX}`"))


def _insert_lines(engine, count: int, key: str = DUPLICATE_KEY) -> None:
    with engine.begin() as conn:
        for _ in range(count):
            conn.execute(
                text(
                    f"INSERT INTO `{TABLE}` "
                    "(order_id, amount_cents, method, idempotency_key) "
                    "VALUES (777, 630, 'mock', :key)"
                ),
                {"key": key},
            )


def test_老库有重复幂等键时迁移先去重再建唯一索引(engine):
    """旧版重复扣款留下的重复键会让 CREATE UNIQUE INDEX 抛 1062，服务起不来。

    建索引前必须先把重复键处理掉：保留最早一条的键不动（后续 pay:<id> 的去重照样
    生效），其余按行 id 重写。数据一行都不能删。
    """
    _drop_index_only(engine)
    try:
        _insert_lines(engine, 2)
        with engine.begin() as conn:
            before = conn.execute(
                text(f"SELECT id, idempotency_key FROM `{TABLE}` "
                     "WHERE order_id = 777 ORDER BY id")
            ).all()
            assert len(before) == 2, "前置条件：两行同键的重复流水"

        applied = run_migrations(engine)
        assert f"{TABLE}.{INDEX}" in applied

        with engine.begin() as conn:
            assert _index_exists(conn), "去重之后必须把唯一索引建起来"
            after = conn.execute(
                text(f"SELECT id, idempotency_key FROM `{TABLE}` "
                     "WHERE order_id = 777 ORDER BY id")
            ).all()
        assert len(after) == 2, "去重不能删数据"
        assert after[0].idempotency_key == DUPLICATE_KEY, "最早一条的键保持原样"
        assert after[1].idempotency_key != DUPLICATE_KEY, "其余重复键必须被重写"
        assert after[1].idempotency_key.startswith(DUPLICATE_KEY), "重写要能看出原始键"

        assert run_migrations(engine) == [], "整段仍要可重复执行"
    finally:
        run_migrations(engine)


def test_重复幂等键去重语句对干净库是空操作(engine, db_session):
    """没有重复键时清洗语句改 0 行，不会把正常流水改写掉。"""
    db_session.add(
        PaymentLog(order_id=778, amount_cents=630, method="mock",
                   idempotency_key="pay:778")
    )
    db_session.commit()
    db_session.expire_all()

    _drop_index_only(engine)
    try:
        run_migrations(engine)
        db_session.expire_all()
        rows = db_session.query(PaymentLog).filter(PaymentLog.order_id == 778).all()
        assert [row.idempotency_key for row in rows] == ["pay:778"]
    finally:
        run_migrations(engine)


# ---------------------------------------------------------------- 老库文件缺归属行


def _pdf_bytes(path: Path) -> bytes:
    """一页带正文的最小 PDF，够体检解析出页数与文本。"""
    import fitz

    with fitz.open() as document:
        page = document.new_page()
        page.insert_text((90, 100), "Abstract")
        page.insert_text((90, 130), "This paper studies formatting.")
        document.save(str(path))
    return path.read_bytes()


def _login(client, device_id: str) -> tuple[dict[str, str], int]:
    body = client.post("/api/auth/login", json={"device_id": device_id}).json()
    assert body["code"] == 0, body
    return {"Authorization": f"Bearer {body['data']['token']}"}, int(body["data"]["user"]["id"])


def _stub_model(monkeypatch) -> None:
    """替身体检模型：离线环境下不调真实 LLM，报告内容对本用例不重要。"""
    monkeypatch.setattr(
        "app.services.format_checker.chat_json",
        lambda messages, thinking=False: {"issues": []},
    )


def _add_order(db_session, user_id: int, file_key: str, *, index: int, days_ago: int):
    """老订单：迁移前上传的文件，只有订单在引用，没有归属行。"""
    from datetime import datetime, timedelta

    from app.models.order import Order

    order = Order(
        order_no=f"0999-{index:03d}",
        pickup_code=f"0999-{index:02d}-{chr(65 + index)}",
        user_id=user_id,
        status="completed",
        file_key=file_key,
        file_name="老论文.pdf",
        page_count=1,
        print_side=1,
        color_mode=0,
        copies=1,
        paper_size="A4",
        amount_cents=15,
        created_at=datetime.now() - timedelta(days=days_ago),
    )
    db_session.add(order)
    db_session.commit()
    db_session.refresh(order)
    return order


def test_老库文件没有归属行时迁移按订单补出来(
    client, db_session, test_storage, tmp_path, monkeypatch
):
    """上传归属表是后加的：迁移前上传、仍被订单引用的文件没有归属行。

    这段老数据在重启后会被新校验（ensure_files_owned_by）一律 40404 挡掉，
    学生重新体检自己的老文件都被拒。迁移要按订单把归属行补出来，
    同一文件多单时取最早订单的用户（一个文件只能属于一个用户）。
    """
    from sqlalchemy import delete, select

    from app.models.uploaded_file import UploadedFile

    _stub_model(monkeypatch)
    owner, owner_id = _login(client, "migrate-owner-1")
    _other, other_id = _login(client, "migrate-other-1")

    uploaded = client.post(
        "/api/file/upload",
        files={"file": ("老论文.pdf", _pdf_bytes(tmp_path / "老论文.pdf"), "application/pdf")},
        headers=owner,
    ).json()
    assert uploaded["code"] == 0, uploaded
    file_key = uploaded["data"]["file_key"]

    # 退回老库的样子：归属行不存在，只有订单在引用这个文件
    db_session.execute(delete(UploadedFile).where(UploadedFile.file_key == file_key))
    db_session.commit()
    assert db_session.execute(
        select(UploadedFile).where(UploadedFile.file_key == file_key)
    ).scalars().all() == []
    _add_order(db_session, owner_id, file_key, index=1, days_ago=3)
    _add_order(db_session, other_id, file_key, index=2, days_ago=0)

    # 前置状态确实是坏的：体检被 40404 拒
    before = client.post("/api/ai/check", json={"file_key": file_key}, headers=owner).json()
    assert before["code"] == 40404, before

    applied = run_migrations(db_session.get_bind())
    assert "uploaded_files.backfill:1" in applied, applied

    # 迁移是在另一条连接上提交的：先结束本会话的事务，否则 REPEATABLE READ
    # 会继续读旧快照，看不到刚补出来的行
    db_session.rollback()
    rows = db_session.execute(
        select(UploadedFile).where(UploadedFile.file_key == file_key)
    ).scalars().all()
    assert len(rows) == 1, "一个文件只能有一行归属"
    assert rows[0].user_id == owner_id, "多单时取最早订单的用户"

    # 体检接口能通过：归属校验放行，报告正常返回
    after = client.post("/api/ai/check", json={"file_key": file_key}, headers=owner).json()
    assert after["code"] == 0, after

    # 再跑一遍：归属行已存在，INSERT IGNORE 不重复插入、不改写归属
    again = run_migrations(db_session.get_bind())
    assert not [action for action in again if action.startswith("uploaded_files.")], again
    db_session.rollback()
    assert len(
        db_session.execute(
            select(UploadedFile).where(UploadedFile.file_key == file_key)
        ).scalars().all()
    ) == 1
