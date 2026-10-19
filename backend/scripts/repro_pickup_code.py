"""取件码撞码的回归校验（REVIEW.md 第 6 条）。

跑法（需要 MySQL 活着；用独立的复现库 `<DB_NAME>_repro`，不碰开发库与测试库）：

    <venv>/bin/python scripts/repro_pickup_code.py

脚本做的事：

1. 清空复现库，从零开始。
2. 用真实下单逻辑连下两单，第一单改成「昨天下的」（创建时间与订单号日期前缀一起改）。
3. 两单按真实状态机一路推到「待取件」。
4. 依次核销：今天的短码、昨天那单的完整码、已经核销过的码。
5. 打印每一步命中哪一单。

现在应该看到的：两单取件码不同（都带日期前缀），短码只命中今天的单，
完整码精确命中昨天那单，重复核销报「没有找到待取件订单」。

历史背景：修复前两单都是 A-01，按码核销会静默命中较新那单，另一单永远无人核销。
同一脚本在修复前会打印「两单取件码相同」，修复后打印「两单取件码不同」。
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

from sqlalchemy import create_engine, text  # noqa: E402
from sqlalchemy.engine import make_url  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

import app.models  # noqa: E402,F401  确保所有表注册到 metadata
from app.config import settings  # noqa: E402
from app.models.base import Base  # noqa: E402
from app.models.order import Order  # noqa: E402
from app.models.user import User  # noqa: E402
from app.schemas.order import OrderCreateRequest  # noqa: E402
from app.services import admin_service, order_service  # noqa: E402

# 回归脚本只验证取件码逻辑，不碰对象存储：跳过「文件是否还在、是否过期」的校验，
# 否则没有真实上传对象时会直接报 40053，脚本跑不到核销那一步。
order_service._validate_file_freshness = lambda _key: None
from app.services.order_state import (  # noqa: E402
    STATUS_PRINTING,
    STATUS_QUEUING,
    STATUS_READY,
    change_status,
)
from app.utils.exceptions import BusinessError  # noqa: E402

REPRO_DB_NAME = f"{settings.db_name}_repro"


def _session_factory():
    """连到独立的复现库，不存在就建，表结构每次重建。"""
    url = make_url(settings.database_url)

    root_engine = create_engine(url.set(database=None), pool_pre_ping=True)
    with root_engine.connect() as conn:
        conn.execute(
            text(
                f"CREATE DATABASE IF NOT EXISTS `{REPRO_DB_NAME}` "
                "DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        )
        conn.commit()
    root_engine.dispose()

    engine = create_engine(url.set(database=REPRO_DB_NAME), pool_pre_ping=True)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, autoflush=False, autocommit=False)


def _payload() -> OrderCreateRequest:
    return OrderCreateRequest(
        file_key="files/2026/10/repro.pdf",
        file_name="repro.pdf",
        page_count=10,
        print_side=2,
        color_mode=0,
        copies=1,
        paper_size="A4",
        remark="repro-pickup",
    )


def _to_ready(db, order: Order) -> None:
    """按真实状态机推到「待取件」：待支付 -> 排队中 -> 打印中 -> 待取件。"""
    for to_status in (STATUS_QUEUING, STATUS_PRINTING, STATUS_READY):
        change_status(db, order, to_status, operator="repro", note="造待取件状态")
        db.commit()


def main() -> int:
    factory = _session_factory()
    db = factory()
    failures: list[str] = []
    try:
        user = User(openid="repro-pickup-code", auth_provider="device", nickname="撞码校验")
        db.add(user)
        db.commit()

        yesterday = datetime.now() - timedelta(days=1)

        older = order_service.create_order(db, user, _payload())
        # 把这一单整体变成「昨天下的」：创建时间、订单号、取件码的日期前缀一起改成昨天，
        # 再把今天的发号器退回一格，让今天这单仍然从 A-01 开始（真实的一天就是这样）
        older.created_at = yesterday.replace(hour=10, minute=0, second=0, microsecond=0)
        tail = older.order_no.split("-")[-1]
        older.order_no = f"{older.created_at:%y%m%d}-{tail}"
        older.pickup_code = f"{older.created_at:%y%m%d}-{older.pickup_code.split('-', 1)[1]}"
        db.execute(
            text("DELETE FROM order_daily_seq WHERE day = :day"),
            {"day": datetime.now().date()},
        )
        db.commit()
        _to_ready(db, older)

        newer = order_service.create_order(db, user, _payload())
        _to_ready(db, newer)

        print(f"复现库：{REPRO_DB_NAME}")
        for label, order in (("昨天那单", older), ("今天那单", newer)):
            print(
                f"  {label}  id={order.id}  订单号={order.order_no}  "
                f"取件码={order.pickup_code}  创建={order.created_at:%m-%d %H:%M}  "
                f"状态={order.status}"
            )

        if older.pickup_code == newer.pickup_code:
            failures.append(f"两单取件码相同（{older.pickup_code}），跨天仍会撞码")
        else:
            print(f"两单取件码不同：{older.pickup_code} / {newer.pickup_code}")

        short = newer.pickup_code.split("-", 1)[1]  # 去掉日期前缀，得到 A-01
        result = admin_service.verify_by_code(db, short, "repro")
        hit = result["order"]
        print(f"核销当天短码 {short}：命中 id={hit['id']} 订单号={hit['order_no']}")
        if hit["id"] != newer.id:
            failures.append("当天短码没有命中今天那单")

        result = admin_service.verify_by_code(db, older.pickup_code, "repro")
        hit = result["order"]
        print(f"核销完整码 {older.pickup_code}：命中 id={hit['id']} 订单号={hit['order_no']}")
        if hit["id"] != older.id:
            failures.append("完整码没有精确命中昨天那单")

        try:
            admin_service.verify_by_code(db, older.pickup_code, "repro")
        except BusinessError as exc:
            print(f"重复核销已完成的码：被拒绝（{exc.message}）")
        else:
            failures.append("已核销的码还能再核销一次")

        if failures:
            print("\n校验失败：")
            for item in failures:
                print(f"  - {item}")
            return 1
        print("\n校验通过：撞码不再发生，短码与完整码各自命中正确的单")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
