"""收单时段管理（上午 / 下午）。

规则：截单（老师手动关闭 / 当前时段容量已满 / 不在时段内）都不拒绝新订单，
学生仍可下单，但会提示「排在下一个打印时段取件」；已在队列中的订单不受任何影响。
时段划分、容量与提示文案全部来自 config/order_rules.json。
"""

from __future__ import annotations

from datetime import date as date_type
from datetime import datetime, time

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_order_rules
from app.models.order import Order
from app.models.queue_session import QueueSession
from app.utils.exceptions import BusinessError

# `orders.slot_taken`：这一单下单时是否占到本时段名额。
#
# 这一列只由 migrations.py 补（本轮文件边界不含 models/ 与 schema.sql，模型里没有映射），
# 所以读写都走原生 SQL，集中在这里，别处不要再拼。
SLOT_TAKEN_SELECT_SQL = text("SELECT `slot_taken` FROM `orders` WHERE id = :order_id")
SLOT_TAKEN_MARK_SQL = text("UPDATE `orders` SET `slot_taken` = 1 WHERE id = :order_id")
SLOT_TAKEN_CLEAR_SQL = text("UPDATE `orders` SET `slot_taken` = 0 WHERE id = :order_id")


def _parse_hhmm(value: str) -> time:
    hour, minute = value.split(":")
    return time(int(hour), int(minute))


def resolve_session_name(now: datetime) -> str | None:
    """按服务端当前时间判断处于哪个时段；不在任何时段内返回 None。"""
    rules = get_order_rules()["session"]
    current = now.time()
    for name in ("am", "pm"):
        window = rules.get(name, {}).get("window")
        if not window:
            continue
        if _parse_hhmm(window[0]) <= current < _parse_hhmm(window[1]):
            return name
    return None


def _insert_default_session(db: Session, day: date_type, name: str) -> None:
    """行不存在时按默认容量建一行（默认开放）。

    每天首批并发请求会同时发现“行不存在”并各自插同一行；(日期, 时段) 唯一键只允许
    一个成功。用 `INSERT ... ON DUPLICATE KEY UPDATE` 变成幂等写入，没有异常可抛。
    """
    default_capacity = int(get_order_rules()["session"].get("default_capacity", 30))
    db.execute(
        text(
            "INSERT INTO queue_sessions (date, session, is_open, capacity, used) "
            "VALUES (:date, :session, 1, :capacity, 0) "
            "ON DUPLICATE KEY UPDATE date = date"
        ),
        {"date": day, "session": name, "capacity": default_capacity},
    )


def get_session(db: Session, day: date_type, name: str) -> QueueSession | None:
    """读当天某时段的设置行，**不加锁**，不存在返回 None。

    首页概览、下单响应这类读为主的路径走这里：普通 SELECT 既不排锁、也不把
    读接口变成写事务，不会与并发下单争同一行锁。
    """
    return db.execute(
        select(QueueSession).where(
            QueueSession.date == day, QueueSession.session == name
        )
    ).scalar_one_or_none()


def ensure_session(db: Session, day: date_type, name: str) -> QueueSession:
    """读优先、缺失才创建；**不持锁**，返回当天的时段行。

    先普通读一次，命中就直接用；未命中才做一次幂等插入，再普通读一次。
    创建后能读到自己的行：本事务内自己插入的行对自己可见，不需要锁定读
    （锁定读只有在读别的并发事务刚提交的行时才有意义）。
    并发首页请求最多各自做一次幂等插入（改一行后马上提交），
    不会像原先那样在只读路径上执行「插入即更新 + `FOR UPDATE`」参与争锁。
    """
    row = get_session(db, day, name)
    if row is not None:
        return row
    _insert_default_session(db, day, name)
    row = get_session(db, day, name)
    if row is None:  # 理论上不会发生：插入是幂等的
        raise BusinessError(f"无法初始化 {day} {name} 时段设置", code=50021)
    return row


def get_or_create_session(db: Session, day: date_type, name: str) -> QueueSession:
    """取当天某时段的设置行并在事务内锁定它，供**要改这一行**的路径使用。

    行政端开关收单（switch_session）要读改写的行必须加 `FOR UPDATE`，否则两个
    老师并发开关会丢一次修改。只读/只看 id 的路径一律用 ensure_session，
    不要把一个行锁带进首页与下单响应的读事务里。

    锁定读走的是最新数据：MySQL 默认 REPEATABLE READ，普通 SELECT 看不到并发
    事务刚提交的那一行，而行政端本来就靠锁定读拿最新值。
    """
    _insert_default_session(db, day, name)
    return db.execute(
        select(QueueSession)
        .where(QueueSession.date == day, QueueSession.session == name)
        .with_for_update()
    ).scalar_one()


def release_session_slot(db: Session, order: Order) -> bool:
    """把订单占用的时段容量还回去；这一单当时没占到名额时什么都不动。

    下单时只有「本时段收单中且未满员」才自增容量（见 order_service.create_order），
    而截单与满员时下的单**没有**自增。所以归还不能无条件做：不看凭证就减，
    减掉的是别人占着的额度，紧接着放进来的新单就是超卖（实测已用数从 5 掉到 2）。

    凭证是订单行上的 `slot_taken`：下单自增成功才置 1；归还成功就清回 0，
    同一单重复走到这里也只会减一次。容量按订单创建时刻反推所属时段，
    减到 0 为止，不会减成负数。
    """
    if slot_taken(db, order.id) != 1:
        return False
    moment = order.created_at or datetime.now()
    name = resolve_session_name(moment)
    if name is None:
        return False
    row = db.execute(
        select(QueueSession)
        .where(QueueSession.date == moment.date(), QueueSession.session == name)
        .with_for_update()
    ).scalar_one_or_none()
    if row is None or row.used <= 0:
        return False
    row.used -= 1
    _clear_slot_taken(db, order.id)
    return True


def mark_session_slot_taken(db: Session, order_id: int) -> bool:
    """标记这一单确实占到了本时段的名额（下单时容量自增成功后调用）。"""
    result = db.execute(SLOT_TAKEN_MARK_SQL, {"order_id": int(order_id)})
    return bool(int(result.rowcount or 0))


def slot_taken(db: Session, order_id: int) -> int:
    """这一单是否占到名额：1=占到，0=没占到（或已归还）。"""
    value = db.execute(SLOT_TAKEN_SELECT_SQL, {"order_id": int(order_id)}).scalar_one_or_none()
    return int(value or 0)


def _clear_slot_taken(db: Session, order_id: int) -> None:
    db.execute(SLOT_TAKEN_CLEAR_SQL, {"order_id": int(order_id)})


def current_session_info(db: Session, now: datetime | None = None) -> dict:
    """当前收单状态，供首页与下单提示使用。

    - can_order：能否下单。V1 始终为 True（截单只影响取件时段，不影响下单）
    - deferred：本次下单是否会被排到下一个打印时段
    """
    now = now or datetime.now()  # 服务端时间
    rules = get_order_rules()["session"]
    name = resolve_session_name(now)

    if name is None:
        return {
            "date": now.date().isoformat(),
            "session": None,
            "label": "",
            "is_open": False,
            "switch_open": False,
            "capacity": 0,
            "used": 0,
            "can_order": True,
            "deferred": True,
            "hint": rules.get("outside_hint", "当前不在收单时段"),
        }

    row = ensure_session(db, now.date(), name)
    is_open = bool(row.is_open) and row.used < row.capacity
    session_rules = rules.get(name, {})
    hint = "" if is_open else session_rules.get("closed_hint", "当前时段已截单，订单将排在下一个打印时段")

    return {
        "date": now.date().isoformat(),
        "session": name,
        "label": session_rules.get("label", name),
        "is_open": is_open,
        "switch_open": bool(row.is_open),
        "capacity": row.capacity,
        "used": row.used,
        "can_order": True,
        "deferred": not is_open,
        "hint": hint,
    }


def order_notice(db: Session, now: datetime | None = None) -> dict:
    """下单时给学生的提示：本次订单是否排到下一个打印时段。"""
    info = current_session_info(db, now)
    return {
        "deferred": info["deferred"],
        "hint": info["hint"],
        "session": info["session"],
        "session_label": info["label"],
    }
