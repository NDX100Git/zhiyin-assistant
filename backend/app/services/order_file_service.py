"""订单文件清单：创建、读取、逐文件替换与总额重算。

一单多文件的数据以 `order_files` 为准，`orders.file_key/file_name` 保留为第一份
文件的兼容字段；`orders.page_count` 与 `orders.amount_cents` 始终是整单汇总。

每份文件可以有自己的打印参数（单双面、黑白彩色、纸张、份数），为空表示沿用订单级
同名字段。行金额一律用 `pricing.calc_amount_cents` 按行上的参数算，整单金额是各行之和；
整单打印页数是各行 `page_count × copies` 之和，文档页数仍是各行 `page_count` 之和。
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

# 文件清单项对外暴露的键：学生端与老师端（队列/详情/下载/开始打印/换文件确认）完全一致
FILE_VIEW_KEYS = (
    "file_index",
    "file_key",
    "file_name",
    "page_count",
    "print_pages",
    "print_side",
    "color_mode",
    "paper_size",
    "copies",
    "amount_cents",
)


def validate_print_params(
    print_side: Any, color_mode: Any, paper_size: Any, copies: Any, limits: dict
) -> tuple[int, int, str, int]:
    """校验一份文件的打印参数，错误码与中文提示沿用订单级的同一套。

    订单级参数与逐份参数都走这里，保证「单文件旧写法」与「逐份覆盖」口径一致。
    """
    if print_side not in (1, 2):
        raise BusinessError("打印方式只能是单面或双面", code=40042)
    if color_mode not in (0, 1):
        raise BusinessError("颜色模式只能是黑白或彩色", code=40043)
    max_copies = int(limits["max_copies"])
    if not isinstance(copies, int) or not 1 <= copies <= max_copies:
        raise BusinessError(f"份数必须在 1 到 {max_copies} 之间", code=40044)
    allowed_papers = limits["allowed_paper_sizes"]
    if paper_size not in allowed_papers:
        raise BusinessError(
            f"纸张规格不支持：{paper_size}，可选 {'、'.join(allowed_papers)}", code=40046
        )
    return int(print_side), int(color_mode), str(paper_size), int(copies)


def row_print_params(order: Order, row: OrderFile) -> tuple[int, int, str, int]:
    """该份文件生效的打印参数：行上有值用行上的，为空回落到订单级同名字段。"""
    print_side = row.print_side if row.print_side is not None else order.print_side
    color_mode = row.color_mode if row.color_mode is not None else order.color_mode
    paper_size = row.paper_size if row.paper_size else order.paper_size
    copies = row.copies if row.copies is not None else order.copies
    return (
        int(print_side if print_side is not None else 2),
        int(color_mode if color_mode is not None else 0),
        str(paper_size or "A4"),
        int(copies if copies is not None else 1),
    )


def line_amount_cents(order: Order, row: OrderFile, page_count: int | None = None) -> int:
    """按该行自己的打印参数算行金额（分）；page_count 不传时用行上的页数。"""
    print_side, color_mode, _paper, copies = row_print_params(order, row)
    pages = int(row.page_count or 0) if page_count is None else int(page_count)
    return calc_amount_cents(pages, print_side, color_mode, copies)


def file_to_dict(row: OrderFile, order: Order) -> dict[str, Any]:
    """当前文件清单的一项。学生端与老师端共用同一套键名。"""
    print_side, color_mode, paper_size, copies = row_print_params(order, row)
    page_count = int(row.page_count or 0)
    return {
        "file_index": int(row.file_index),
        "file_key": row.file_key,
        "file_name": row.file_name,
        "page_count": page_count,
        "print_pages": int(row.print_pages or 0) or page_count * copies,
        "print_side": print_side,
        "color_mode": color_mode,
        "paper_size": paper_size,
        "copies": copies,
        "amount_cents": int(row.amount_cents or 0),
    }


def _legacy_file_dict(order: Order) -> dict[str, Any]:
    """迁移还没回填时的兜底：把订单上的单文件字段当成第 1 份文件。"""
    page_count = int(order.page_count or 0)
    copies = int(order.copies or 1)
    return {
        "file_index": 1,
        "file_key": order.file_key,
        "file_name": order.file_name,
        "page_count": page_count,
        "print_pages": int(order.print_page_count or 0) or page_count * copies,
        "print_side": int(order.print_side if order.print_side is not None else 2),
        "color_mode": int(order.color_mode if order.color_mode is not None else 0),
        "paper_size": str(order.paper_size or "A4"),
        "copies": copies,
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
    page_count = int(order.page_count or 0)
    copies = int(order.copies or 1)
    row = OrderFile(
        order_id=order.id,
        file_index=1,
        file_key=order.file_key,
        file_name=order.file_name,
        page_count=page_count,
        print_side=order.print_side,
        color_mode=order.color_mode,
        paper_size=order.paper_size,
        copies=copies,
        print_pages=page_count * copies,
        amount_cents=int(order.amount_cents or 0),
    )
    db.add(row)
    db.flush()
    return [row]


def files_view(db: Session, order: Order) -> list[dict[str, Any]]:
    """单张订单的文件清单（对外字段）。"""
    rows = _query_files(db, order.id)
    if rows:
        return [file_to_dict(row, order) for row in rows]
    if order.file_key:
        return [_legacy_file_dict(order)]
    return []


def batch_files_view(
    db: Session, orders: list[Order]
) -> dict[int, list[dict[str, Any]]]:
    """一次取出一批订单的文件清单，列表接口避免逐行查询。"""
    if not orders:
        return {}
    by_id = {int(order.id): order for order in orders}
    order_ids = list(by_id)
    rows = list(
        db.execute(
            select(OrderFile)
            .where(OrderFile.order_id.in_(order_ids))
            .order_by(OrderFile.order_id.asc(), OrderFile.file_index.asc())
        ).scalars()
    )
    grouped: dict[int, list[dict[str, Any]]] = {}
    for row in rows:
        grouped.setdefault(row.order_id, []).append(
            file_to_dict(row, by_id[int(row.order_id)])
        )

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


def system_total_cents(db: Session, order: Order) -> int:
    """整单系统价（分）：Σ 各份行金额。没有清单行的老订单按订单级参数兜底。"""
    rows = _query_files(db, order.id)
    if rows:
        return sum(line_amount_cents(order, row) for row in rows)
    return calc_amount_cents(
        int(order.page_count or 0),
        int(order.print_side if order.print_side is not None else 2),
        int(order.color_mode if order.color_mode is not None else 0),
        int(order.copies or 1),
    )


def _pick(item: dict, key: str, default):
    """逐份字段不传（或显式传 null）时继承订单级同名字段。"""
    value = item.get(key)
    return default if value is None else value


def item_amount_cents(item: dict[str, Any]) -> int:
    """清单项的行金额（分）= 单价 × 该份文档页数 × 该份份数。

    报价、下单落库与换文件重算都从这里取数，不存在第二套算价公式。
    """
    return calc_amount_cents(
        int(item["page_count"]),
        int(item["print_side"]),
        int(item["color_mode"]),
        int(item["copies"]),
    )


def items_totals(items: list[dict[str, Any]]) -> tuple[int, int, int]:
    """清单汇总：(文档总页数, 打印总页数, 系统总价)。下单时用它写订单兼容字段。"""
    total_pages = 0
    total_print_pages = 0
    amount_cents = 0
    for item in items:
        page_count = int(item["page_count"])
        copies = int(item["copies"])
        total_pages += page_count
        total_print_pages += page_count * copies
        amount_cents += item_amount_cents(item)
    return total_pages, total_print_pages, amount_cents


def quote_items(items: list[dict[str, Any]]) -> dict[str, Any]:
    """把规范化后的清单算成报价视图：逐份行金额 + 文档页数/打印页数/总额。

    返回结构与订单文件项同一套键名（FILE_VIEW_KEYS），学生端与老师端
    可以拿同一套归一化逻辑读报价与订单，不必区分两个来源。
    """
    total_pages, total_print_pages, amount_cents = items_totals(items)
    files = [
        {
            "file_index": int(item["file_index"]),
            "file_key": item["file_key"],
            "file_name": item.get("file_name"),
            "page_count": int(item["page_count"]),
            "print_pages": int(item["page_count"]) * int(item["copies"]),
            "print_side": int(item["print_side"]),
            "color_mode": int(item["color_mode"]),
            "paper_size": item["paper_size"],
            "copies": int(item["copies"]),
            "amount_cents": item_amount_cents(item),
        }
        for item in items
    ]
    return {
        "files": files,
        "page_count": total_pages,
        "print_page_count": total_print_pages,
        "amount_cents": amount_cents,
    }


def normalize_file_items(payload, limits: dict) -> list[dict[str, Any]]:
    """把请求里的单文件字段或文件清单统一成按顺序编号的清单。

    逐份打印参数（print_side / color_mode / paper_size / copies）不传时继承订单级
    同名字段；传了就按订单级同一套限制校验，错误码也用订单级的同一套。
    """
    order_print_side = getattr(payload, "print_side", None)
    order_color_mode = getattr(payload, "color_mode", None)
    order_paper_size = getattr(payload, "paper_size", None)
    order_copies = getattr(payload, "copies", None)

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

        print_side, color_mode, paper_size, copies = validate_print_params(
            _pick(item, "print_side", order_print_side),
            _pick(item, "color_mode", order_color_mode),
            _pick(item, "paper_size", order_paper_size),
            _pick(item, "copies", order_copies),
            limits,
        )

        file_index = item.get("file_index") or position
        if int(file_index) in seen_indexes:
            raise BusinessError("文件顺序重复，请刷新后重试", code=40048)
        seen_indexes.add(int(file_index))
        items.append(
            {
                "file_key": file_key,
                "file_name": item.get("file_name"),
                "page_count": int(page_count),
                "print_side": print_side,
                "color_mode": color_mode,
                "paper_size": paper_size,
                "copies": copies,
                "print_pages": int(page_count) * copies,
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
        page_count = int(item["page_count"])
        copies = int(item["copies"])
        row = OrderFile(
            order_id=order.id,
            file_index=int(item["file_index"]),
            file_key=item["file_key"],
            file_name=item.get("file_name"),
            page_count=page_count,
            print_side=int(item["print_side"]),
            color_mode=int(item["color_mode"]),
            paper_size=item["paper_size"],
            copies=copies,
            print_pages=page_count * copies,
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
    """按每份文件自己的打印参数重算行金额与整单汇总。

    返回 (文档总页数, 系统总价)。`final_amount_cents` 只覆盖订单总额（老师在确认
    换文件时可以改最终价），每份文件的行金额仍保留系统价，便于对账。

    `order.page_count` 仍是各份文档页数之和；`order.print_page_count` 是各份
    `page_count × copies` 之和。
    """
    total_pages = 0
    total_print_pages = 0
    system_total = 0
    for row in rows:
        print_side, color_mode, _paper, copies = row_print_params(order, row)
        page_count = int(row.page_count or 0)
        print_pages = page_count * copies
        line_amount = calc_amount_cents(page_count, print_side, color_mode, copies)
        row.print_pages = print_pages
        row.amount_cents = line_amount
        total_pages += page_count
        total_print_pages += print_pages
        system_total += line_amount

    order.page_count = total_pages
    order.print_page_count = total_print_pages
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
    "FILE_VIEW_KEYS",
    "batch_files_view",
    "create_order_files",
    "ensure_files",
    "file_to_dict",
    "files_view",
    "is_file_unconfirmed",
    "item_amount_cents",
    "items_totals",
    "line_amount_cents",
    "normalize_file_items",
    "quote_items",
    "recalc_order_totals",
    "resolve_file_row",
    "row_print_params",
    "system_total_cents",
    "validate_print_params",
]
