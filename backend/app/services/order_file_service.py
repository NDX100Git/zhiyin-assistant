"""订单文件清单：创建、读取、逐文件替换与总额重算。

一单多文件的数据以 `order_files` 为准，`orders.file_key/file_name` 保留为第一份
文件的兼容字段；`orders.page_count` 与 `orders.amount_cents` 始终是整单汇总。
老订单没有清单行时按订单上的单文件字段兜底，读写都不会因为迁移没跑到而失效。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.order import Order
from app.models.order_file import OrderFile
from app.services.pricing import calc_amount_cents
from app.utils.exceptions import BusinessError

FILE_KEY_PREFIX = "files/"


def file_to_dict(row: OrderFile) -> dict[str, Any]:
    """当前文件清单的一项。学生端与老师端共用同一套键名。"""
    return {
        "file_index": int(row.file_index),
        "file_key": row.file_key,
        "file_name": row.file_name,
        "page_count": int(row.page_count or 0),
        "amount_cents": int(row.amount_cents or 0),
    }


def _legacy_file_dict(order: Order) -> dict[str, Any]:
    """迁移还没回填时的兜底：把订单上的单文件字段当成第 1 份文件。"""
    return {
        "file_index": 1,
        "file_key": order.file_key,
        "file_name": order.file_name,
        "page_count": int(order.page_count or 0),
        "amount_cents": int(order.amount_cents or 0),
    }


def _query_files(db: Session, order_id: int) -> list[OrderFile]:
    return list(
        db.execute(
            select(OrderFile)
            .where(OrderFile.order_id == order_id)
            .order_by(OrderFile.file_index.asc(), OrderFile.id.asc())
        ).scalars()
    )


def ensure_files(db: Session, order: Order) -> list[OrderFile]:
    """取当前文件行；老订单没有行时补一行，保证逐文件替换能落地。"""
    rows = _query_files(db, order.id)
    if rows:
        return rows
    if not order.file_key:
        return []
    row = OrderFile(
        order_id=order.id,
        file_index=1,
        file_key=order.file_key,
        file_name=order.file_name,
        page_count=int(order.page_count or 0),
        amount_cents=int(order.amount_cents or 0),
    )
    db.add(row)
    db.flush()
    return [row]


def files_view(db: Session, order: Order) -> list[dict[str, Any]]:
    """单张订单的文件清单（对外字段）。"""
    rows = _query_files(db, order.id)
    if rows:
        return [file_to_dict(row) for row in rows]
    if order.file_key:
        return [_legacy_file_dict(order)]
    return []


def batch_files_view(
    db: Session, orders: list[Order]
) -> dict[int, list[dict[str, Any]]]:
    """一次取出一批订单的文件清单，列表接口避免逐行查询。"""
    if not orders:
        return {}
    order_ids = [order.id for order in orders]
    rows = list(
        db.execute(
            select(OrderFile)
            .where(OrderFile.order_id.in_(order_ids))
            .order_by(OrderFile.order_id.asc(), OrderFile.file_index.asc())
        ).scalars()
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.order_id, []).append(file_to_dict(row))

    result: dict[int, list[dict[str, Any]]] = {}
    for order in orders:
        views = grouped.get(order.id)
        if views:
            result[order.id] = views
        elif order.file_key:
            result[order.id] = [_legacy_file_dict(order)]
        else:
            result[order.id] = []
    return result


def normalize_file_items(payload, limits: dict) -> list[dict[str, Any]]:
    """把请求里的单文件字段或文件清单统一成按顺序编号的清单。"""
    raw = getattr(payload, "files", None)
    if raw is None:
        if not getattr(payload, "file_key", None):
            raise BusinessError("请选择要打印的文件", code=40041)
        raw_items = [
            {
                "file_key": payload.file_key,
                "file_name": getattr(payload, "file_name", None),
                "page_count": getattr(payload, "page_count", None),
                "file_index": 1,
            }
        ]
    else:
        raw_items = [item.model_dump() for item in raw]

    if not raw_items:
        raise BusinessError("请至少选择一个文件", code=40041)
    max_files = int(limits.get("max_files", 20))
    if len(raw_items) > max_files:
        raise BusinessError(f"一次最多打印 {max_files} 个文件", code=40048)

    max_pages = int(limits["max_page_count"])
    seen_keys: set[str] = set()
    seen_indexes: set[int] = set()
    items: list[dict[str, Any]] = []
    for position, item in enumerate(raw_items, start=1):
        file_key = str(item.get("file_key") or "")
        if not file_key.startswith(FILE_KEY_PREFIX):
            raise BusinessError("文件标识无效，请重新上传", code=40041)
        if file_key in seen_keys:
            raise BusinessError("同一份文件不能重复加入订单，需要多份请改份数", code=40049)
        seen_keys.add(file_key)

        page_count = item.get("page_count")
        if not isinstance(page_count, int) or not 1 <= page_count <= max_pages:
            raise BusinessError(f"页数必须在 1 到 {max_pages} 之间", code=40045)

        file_index = item.get("file_index") or position
        if int(file_index) in seen_indexes:
            raise BusinessError("文件顺序重复，请刷新后重试", code=40048)
        seen_indexes.add(int(file_index))
        items.append(
            {
                "file_key": file_key,
                "file_name": item.get("file_name"),
                "page_count": int(page_count),
                "file_index": int(file_index),
            }
        )

    # 按调用方给的文件顺序排序，再重新编号成连续的 1..n，避免前端拿到空洞编号
    items.sort(key=lambda row: row["file_index"])
    for position, item in enumerate(items, start=1):
        item["file_index"] = position

    total_pages = sum(int(item["page_count"]) for item in items)
    if total_pages > max_pages:
        raise BusinessError(
            f"所有文件合计页数不能超过 {max_pages} 页（当前 {total_pages} 页）",
            code=40045,
        )
    return items


def create_order_files(db: Session, order: Order, items: list[dict[str, Any]]) -> list[OrderFile]:
    """按清单写入订单文件行，并把整单汇总写到订单兼容字段。"""
    rows: list[OrderFile] = []
    for item in items:
        row = OrderFile(
            order_id=order.id,
            file_index=int(item["file_index"]),
            file_key=item["file_key"],
            file_name=item.get("file_name"),
            page_count=int(item["page_count"]),
            amount_cents=0,
        )
        db.add(row)
        rows.append(row)
    db.flush()
    recalc_order_totals(order, rows)
    return rows


def resolve_file_row(
    db: Session, order: Order, file_index: int | None
) -> tuple[list[OrderFile], OrderFile]:
    """定位要替换或下载的那一份文件。

    多文件订单必须指定 file_index；单文件订单省略时默认第 1 份。
    """
    rows = ensure_files(db, order)
    if not rows:
        raise BusinessError("该订单没有关联文件", code=40402)
    if file_index is None:
        if len(rows) == 1:
            return rows, rows[0]
        raise BusinessError("该订单有多份文件，请指定要操作的文件序号", code=40048)

    target = next((row for row in rows if int(row.file_index) == int(file_index)), None)
    if target is None:
        raise BusinessError(
            f"订单里没有第 {file_index} 份文件，请刷新后重试", code=40405
        )
    return rows, target


def recalc_order_totals(
    order: Order, rows: list[OrderFile], final_amount_cents: int | None = None
) -> tuple[int, int]:
    """按当前打印参数重算每份文件金额与整单汇总。

    返回 (总页数, 系统总价)。`final_amount_cents` 只覆盖订单总额（老师在确认
    换文件时可以改最终价），每份文件的行金额仍保留系统价，便于对账。
    """
    total_pages = 0
    system_total = 0
    for row in rows:
        line_amount = calc_amount_cents(
            int(row.page_count or 0),
            int(order.print_side or 2),
            int(order.color_mode or 0),
            int(order.copies or 1),
        )
        row.amount_cents = line_amount
        total_pages += int(row.page_count or 0)
        system_total += line_amount

    order.page_count = total_pages
    order.amount_cents = system_total if final_amount_cents is None else int(final_amount_cents)
    # 兼容旧字段：订单上的 file_key/file_name 始终指向第 1 份文件
    first = rows[0]
    order.file_key = first.file_key
    order.file_name = first.file_name
    return total_pages, system_total


def is_file_unconfirmed(order: Order) -> bool:
    """文件被替换后，老师还没确认看到过。"""
    return order.file_changed_at is not None and order.files_seen_at is None


__all__ = [
    "batch_files_view",
    "create_order_files",
    "ensure_files",
    "file_to_dict",
    "files_view",
    "is_file_unconfirmed",
    "normalize_file_items",
    "recalc_order_totals",
    "resolve_file_row",
]
