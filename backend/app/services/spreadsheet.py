"""Excel 归一化：把「页数取决于排版」的表格变成「页数确定」。

同一个汇总表，页数会随四件事变化：列宽与缩放决定横向切几刀、默认只打印活动工作表、
打印区域与手动分页符、隐藏行列与空白区造出的空页。学生看到的「1 页」和实际出纸的
「12 页」一旦不一致，收银台就要吵价格。

所以转换前统一版式，让页数只由内容决定：

- 缩到一页宽，纵向不限（宽表不会横向切碎）
- 打印区域收紧到真正有内容的行列，去掉尾部空行空列
- 清掉手动分页符与重复标题行，避免额外分页
- 只打可见工作表；隐藏表是学生主动藏起来的，不该出纸

改口径只改 config/spreadsheet_rules.json。
"""

from __future__ import annotations

import logging
from pathlib import Path

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


def _apply_page_setup(worksheet, bounds: tuple[int, int, int, int], rules: dict) -> None:
    from openpyxl.utils import get_column_letter

    first_row, first_col, last_row, last_col = bounds
    worksheet.print_area = f"{get_column_letter(first_col)}{first_row}:{get_column_letter(last_col)}{last_row}"

    worksheet.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    worksheet.page_setup.fitToWidth = int(rules.get("fit_to_width", 1))
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


def normalize_spreadsheet(source: Path, work_dir: Path) -> Path:
    """按统一版式重存一份表格，返回新文件路径。原文件不动。"""
    rules = get_spreadsheet_rules()
    try:
        workbook = load_workbook(source, data_only=False)
    except Exception as exc:
        raise BusinessError("表格文件打不开，可能已损坏或不是 Excel 格式", code=50043) from exc

    printable = [sheet for sheet in workbook.worksheets if sheet.sheet_state == "visible"]
    if not printable:
        raise BusinessError("表格里没有可见的工作表，请先取消隐藏再上传", code=50044)

    kept = 0
    for worksheet in printable:
        bounds = _content_bounds(worksheet)
        if bounds is None:
            worksheet.sheet_state = "hidden"  # 空表不出纸，也不占页
            logger.info("工作表「%s」没有任何内容，跳过", worksheet.title)
            continue
        _apply_page_setup(worksheet, bounds, rules)
        kept += 1

    if kept == 0:
        raise BusinessError("表格里没有任何内容，请检查后再上传", code=50045)

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
