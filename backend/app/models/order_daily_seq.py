"""每个自然日一行的发号器：订单号与取件码都从这里的同一个序号出。

为什么单独一张表：订单号、取件码、时段容量原先都站在「当日订单数加一」上面，
并发会读到同一个数，只能靠唯一索引加失败重试兜；序号表这一行被 FOR UPDATE 级别的
自增锁住，一笔下单一个号，不重号也不需要重试。
"""

from __future__ import annotations

from datetime import date as date_type

from sqlalchemy import Date, Integer
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class OrderDailySeq(Base):
    __tablename__ = "order_daily_seq"
    __table_args__ = ({"comment": "按日发号器，一笔下单取一个号"},)

    day: Mapped[date_type] = mapped_column(Date, primary_key=True)
    last_seq: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
