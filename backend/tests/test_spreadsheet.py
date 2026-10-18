"""表格归一版式的测试：不依赖 LibreOffice，纯文件级校验。

这些断言盯的是「页数确定」的前提：打印区域收紧、缩到一页宽、分页符清干净、
隐藏表不出纸。任何一条回退，同一个表格的页数就会随排版漂移。
"""

from __future__ import annotations

from pathlib import Path

import pytest
from openpyxl import Workbook, load_workbook
from openpyxl.worksheet.pagebreak import Break

from app.services.spreadsheet import normalize_spreadsheet
from app.utils.exceptions import BusinessError


def _wide_sheet(path: Path, rows: int = 30, cols: int = 12) -> Path:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "汇总"
    for row in range(1, rows + 1):
        for col in range(1, cols + 1):
            sheet.cell(row=row, column=col, value=f"R{row}C{col}")
    # 尾部留出空行空列，模拟学生表里常见的「幽灵区」
    sheet.cell(row=rows + 20, column=cols + 10, value=None)
    sheet.row_breaks.append(Break(id=10))
    workbook.save(path)
    return path


def test_表格归一化把打印区域收紧到有内容的行列(tmp_path: Path) -> None:
    source = _wide_sheet(tmp_path / "汇总表.xlsx", rows=30, cols=12)
    target = normalize_spreadsheet(source, tmp_path / "work")

    sheet = load_workbook(target).active
    # openpyxl 会把表名一起写进打印区域，这里只校验单元格范围
    assert str(sheet.print_area).endswith("$A$1:$L$30")


def test_表格归一化打开一页宽并清掉手动分页符(tmp_path: Path) -> None:
    source = _wide_sheet(tmp_path / "宽表.xlsx")
    target = normalize_spreadsheet(source, tmp_path / "work")

    sheet = load_workbook(target).active
    assert sheet.sheet_properties.pageSetUpPr.fitToPage is True
    assert sheet.page_setup.fitToWidth == 1
    assert sheet.page_setup.fitToHeight == 0
    assert len(sheet.row_breaks.brk) == 0


def test_隐藏工作表不参与打印(tmp_path: Path) -> None:
    workbook = Workbook()
    workbook.active.title = "要打印"
    workbook.active["A1"] = "明细"
    workbook.active["B2"] = "第二格"
    hidden = workbook.create_sheet("隐藏明细")
    hidden["A1"] = "不该出纸"
    hidden.sheet_state = "hidden"
    source = tmp_path / "多表.xlsx"
    workbook.save(source)

    target = normalize_spreadsheet(source, tmp_path / "work")
    result = load_workbook(target)
    assert result["隐藏明细"].sheet_state == "hidden"
    assert str(result["要打印"].print_area).endswith("$A$1:$B$2")


def test_看不见内容的工作表被跳过(tmp_path: Path) -> None:
    workbook = Workbook()
    workbook.active["A1"] = "有内容"
    empty = workbook.create_sheet("空白表")
    empty["C5"] = "   "
    source = tmp_path / "含空表.xlsx"
    workbook.save(source)

    target = normalize_spreadsheet(source, tmp_path / "work")
    result = load_workbook(target)
    assert result["空白表"].sheet_state == "hidden"


def test_整张表都空时明确报错(tmp_path: Path) -> None:
    workbook = Workbook()
    workbook.active["A1"] = None
    source = tmp_path / "空表.xlsx"
    workbook.save(source)

    with pytest.raises(BusinessError) as excinfo:
        normalize_spreadsheet(source, tmp_path / "work")
    assert "没有任何内容" in str(excinfo.value)


def test_损坏文件给出人话提示(tmp_path: Path) -> None:
    source = tmp_path / "坏表.xlsx"
    source.write_bytes(b"not an excel file")

    with pytest.raises(BusinessError) as excinfo:
        normalize_spreadsheet(source, tmp_path / "work")
    assert "打不开" in str(excinfo.value)
