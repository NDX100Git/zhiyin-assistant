"""Excel 归一化：把「页数取决于排版」的表格变成「页数确定」。

同一个汇总表，页数会随四件事变化：列宽与缩放决定横向切几刀、默认只打印活动工作表、
打印区域与手动分页符、隐藏行列与空白区造出的空页。学生看到的「1 页」和实际出纸的
「12 页」一旦不一致，收银台就要吵价格。

所以转换前统一版式，让页数只由内容决定：

- 缩到一页宽，纵向不限（宽表不会横向切碎）
- 打印区域收紧到真正有内容的行列，去掉尾部空行空列
- 清掉手动分页符与重复标题行，避免额外分页
- 只打可见工作表；隐藏表是学生主动藏起来的，不该出纸

归一化时同时把「有效区域之外还有多少空行空列」与最终出纸的中位字号带出来，
供体检规则判断。这里只产出事实，不判断是否报问题。

改口径只改 config/spreadsheet_rules.json。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pymupdf
from openpyxl import load_workbook
from openpyxl.worksheet.page import PageMargins
from openpyxl.worksheet.pagebreak import ColBreak, RowBreak
from openpyxl.worksheet.properties import PageSetupProperties

from app.config import get_spreadsheet_rules
from app.utils.exceptions import BusinessError

logger = logging.getLogger(__name__)


def _is_blank(value: object) -> bool:
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _content_bounds(worksheet) -> tuple[int, int, int, int] | None:
    """真正有内容的行列范围；整张表都空则返回 None。"""
    max_row = worksheet.max_row or 0
    while max_row > 0 and all(_is_blank(c.value) for c in worksheet[max_row]):
        max_row -= 1
    if max_row == 0:
        return None

    last_col = 0
    for row in worksheet.iter_rows(max_row=max_row):
        for cell in row:
            if not _is_blank(cell.value) and cell.column > last_col:
                last_col = cell.column
    if last_col == 0:
        return None
    return 1, 1, max_row, last_col


def _apply_page_setup(
    worksheet, bounds: tuple[int, int, int, int], rules: dict, fit_to_width: int
) -> None:
    from openpyxl.utils import get_column_letter

    first_row, first_col, last_row, last_col = bounds
    worksheet.print_area = f"{get_column_letter(first_col)}{first_row}:{get_column_letter(last_col)}{last_row}"

    worksheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    worksheet.page_setup.fitToWidth = fit_to_width
    worksheet.page_setup.fitToHeight = 0  # 纵向不限，按内容往下分页
    worksheet.page_setup.orientation = str(rules.get("orientation", "landscape"))
    worksheet.page_setup.paperSize = worksheet.PAPERSIZE_A4

    margin_cm = float(rules.get("margin_cm", 1.0))
    margin_inch = margin_cm / 2.54
    worksheet.page_margins = PageMargins(
        left=margin_inch,
        right=margin_inch,
        top=margin_inch,
        bottom=margin_inch,
        header=0.0,
        footer=0.0,
    )
    worksheet.print_options.horizontalCentered = True

    # 手动分页符会把一张表切碎，且学生自己也不一定记得设过
    worksheet.row_breaks = RowBreak()
    worksheet.col_breaks = ColBreak()
    worksheet.print_title_rows = None
    # 横向分成多页时把第一列当标签列重复，否则第二页起不知道每行是谁
    worksheet.print_title_cols = "A:A" if fit_to_width > 1 else None


def normalize_spreadsheet(
    source: Path,
    work_dir: Path,
    fit_to_width: int = 1,
    stats: dict | None = None,
) -> Path:
    """按统一版式重存一份表格，返回新文件路径。原文件不动。

    fit_to_width 是横向占用几页：默认 1（缩到一页宽），列太多看不清时由调用方放宽。
    stats 不为 None 时，把每个可见工作表的有效区域与末尾空行空列写进去。
    """
    rules = get_spreadsheet_rules()
    try:
        workbook = load_workbook(source, data_only=False)
    except Exception as exc:
        raise BusinessError("表格文件打不开，可能已损坏或不是 Excel 格式", code=50043) from exc

    printable = [sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
    if not printable:
        raise BusinessError("表格里没有可见的工作表，请先取消隐藏再上传", code=50044)

    kept = 0
    sheet_stats: list[dict] = []
    for worksheet in printable:
        bounds = _content_bounds(worksheet)
        if bounds is None:
            worksheet.sheet_state = "hidden"  # 空表不出纸，也不占页
            logger.info("工作表「%s」没有任何内容，跳过", worksheet.title)
            continue
        _apply_page_setup(worksheet, bounds, rules, fit_to_width)
        kept += 1
        if stats is not None:
            first_row, first_col, last_row, last_col = bounds
            sheet_stats.append(
                {
                    "title": worksheet.title,
                    "content_rows": last_row - first_row + 1,
                    "content_columns": last_col - first_col + 1,
                    # openpyxl 的 max_row/max_column 会被格式或幽灵单元格撑大，
                    # 与有效区域相减就是学生自己也不一定看得见的尾部空白
                    "trailing_blank_rows": max(0, int(worksheet.max_row or 0) - last_row),
                    "trailing_blank_cols": max(0, int(worksheet.max_column or 0) - last_col),
                }
            )

    if kept == 0:
        raise BusinessError("表格里没有任何内容，请检查后再上传", code=50045)

    if stats is not None:
        stats["sheets"] = sheet_stats

    work_dir.mkdir(parents=True, exist_ok=True)
    target = work_dir / f"{source.stem}.normalized.xlsx"
    workbook.save(target)
    logger.info(
        "表格已归一版式：%s，打印 %d 个工作表，跳过隐藏 %d 个",
        source.name,
        kept,
        len(workbook.worksheets) - kept,
    )
    return target


def _print_font_percentile(pdf_path: Path) -> float | None:
    """转换结果里的中位字号。用中位数而不是最小值：少数脚注不该绑架整表版式。"""
    sizes: list[float] = []
    document = pymupdf.open(pdf_path)
    try:
        for page in document:
            for block in page.get_text("dict")["blocks"]:
                for line in block.get("lines", []):
                    for span in line.get("spans", []):
                        if span["text"].strip():
                            sizes.append(float(span["size"]))
    finally:
        document.close()
    if not sizes:
        return None
    sizes.sort()
    return sizes[len(sizes) // 2]


def render_pdf(source: Path, work_dir: Path, convert, stats: dict | None = None) -> Path:
    """把表格渲染成 PDF，并保证字不至于小到看不清。

    缩到一页宽是最省纸也最合直觉的做法，但列一多，缩放会把字压到五六号，
    出纸等于白印。所以先按一页宽试，中位字号低于阈值就把横向页数放宽一页重来，
    直到字够大或到上限。首列会作为标签列重复，横向分页也不会丢行头。

    stats 不为 None 时，把最终实际使用的横向页数、中位字号与各表空白区写进去；
    体检规则据此判断「列太多字号过小」与「末尾大片空行空列」。
    """
    rules = get_spreadsheet_rules()
    min_pt = float(rules.get("min_readable_font_pt", 8.0))
    max_width = max(1, int(rules.get("max_width_pages", 4)))

    pdf_path: Path | None = None
    attempt_stats: dict = {}
    for width_pages in range(1, max_width + 1):
        attempt_dir = work_dir / f"fit{width_pages}"
        attempt_dir.mkdir(parents=True, exist_ok=True)
        attempt_stats = {}
        prepared = normalize_spreadsheet(
            source, attempt_dir, fit_to_width=width_pages, stats=attempt_stats
        )
        pdf_path = convert(prepared, attempt_dir)
        font_pt = _print_font_percentile(pdf_path)
        attempt_stats.update(
            {
                "fit_to_width_pages": width_pages,
                "print_font_pt": font_pt,
                "min_readable_font_pt": min_pt,
            }
        )
        if font_pt is None or font_pt >= min_pt:
            logger.info(
                "表格渲染完成：%s，横向 %d 页，中位字号 %.1f pt",
                source.name,
                width_pages,
                font_pt or 0.0,
            )
            if stats is not None:
                stats.update(attempt_stats)
            return pdf_path
        logger.info(
            "表格缩到横向 %d 页后中位字号只有 %.1f pt，低于 %.1f pt，放宽一页重试",
            width_pages,
            font_pt,
            min_pt,
        )

    logger.warning("表格横向已放宽到 %d 页仍偏小，按最大宽度出纸：%s", max_width, source.name)
    if stats is not None:
        stats.update(attempt_stats)
    return pdf_path
