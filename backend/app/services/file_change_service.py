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
from app.models.order_file import OrderFile
from app.models.order_file_change import (
    CHANGE_APPROVED,
    CHANGE_CANCELLED,
    CHANGE_PENDING,
    CHANGE_REJECTED,
    OrderFileChange,
)
from app.models.order_file_version import OrderFileVersion
from app.models.payment_log import PaymentLog
from app.services import queue_snapshot
from app.services.file_service import ensure_files_owned_by
from app.services.notifier import get_notifier
from app.services.order_file_service import (
    ensure_files,
    line_amount_cents,
    recalc_order_totals,
    resolve_file_row,
    row_print_params,
)
from app.services.order_state import (
    STATUS_ABNORMAL,
    STATUS_PENDING_PAY,
    STATUS_QUEUING,
    change_status,
    record_log,
    status_label,
)
from app.services.pricing import ensure_amount_allowed
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


def db_now(db: Session) -> datetime:
    """数据库时钟的「现在」，本模块写时间列一律用它。

    为什么不能取应用进程的 `datetime.now()`：`orders.created_at` 由数据库
    `CURRENT_TIMESTAMP` 落库，而队列按 `COALESCE(requeue_at, created_at)` 升序排。
    重排队时间若取应用时钟，一旦应用机比数据库慢，`requeue_at` 就会早于已有的
    `created_at`，刚换完文件的单被排到队首而不是队尾。两边统一取数据库时间，
    排队位置只依赖一个时钟。
    """
    return db.execute(select(func.current_timestamp())).scalar_one()


def file_system_amount_cents(order: Order, row, page_count: int | None = None) -> int:
    """按该份文件自己的打印参数算金额（分）。换文件重算一律用它，不再用订单级参数。"""
    return line_amount_cents(order, row, page_count)


def file_print_pages(order: Order, row, page_count: int | None = None) -> int:
    """该份文件的打印页数 = 页数 × 份数；份数沿用该行原值。"""
    _print_side, _color_mode, _paper, copies = row_print_params(order, row)
    pages = int(row.page_count or 0) if page_count is None else int(page_count)
    return pages * copies


def _validate_new_file(
    db: Session, user, file_key: str, page_count: int, reason: str | None
) -> None:
    limits = get_order_rules()["limits"]
    if not file_key or not str(file_key).startswith("files/"):
        raise BusinessError("文件标识无效，请重新上传", code=40041)
    if not 1 <= int(page_count) <= int(limits["max_page_count"]):
        raise BusinessError(f"页数必须在 1 到 {limits['max_page_count']} 之间", code=40045)
    if reason and len(reason) > int(limits["max_remark_length"]):
        raise BusinessError(f"换文件原因最多 {limits['max_remark_length']} 个字符", code=40047)

    ensure_files_owned_by(db, user, [file_key])

    # 延迟导入：下单服务依赖本模块写初始版本，模块级互相导入会成环。
    # 延迟导入也让测试或回归脚本替换 order_service._validate_file_freshness 时能生效。
    from app.services.order_service import (
        _validate_file_freshness,
        validate_declared_page_counts,
    )

    _validate_file_freshness(file_key)
    validate_declared_page_counts(
        db,
        [{"file_key": file_key, "page_count": int(page_count), "file_index": 1}],
    )


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
    file_index: int,
    file_key: str,
    file_name: str | None,
    page_count: int,
    amount_cents: int,
    operator: str,
    note: str | None = None,
) -> OrderFileVersion:
    """把同一份文件的旧版本标记为非当前，再写入一条新的生效版本。"""
    db.execute(
        update(OrderFileVersion)
        .where(
            OrderFileVersion.order_id == order.id,
            OrderFileVersion.file_index == int(file_index),
            OrderFileVersion.is_current == 1,
        )
        .values(is_current=0)
        .execution_options(synchronize_session=False)
    )
    version = OrderFileVersion(
        order_id=order.id,
        file_index=int(file_index),
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


def create_initial_version(
    db: Session, order: Order, files: list | None = None
) -> OrderFileVersion | None:
    """下单时按文件清单逐份记录第 1 个版本；没有文件的历史数据不记录。"""
    rows = files if files is not None else ensure_files(db, order)
    if not rows:
        return None

    next_no = _next_version_no(db, order.id)
    versions: list[OrderFileVersion] = []
    for row in rows:
        version = OrderFileVersion(
            order_id=order.id,
            file_index=int(row.file_index),
            version_no=next_no,
            file_key=row.file_key,
            file_name=row.file_name,
            page_count=int(row.page_count or 0),
            amount_cents=int(row.amount_cents or 0),
            is_current=1,
            operator="student",
            note="下单时的原始文件",
        )
        db.add(version)
        versions.append(version)
        next_no += 1
    db.flush()
    return versions[0]


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


def change_to_view(change: OrderFileChange, copies: int = 1) -> dict:
    """待确认请求的对外字段，学生端与老师端共用同一套键名。

    `old_page_count` / `new_page_count` / `old_amount_cents` / `new_amount_cents`
    是订单总额与总页数（与单文件时代语义一致）；`*_file_*` 字段才是被替换的
    那一份文件自己的页数、打印页数与行金额，老师确认弹窗用后者显示「换的是哪一份」。
    换文件不改份数，所以新旧打印页数都用该行原份数 × 页数算出来。
    """
    count = int(copies or 1)
    old_file_pages = int(change.old_file_page_count or 0)
    new_file_pages = int(change.new_file_page_count or 0)
    return {
        "id": change.id,
        "change_id": change.id,
        "order_id": change.order_id,
        "file_index": int(change.file_index or 1),
        "new_file_name": change.new_file_name,
        "new_page_count": change.new_page_count,
        "new_amount_cents": change.new_amount_cents,
        "new_file_page_count": change.new_file_page_count,
        "new_file_print_pages": new_file_pages * count,
        "new_file_amount_cents": change.new_file_amount_cents,
        "submitted_at": change.created_at.isoformat() if change.created_at else None,
        "reason": change.reason,
        # 老师确认弹窗需要对照旧值；学生端也可以直接展示
        "old_file_name": change.old_file_name,
        "old_page_count": change.old_page_count,
        "old_amount_cents": change.old_amount_cents,
        "old_file_page_count": change.old_file_page_count,
        "old_file_print_pages": old_file_pages * count,
        "old_file_amount_cents": change.old_file_amount_cents,
        "file_copies": count,
    }


def _file_copies(db: Session, order_id: int, file_index: int | None) -> int:
    """取该行原来的份数；老数据没行时回落到订单级份数。"""
    row = db.execute(
        select(OrderFile).where(
            OrderFile.order_id == order_id,
            OrderFile.file_index == int(file_index or 1),
        )
    ).scalar_one_or_none()
    if row is not None and row.copies is not None:
        return int(row.copies)
    order = db.get(Order, order_id)
    return int(order.copies or 1) if order is not None else 1


def pending_change_view(db: Session, order_id: int) -> dict | None:
    change = get_pending_change(db, order_id)
    if change is None:
        return None
    return change_to_view(change, _file_copies(db, order_id, change.file_index))


def batch_pending_changes(db: Session, order_ids: list[int]) -> dict[int, dict]:
    """一次取出一批订单的待确认请求，避免列表接口逐行查询。"""
    if not order_ids:
        return {}
    rows = list(
        db.execute(
            select(OrderFileChange).where(
                OrderFileChange.order_id.in_(order_ids),
                OrderFileChange.status == CHANGE_PENDING,
            )
        ).scalars()
    )
    if not rows:
        return {}
    # 份数按行取：一次查完这批订单的文件行，不逐条请求
    copies_by_slot: dict[tuple[int, int], int] = {}
    order_copies: dict[int, int] = {}
    file_rows = list(
        db.execute(
            select(OrderFile).where(
                OrderFile.order_id.in_([change.order_id for change in rows])
            )
        ).scalars()
    )
    for row in file_rows:
        if row.copies is not None:
            copies_by_slot[(int(row.order_id), int(row.file_index))] = int(row.copies)
    for order in db.execute(
        select(Order).where(Order.id.in_([change.order_id for change in rows]))
    ).scalars():
        order_copies[int(order.id)] = int(order.copies or 1)

    result: dict[int, dict] = {}
    for change in rows:
        copies = copies_by_slot.get(
            (int(change.order_id), int(change.file_index or 1)),
            order_copies.get(int(change.order_id), 1),
        )
        result[change.order_id] = change_to_view(change, copies)
    return result


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
            reviewed_at=db_now(db),
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


def _ensure_total_pages(order: Order, target, new_page_count: int) -> int:
    """换一份文件后整单页数不能超过配置上限。"""
    max_pages = int(get_order_rules()["limits"]["max_page_count"])
    total = int(order.page_count or 0) - int(target.page_count or 0) + int(new_page_count)
    if total > max_pages:
        raise BusinessError(
            f"换完后整单合计 {total} 页，超过上限 {max_pages} 页", code=40045
        )
    return total


def _apply_immediately(db: Session, order: Order, payload) -> dict:
    """待支付订单直接换：页数与金额重算，订单号、取件码、状态不变。"""
    rows, target = resolve_file_row(db, order, getattr(payload, "file_index", None))
    _ensure_total_pages(order, target, payload.page_count)

    old = {
        "file_name": target.file_name,
        "page_count": target.page_count,
        "print_pages": int(target.print_pages or 0),
        "file_amount_cents": target.amount_cents,
        "total_page_count": order.page_count,
        "total_print_page_count": int(order.print_page_count or 0),
        "total_amount_cents": order.amount_cents,
        "file_index": int(target.file_index),
    }

    # 待支付状态本不该有待确认请求；万一有，先让它失效，避免两套数据打架
    expire_pending_changes(db, order.id, "订单处于待支付，直接换文件", "system")

    target.file_key = payload.file_key
    target.file_name = payload.file_name
    target.page_count = int(payload.page_count)
    total_pages, new_amount = recalc_order_totals(order, rows)

    _push_version(
        db,
        order,
        file_index=target.file_index,
        file_key=payload.file_key,
        file_name=payload.file_name,
        page_count=payload.page_count,
        amount_cents=target.amount_cents,
        operator="student",
        note=payload.reason or "学生换文件（待支付，直接生效）",
    )

    # 文件变了，老师之前「看到」的状态作废，直到重新下载确认
    now = db_now(db)
    order.file_changed_at = now
    order.files_seen_at = None

    file_label = f"第 {old['file_index']} 份文件" if len(rows) > 1 else "文件"
    note = (
        f"学生换{file_label}：{old['file_name'] or '原文件'}（{old['page_count'] or 0} 页）→ "
        f"{payload.file_name or payload.file_key}（{payload.page_count} 页）；"
        f"整单 {old['total_page_count'] or 0} 页 / {old['total_amount_cents'] or 0} 分 → "
        f"{total_pages} 页 / {new_amount} 分"
    )
    _record_file_event(
        db,
        order,
        action="file_replace",
        note=_note(note),
        operator="student",
        event="order_file_replaced",
        payload={
            "file_index": old["file_index"],
            "old_file_name": old["file_name"],
            "old_page_count": old["page_count"],
            "old_print_pages": old["print_pages"],
            "old_amount_cents": old["file_amount_cents"],
            "old_total_page_count": old["total_page_count"],
            "old_total_print_page_count": old["total_print_page_count"],
            "old_total_amount_cents": old["total_amount_cents"],
            "new_file_name": payload.file_name,
            "new_page_count": int(payload.page_count),
            "new_print_pages": int(target.print_pages or 0),
            "new_amount_cents": int(target.amount_cents),
            "new_total_page_count": total_pages,
            "new_total_print_page_count": int(order.print_page_count or 0),
            "new_total_amount_cents": new_amount,
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

    rows, target = resolve_file_row(db, order, getattr(payload, "file_index", None))
    new_total_pages = _ensure_total_pages(order, target, payload.page_count)
    # 只重算被换的那一份：其余行的行金额按各自参数原样累加
    new_file_amount = file_system_amount_cents(order, target, payload.page_count)
    new_amount = sum(
        file_system_amount_cents(order, row) for row in rows if row is not target
    ) + new_file_amount
    new_file_print_pages = file_print_pages(order, target, payload.page_count)
    new_total_print_pages = (
        int(order.print_page_count or 0)
        - int(target.print_pages or 0)
        + new_file_print_pages
    )

    change = OrderFileChange(
        order_id=order.id,
        status=CHANGE_PENDING,
        pending_flag=1,
        file_index=int(target.file_index),
        old_file_key=target.file_key,
        old_file_name=target.file_name,
        # 这两个是订单总额快照，保持单文件时代的语义
        old_page_count=order.page_count,
        old_amount_cents=order.amount_cents,
        old_file_page_count=target.page_count,
        old_file_amount_cents=target.amount_cents,
        new_file_key=payload.file_key,
        new_file_name=payload.file_name,
        # 新文件自己的页数，以及换完后的整单总页数
        new_file_page_count=int(payload.page_count),
        new_page_count=new_total_pages,
        new_amount_cents=new_amount,
        new_file_amount_cents=new_file_amount,
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

    file_label = f"第 {target.file_index} 份文件" if len(rows) > 1 else "文件"
    note = (
        f"学生申请换{file_label}：{target.file_name or '原文件'}"
        f"（{target.page_count or 0} 页 → {payload.page_count} 页，"
        f"打印 {target.print_pages or 0} 页 → {new_file_print_pages} 页）→ "
        f"{payload.file_name or payload.file_key}，"
        f"整单 {order.page_count or 0} 页 / {order.amount_cents or 0} 分 → "
        f"{new_total_pages} 页 / {new_amount} 分，等待老师确认"
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
            "file_index": int(target.file_index),
            "old_file_name": target.file_name,
            "old_page_count": target.page_count,
            "old_print_pages": int(target.print_pages or 0),
            "old_amount_cents": target.amount_cents,
            "old_total_page_count": order.page_count,
            "old_total_amount_cents": order.amount_cents,
            "new_file_name": payload.file_name,
            "new_page_count": int(payload.page_count),
            "new_print_pages": new_file_print_pages,
            "new_amount_cents": new_file_amount,
            "new_total_page_count": new_total_pages,
            "new_total_print_page_count": new_total_print_pages,
            "new_total_amount_cents": new_amount,
        },
    )
    db.commit()
    db.refresh(change)
    logger.info(
        "订单 %s 换文件请求 #%s 待老师确认：第 %s 份 %s 页 / 整单 %s 分",
        order.order_no, change.id, change.file_index, change.new_file_page_count,
        change.new_amount_cents,
    )
    return change


def replace_order_file(db: Session, order: Order, payload, user) -> dict:
    """学生换文件入口。返回 {"applied": bool, "change": OrderFileChange | None}。"""
    _validate_new_file(db, user, payload.file_key, payload.page_count, payload.reason)

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
    now = db_now(db)
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


def _new_file_page_count(change: OrderFileChange, target: OrderFile) -> int:
    """换进来的那一份文件自己的页数。

    `new_file_page_count` 是这一份文件的页数，`new_page_count` 是整单总页数，
    在多文件订单上两者不是一回事，不能互相回退。老的待确认请求没有单文件页数时，
    只能用整单页数的变化量把这一份推出来：
    该行原页数 + (新整单页数 - 老整单页数)。
    直接回退到整单总页数，会把整单页数写进这一行，页数、行金额与打印页数全错位。
    两个整单页数也缺时，保持该行原页数不变，不动比写错安全。
    """
    if change.new_file_page_count is not None:
        return int(change.new_file_page_count)
    own_pages = int(target.page_count or 0)
    if change.old_page_count is None or change.new_page_count is None:
        return own_pages
    return max(0, own_pages + int(change.new_page_count) - int(change.old_page_count))


def _approve_change(
    db: Session, order: Order, change: OrderFileChange, operator: str,
    amount_cents: int | None,
) -> dict:
    if order.status not in CONFIRMABLE_STATUSES:
        raise BusinessError(
            f"订单当前状态为「{status_label(order.status)}」，不能确认换文件", code=40912
        )

    # 换文件后的新金额同样要过护栏：只挡负数时把 30 元敲成 300 元会直接落库，
    # 学生换完文件当场多付十倍
    final_amount = ensure_amount_allowed(
        int(change.new_amount_cents) if amount_cents is None else int(amount_cents),
        int(change.new_amount_cents or 0) if amount_cents is not None else 0,
    )

    rows, target = resolve_file_row(db, order, change.file_index)
    new_file_pages = _new_file_page_count(change, target)
    _ensure_total_pages(order, target, new_file_pages)

    old = {
        "file_index": int(target.file_index),
        "file_name": target.file_name,
        "page_count": target.page_count,
        "print_pages": int(target.print_pages or 0),
        "file_amount_cents": target.amount_cents,
        "total_page_count": order.page_count,
        "total_print_page_count": int(order.print_page_count or 0),
        "total_amount_cents": order.amount_cents,
    }
    paid_total = _paid_total(db, order.id)

    target.file_key = change.new_file_key
    target.file_name = change.new_file_name
    target.page_count = new_file_pages
    total_pages, system_total = recalc_order_totals(
        order, rows, final_amount_cents=final_amount
    )

    _push_version(
        db,
        order,
        file_index=target.file_index,
        file_key=change.new_file_key,
        file_name=change.new_file_name,
        page_count=new_file_pages,
        # 单文件订单的版本金额就是整单成交价（包含老师改价）；多文件时保留该文件
        # 自己的系统行金额，整单成交价在 orders.amount_cents 上，版本表不做分摊。
        amount_cents=final_amount if len(rows) == 1 else target.amount_cents,
        operator="admin",
        note=f"老师确认换文件（{operator}）",
    )

    # 重新排到队尾：不改订单号与创建时间，只写重新排队时间。
    # 文件已替换，老师之前的「已看到」作废，等重新下载后再确认。
    # 「现在」取数据库时钟：created_at 也是数据库时间，两边不同源就会排错位置。
    now = db_now(db)
    order.requeue_at = now
    order.file_changed_at = now
    order.files_seen_at = None

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

    file_label = f"第 {old['file_index']} 份文件" if len(rows) > 1 else "文件"
    note = (
        f"老师确认换{file_label}：{old['file_name'] or '原文件'}"
        f"（{old['page_count'] or 0} 页 / 打印 {old['print_pages']} 页）→ "
        f"{change.new_file_name or change.new_file_key}"
        f"（{new_file_pages} 页 / 打印 {int(target.print_pages or 0)} 页）；"
        f"整单 {old['total_page_count'] or 0} 页 / {old['total_amount_cents'] or 0} 分 → "
        f"{total_pages} 页 / {final_amount} 分（系统重算 {system_total} 分）；"
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
            "file_index": old["file_index"],
            "old_file_name": old["file_name"],
            "old_page_count": old["page_count"],
            "old_print_pages": old["print_pages"],
            "old_amount_cents": old["file_amount_cents"],
            "old_total_page_count": old["total_page_count"],
            "old_total_print_page_count": old["total_print_page_count"],
            "old_total_amount_cents": old["total_amount_cents"],
            "new_file_name": change.new_file_name,
            "new_page_count": new_file_pages,
            "new_print_pages": int(target.print_pages or 0),
            "new_amount_cents": int(target.amount_cents),
            "new_total_page_count": total_pages,
            "new_total_print_page_count": int(order.print_page_count or 0),
            "new_total_amount_cents": final_amount,
            "system_total_amount_cents": system_total,
            "paid_total_cents": paid_total,
            "supplement_cents": supplement,
            "refund_cents": 0,
            "message": price_note,
        },
    )
    queue_snapshot.invalidate()  # 换了排到队尾，旧快照里的位置立刻作废
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
    "file_print_pages",
    "file_system_amount_cents",
    "get_pending_change",
    "pending_change_view",
    "replace_order_file",
]
