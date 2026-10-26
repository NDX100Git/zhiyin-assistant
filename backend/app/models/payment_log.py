"""收款记录表。金额单位：分。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class PaymentLog(Base):
    __tablename__ = "payment_logs"
    __table_args__ = (
        Index("idx_payment_order", "order_id"),
        # 同一订单同一幂等键唯一：支付、现场收款、退款各用各的键，
        # 重复提交与并发重放由数据库直接挡掉，不依赖应用层「先查后写」。
        # 老库需要补这一列与唯一索引（migrations.py 不在本次改动范围）：
        #   ALTER TABLE `payment_logs`
        #     ADD COLUMN `idempotency_key` VARCHAR(64) NULL COMMENT '同一订单内唯一的幂等键',
        #     ADD UNIQUE KEY `uq_payment_idem` (`order_id`, `idempotency_key`);
        UniqueConstraint("order_id", "idempotency_key", name="uq_payment_idem"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # 退款是负数行（method=refund），当日收入直接用流水求和即可把退款排除
    amount_cents: Mapped[int] = mapped_column(Integer, nullable=False)
    method: Mapped[str | None] = mapped_column(String(16), nullable=True)  # mock / onsite / refund
    operator: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 幂等键：pay:<order_id> / onsite:<order_id> / refund:<order_id>
    idempotency_key: Mapped[str | None] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, nullable=False, server_default=func.current_timestamp()
    )
