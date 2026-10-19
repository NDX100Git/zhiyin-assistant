"""换文件业务：学生提交、老师确认，以及文件版本与待确认请求的维护。

口径（与产品结论一致）：

- 待支付：立即生效，页数与金额重算，订单号、取件码、状态都不变。
- 排队中、异常：只创建「待老师确认」的请求，不立即改单；异常单通过后恢复排队。
- 打印中、待取件、已完成、已取消：拒绝并给出明确中文提示。
- 每张单同时只允许一个待确认请求，靠唯一索引兜底。
- 通过后重新排到队尾（写 requeue_at，不改 order_no 与 created_at）。
- 价格差没有退款通道：新价更低不退；新价更高由老师确认，差额在核销时现场补收。
"""

from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.config import get_order_rules
from app.models.order import Order
from app.models.order_file_change import (
    CHANGE_APPROVED,
    CHANGE_CANCELLED,
    CHANGE_PENDING,
    CHANGE_REJECTED,
    OrderFileChange,
)
from app.models.order_file_version import OrderFileVersion
from app.models.payment_log import PaymentLog
from app.services.notifier import get_notifier
from app.services.order_state import (
    STATUS_ABNORMAL,
    STATUS_PENDING_PAY,
    STATUS_QUEUING,
    change_status,
    record_log,
    status_label,
)
from app.services.pricing import calc_amount_cents
from app.utils.exceptions import BusinessError

logger = logging.getLogger(__name__)

# 立即生效的状态
IMMEDIATE_STATUSES = frozenset({STATUS_PENDING_PAY})
# 需要老师确认的状态
CONFIRM_REQUIRED_STATUSES = frozenset({STATUS_QUEUING, STATUS_ABNORMAL})
# 老师确认接口允许的订单状态
CONFIRMABLE_STATUSES = frozenset({STATUS_QUEUING, STATUS_ABNORMAL})

# 非法状态给出可执行的中文提示，而不是只有一句「不能换」
REJECT_MESSAGES = {
    "printing": "订单正在打印，不能更换文件；如文件有问题，请联系老师标为异常后再换",
    "ready": "文件已经打印完成，不能更换；如需重打请联系老师",
    "completed": "订单已完成，不能更换文件",
    "cancelled": "订单已取消，不能更换文件",
}


def _note(text: str) -> str:
    """操作日志的 note 列是 VARCHAR(255)，长文件名必须截断，不能让换文件失败。"""
    return text if len(text) <= 255 else text[:252] + "..."


def system_amount_cents(order: Order, page_count: int) -> int:
    """按订单现有的打印参数重算新页数对应的金额（分）。"""
    return calc_amount_cents(
        int(page_count),
        int(order.print_side or 2),
        int(order.color_mode or 0),
        int(order.copies or 1),
    )


def _validate_new_file(file_key: str, page_count: int, reason: str | None) -> None:
    limits = get_order_rules()["limits"]
    if not file_key or not str(file_key).startswith("files/"):
        raise BusinessError("文件标识无效，请重新上传", code=40041)
    if not 1 <= int(page_count) <= int(limits["max_page_count"]):
        raise BusinessError(f"页数必须在 1 到 {limits['max_page_count']} 之间", code=40045)
    if reason and len(reason) > int(limits["max_remark_length"]):
        raise BusinessError(f"换文件原因最多 {limits['max_remark_length']} 个字符", code=40047)

    # 延迟导入：下单服务依赖本模块写初始版本，模块级互相导入会成环。
    # 延迟导入也让测试或回归脚本替换 order_service._validate_file_freshness 时能生效。
    from app.services.order_service import _validate_file_freshness

    _validate_file_freshness(file_key)


def _next_version_no(db: Session, order_id: int) -> int:
    current = db.execute(
        select(func.coalesce(func.max(OrderFileVersion.version_no), 0)).where(
            OrderFileVersion.order_id == order_id
        )
    ).scalar_one()
    return int(current) + 1


def _push_version(
    db: Session,
    order: Order,
    *,
    file_key: str,
    file_name: str | None,
    page_count: int,
    amount_cents: int,
    operator: str,
    note: str | None = None,
) -> OrderFileVersion:
    """把旧版本标记为非当前，再写入一条新的生效版本。"""
    db.execute(
        update(OrderFileVersion)
        .where(OrderFileVersion.order_id == order.id, OrderFileVersion.is_current == 1)
        .values(is_current=0)
        .execution_options(synchronize_session=False)
    )
    version = OrderFileVersion(
        order_id=order.id,
        version_no=_next_version_no(db, order.id),
        file_key=file_key,
        file_name=file_name,
        page_count=int(page_count),
        amount_cents=int(amount_cents),
        is_current=1,
        operator=operator,
        note=note,
    )
    db.add(version)
    db.flush()
    return version


def create_initial_version(db: Session, order: Order) -> OrderFileVersion | None:
    """下单时记录第 1 个文件版本；没有文件标识的历史数据不记录。"""
    if not order.file_key:
        return None
    version = OrderFileVersion(
        order_id=order.id,
        version_no=_next_version_no(db, order.id),
        file_key=order.file_key,
        file_name=order.file_name,
        page_count=int(order.page_count or 0),
        amount_cents=int(order.amount_cents or 0),
        is_current=1,
        operator="student",
        note="下单时的原始文件",
    )
    db.add(version)
    db.flush()
    return version


def get_pending_change(db: Session, order_id: int) -> OrderFileChange | None:
    return db.execute(
        select(OrderFileChange)
        .where(
            OrderFileChange.order_id == order_id,
            OrderFileChange.status == CHANGE_PENDING,
        )
        .order_by(OrderFileChange.id.desc())
        .limit(1)
    ).scalar_one_or_none()


def change_to_view(change: OrderFileChange) -> dict:
    """待确认请求的对外字段，学生端与老师端共用同一套键名。"""
    return {
        "id": change.id,
        "change_id": change.id,
        "order_id": change.order_id,
        "new_file_name": change.new_file_name,
        "new_page_count": change.new_page_count,
        "new_amount_cents": change.new_amount_cents,
        "submitted_at": change.created_at.isoformat() if change.created_at else None,
        "reason": change.reason,
        # 老师确认弹窗需要对照旧值；学生端也可以直接展示
        "old_file_name": change.old_file_name,
        "old_page_count": change.old_page_count,
        "old_amount_cents": change.old_amount_cents,
    }


def pending_change_view(db: Session, order_id: int) -> dict | None:
    change = get_pending_change(db, order_id)
    return change_to_view(change) if change else None


def batch_pending_changes(db: Session, order_ids: list[int]) -> dict[int, dict]:
    """一次取出一批订单的待确认请求，避免列表接口逐行查询。"""
    if not order_ids:
        return {}
    rows = db.execute(
        select(OrderFileChange).where(
            OrderFileChange.order_id.in_(order_ids),
            OrderFileChange.status == CHANGE_PENDING,
        )
    ).scalars()
    return {change.order_id: change_to_view(change) for change in rows if change}


def expire_pending_changes(
    db: Session, order_id: int, reason: str, operator: str
) -> int:
    """订单进入不可换文件的状态时，让未处理的请求失效，避免悬空。"""
    result = db.execute(
        update(OrderFileChange)
        .where(
            OrderFileChange.order_id == order_id,
            OrderFileChange.status == CHANGE_PENDING,
        )
        .values(
            status=CHANGE_CANCELLED,
            pending_flag=None,
            reviewed_at=datetime.now(),
            reviewed_by=operator,
            review_note=reason,
        )
        .execution_options(synchronize_session=False)
    )
    count = int(result.rowcount or 0)
    if count:
        logger.info("订单 %s 有 %d 条待确认换文件请求已失效：%s", order_id, count, reason)
    return count


def _record_file_event(
    db: Session,
    order: Order,
    *,
    action: str,
    note: str,
    operator: str,
    event: str,
    payload: dict,
) -> None:
    record_log(db, order, action, order.status, order.status, note, operator)
    get_notifier(db).send(
        user_id=order.user_id,
        event=event,
        payload={
            "order_no": order.order_no,
            "pickup_code": order.pickup_code,
            "status": order.status,
            "status_label": status_label(order.status),
            **payload,
        },
        order_id=order.id,
    )


def _apply_immediately(db: Session, order: Order, payload) -> dict:
    """待支付订单直接换：页数与金额重算，订单号、取件码、状态不变。"""
    old = {
        "file_name": order.file_name,
        "page_count": order.page_count,
        "amount_cents": order.amount_cents,
    }
    new_amount = system_amount_cents(order, payload.page_count)

    # 待支付状态本不该有待确认请求；万一有，先让它失效，避免两套数据打架
    expire_pending_changes(db, order.id, "订单处于待支付，直接换文件", "system")

    _push_version(
        db,
        order,
        file_key=payload.file_key,
        file_name=payload.file_name,
        page_count=payload.page_count,
        amount_cents=new_amount,
        operator="student",
        note=payload.reason or "学生换文件（待支付，直接生效）",
    )

    order.file_key = payload.file_key
    order.file_name = payload.file_name
    order.page_count = int(payload.page_count)
    order.amount_cents = new_amount

    note = (
        f"学生换文件：{old['file_name'] or '原文件'}（{old['page_count'] or 0} 页）→ "
        f"{payload.file_name or payload.file_key}（{payload.page_count} 页），"
        f"金额 {old['amount_cents'] or 0} 分 → {new_amount} 分"
    )
    _record_file_event(
        db,
        order,
        action="file_replace",
        note=_note(note),
        operator="student",
        event="order_file_replaced",
        payload={
            "old_file_name": old["file_name"],
            "old_page_count": old["page_count"],
            "old_amount_cents": old["amount_cents"],
            "new_file_name": payload.file_name,
            "new_page_count": int(payload.page_count),
            "new_amount_cents": new_amount,
        },
    )
    db.commit()
    db.refresh(order)
    logger.info("订单 %s 待支付换文件已生效：%s 页 / %s 分", order.order_no,
                order.page_count, order.amount_cents)
    return {"applied": True, "change": None}


def _create_pending_change(db: Session, order: Order, payload) -> OrderFileChange:
    if get_pending_change(db, order.id) is not None:
        raise BusinessError("该订单已有待老师确认的换文件请求，请等待处理", code=40910)

    new_amount = system_amount_cents(order, payload.page_count)
    change = OrderFileChange(
        order_id=order.id,
        status=CHANGE_PENDING,
        pending_flag=1,
        old_file_key=order.file_key,
        old_file_name=order.file_name,
        old_page_count=order.page_count,
        old_amount_cents=order.amount_cents,
        new_file_key=payload.file_key,
        new_file_name=payload.file_name,
        new_page_count=int(payload.page_count),
        new_amount_cents=new_amount,
        reason=payload.reason,
        requested_by="student",
    )
    db.add(change)
    try:
        db.flush()
    except IntegrityError as exc:
        # 唯一索引兜底：两个请求同时提交时只有一个能拿到待确认名额
        db.rollback()
        raise BusinessError("该订单已有待老师确认的换文件请求，请等待处理", code=40910) from exc

    note = (
        f"学生申请换文件：{order.file_name or '原文件'}（{order.page_count or 0} 页）→ "
        f"{payload.file_name or payload.file_key}（{payload.page_count} 页），"
        f"系统重算 {new_amount} 分，等待老师确认"
    )
    _record_file_event(
        db,
        order,
        action="file_change_request",
        note=_note(note),
        operator="student",
        event="order_file_change_pending",
        payload={
            "change_id": change.id,
            "old_file_name": order.file_name,
            "old_page_count": order.page_count,
            "old_amount_cents": order.amount_cents,
            "new_file_name": payload.file_name,
            "new_page_count": int(payload.page_count),
            "new_amount_cents": new_amount,
        },
    )
    db.commit()
    db.refresh(change)
    logger.info(
        "订单 %s 换文件请求 #%s 待老师确认：%s 页 / %s 分",
        order.order_no, change.id, change.new_page_count, change.new_amount_cents,
    )
    return change


def replace_order_file(db: Session, order: Order, payload) -> dict:
    """学生换文件入口。返回 {"applied": bool, "change": OrderFileChange | None}。"""
    _validate_new_file(payload.file_key, payload.page_count, payload.reason)

    # 行锁把同一张单的并发换文件请求串起来，避免两个版本号同时算成同一个
    db.refresh(order, with_for_update=True)

    if order.status in IMMEDIATE_STATUSES:
        return _apply_immediately(db, order, payload)

    if order.status in CONFIRM_REQUIRED_STATUSES:
        change = _create_pending_change(db, order, payload)
        return {"applied": False, "change": change}

    message = REJECT_MESSAGES.get(
        order.status,
        f"订单当前状态为「{status_label(order.status)}」，不能更换文件",
    )
    raise BusinessError(message, code=40911)


def _paid_total(db: Session, order_id: int) -> int:
    return int(
        db.execute(
            select(func.coalesce(func.sum(PaymentLog.amount_cents), 0)).where(
                PaymentLog.order_id == order_id
            )
        ).scalar_one()
    )


def _reject_change(db: Session, order: Order, change: OrderFileChange, operator: str) -> dict:
    now = datetime.now()
    change.status = CHANGE_REJECTED
    change.pending_flag = None
    change.reviewed_at = now
    change.reviewed_by = operator
    change.review_note = "老师驳回换文件请求"

    note = (
        f"老师驳回换文件：{change.new_file_name or change.new_file_key}"
        f"（{change.new_page_count} 页）保持原文件与金额"
    )
    _record_file_event(
        db,
        order,
        action="file_change_reject",
        note=_note(note),
        operator=operator,
        event="order_file_change_rejected",
        payload={
            "change_id": change.id,
            "new_file_name": change.new_file_name,
            "new_page_count": change.new_page_count,
        },
    )
    db.commit()
    db.refresh(order)
    logger.info("订单 %s 换文件请求 #%s 被老师驳回", order.order_no, change.id)
    return {"approved": False, "change": change}


def _approve_change(
    db: Session, order: Order, change: OrderFileChange, operator: str,
    amount_cents: int | None,
) -> dict:
    if order.status not in CONFIRMABLE_STATUSES:
        raise BusinessError(
            f"订单当前状态为「{status_label(order.status)}」，不能确认换文件", code=40912
        )

    final_amount = (
        int(change.new_amount_cents) if amount_cents is None else int(amount_cents)
    )
    if final_amount < 0:
        raise BusinessError("金额不能为负数", code=40061)

    old = {
        "file_name": order.file_name,
        "page_count": order.page_count,
        "amount_cents": order.amount_cents,
    }
    paid_total = _paid_total(db, order.id)

    _push_version(
        db,
        order,
        file_key=change.new_file_key,
        file_name=change.new_file_name,
        page_count=change.new_page_count,
        amount_cents=final_amount,
        operator="admin",
        note=f"老师确认换文件（{operator}）",
    )

    order.file_key = change.new_file_key
    order.file_name = change.new_file_name
    order.page_count = int(change.new_page_count)
    order.amount_cents = final_amount

    # 重新排到队尾：不改订单号与创建时间，只写重新排队时间
    now = datetime.now()
    order.requeue_at = now

    status_note = ""
    if order.status == STATUS_ABNORMAL:
        change_status(
            db, order, STATUS_QUEUING, operator=operator,
            note="换文件确认通过，异常单恢复排队",
        )
        status_note = "，异常单已恢复排队"

    change.status = CHANGE_APPROVED
    change.pending_flag = None
    change.final_amount_cents = final_amount
    change.reviewed_at = now
    change.reviewed_by = operator
    change.review_note = "老师确认换文件"

    # 差额口径与核销时一致：新价高于已收的部分现场补收，低于的部分不退
    supplement = max(0, final_amount - paid_total)
    if final_amount > paid_total:
        price_note = f"应补收 {supplement} 分，取件时现场收取"
    elif final_amount < paid_total:
        price_note = "新价低于已付，多付部分不退"
    else:
        price_note = "与已付金额一致，无需补收"

    note = (
        f"老师确认换文件：{old['file_name'] or '原文件'}（{old['page_count'] or 0} 页）→ "
        f"{change.new_file_name or change.new_file_key}（{change.new_page_count} 页），"
        f"金额 {old['amount_cents'] or 0} 分 → {final_amount} 分；"
        f"{price_note}；已重新排到队尾{status_note}"
    )
    _record_file_event(
        db,
        order,
        action="file_change_approve",
        note=_note(note),
        operator=operator,
        event="order_file_change_approved",
        payload={
            "change_id": change.id,
            "old_file_name": old["file_name"],
            "old_page_count": old["page_count"],
            "old_amount_cents": old["amount_cents"],
            "new_file_name": change.new_file_name,
            "new_page_count": change.new_page_count,
            "new_amount_cents": final_amount,
            "paid_total_cents": paid_total,
            "supplement_cents": supplement,
            "refund_cents": 0,
            "message": price_note,
        },
    )
    db.commit()
    db.refresh(order)
    logger.info(
        "订单 %s 换文件请求 #%s 已通过：%s 页 / %s 分，重新排队",
        order.order_no, change.id, order.page_count, order.amount_cents,
    )
    return {"approved": True, "change": change, "supplement_cents": supplement,
            "paid_total_cents": paid_total}


def confirm_file_change(
    db: Session, order: Order, approve: bool, amount_cents: int | None, operator: str
) -> dict:
    """老师确认或驳回换文件请求。"""
    db.refresh(order, with_for_update=True)
    change = get_pending_change(db, order.id)
    if change is None:
        raise BusinessError("该订单没有待确认的换文件请求", code=40403)

    if not approve:
        return _reject_change(db, order, change, operator)
    return _approve_change(db, order, change, operator, amount_cents)


__all__ = [
    "CONFIRM_REQUIRED_STATUSES",
    "IMMEDIATE_STATUSES",
    "batch_pending_changes",
    "change_to_view",
    "confirm_file_change",
    "create_initial_version",
    "expire_pending_changes",
    "get_pending_change",
    "pending_change_view",
    "replace_order_file",
    "system_amount_cents",
]
