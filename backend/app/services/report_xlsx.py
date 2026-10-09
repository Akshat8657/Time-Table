"""One Excel writer for every report (Phase 6): a header band, the key numbers, then the table - print-ready, filterable.

A report is a plain dict (the same one the screen shows):
  title, sheet, scope_text, meta                      -> the header band
  xl_summary [(label, value), ...]                    -> the key numbers under it
  columns   [{key, label, type: text|int|float|pct|state, width, tones: {value: tone}}]
  rows      [{key: value, ...}]
  sections  [{title, columns, rows}]                  -> extra tables, each on its own sheet
Lists are joined with ", ", booleans become Yes / No, and a text that starts with "=" stays text (never a formula).
"""
from __future__ import annotations

from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

from .exporter import _save, now_text

TONE_FILL = {"success": "D1FAE5", "warning": "FEF3C7", "danger": "FEE2E2", "info": "DBEAFE", "neutral": "F1F5F9"}
THIN = Side(style="thin", color="CDD3E3")
BOX = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def _fill(hex_color: str) -> PatternFill:
    return PatternFill("solid", start_color=hex_color, end_color=hex_color)


def _value(raw: Any) -> Any:
    if raw is None:
        return ""
    if isinstance(raw, bool):
        return "Yes" if raw else "No"
    if isinstance(raw, (list, tuple, set)):
        return ", ".join(str(_value(x)) for x in raw)
    return raw


def _band(ws, title: str, scope_text: str, meta: str, college_name: str, ncols: int) -> int:
    ncols = max(ncols, 4)
    for row, (text, font) in enumerate((
        (college_name, Font(bold=True, size=16, color="4F46E5")),
        (scope_text, Font(size=11, color="556079")),
        (title, Font(bold=True, size=14)),
        (f"{meta}   |   Printed {now_text()}", Font(size=9, color="556079")),
    ), start=1):
        ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=ncols)
        ws.cell(row, 1, text).font = font
    ws.row_dimensions[1].height = 24
    return 6


def _table(ws, row: int, columns: list[dict[str, Any]], rows: list[dict[str, Any]]) -> int:
    """Writes the header + data rows starting at `row`; returns the last row used."""
    for c, col in enumerate(columns, start=1):
        cell = ws.cell(row, c, col["label"])
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = _fill("4F46E5")
        cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        cell.border = BOX
        ws.column_dimensions[get_column_letter(c)].width = max(col.get("width", 14), len(col["label"]) + 2)
    ws.row_dimensions[row].height = 28
    for r, data in enumerate(rows, start=row + 1):
        for c, col in enumerate(columns, start=1):
            raw = data.get(col["key"])
            cell = ws.cell(r, c, _value(raw))
            cell.border = BOX
            kind = col.get("type", "text")
            if kind == "pct" and isinstance(raw, (int, float)):
                cell.number_format = '0"%"'
                cell.alignment = Alignment(horizontal="center")
            elif kind == "float" and isinstance(raw, (int, float)):
                cell.number_format = "0.0"
                cell.alignment = Alignment(horizontal="center")
            elif kind == "int":
                cell.alignment = Alignment(horizontal="center")
            else:
                cell.alignment = Alignment(vertical="center", wrap_text=True)
            tone = (col.get("tones") or {}).get(str(raw))
            if tone:
                cell.fill = _fill(TONE_FILL[tone])
                cell.font = Font(bold=True)
    last = row + len(rows)
    if rows:
        ws.auto_filter.ref = f"A{row}:{get_column_letter(len(columns))}{last}"
    ws.freeze_panes = ws.cell(row + 1, 1)
    return last


def _page(ws, header_row: int, last_row: int) -> None:
    ws.page_setup.orientation = "landscape"
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.print_options.horizontalCentered = True
    ws.page_margins.left = ws.page_margins.right = 0.4
    ws.print_title_rows = f"{header_row}:{header_row}"
    ws.oddFooter.left.text = "TimeTablePro"
    ws.oddFooter.right.text = "Page &P of &N"
    ws.print_area = f"A1:{get_column_letter(max(ws.max_column, 1))}{max(last_row, header_row)}"


def report_xlsx(report: dict[str, Any], college_name: str) -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = str(report.get("sheet") or "Report")[:31]
    columns = report["columns"]
    row = _band(ws, report["title"], report.get("scope_text", ""), report.get("meta", ""), college_name, len(columns))
    for label, value in report.get("xl_summary", []):   # the key numbers
        ws.cell(row, 1, label).font = Font(bold=True, color="556079")
        ws.cell(row, 2, _value(value)).font = Font(bold=True)
        row += 1
    if report.get("xl_summary"):
        row += 1
    header_row = row
    last = _table(ws, header_row, columns, report["rows"])
    if not report["rows"]:
        ws.cell(header_row + 1, 1, report.get("empty_text", "Nothing to show for these filters.")).font = Font(italic=True, color="556079")
        last = header_row + 1
    _page(ws, header_row, last)
    for section in report.get("sections", []):
        sheet = wb.create_sheet(str(section["title"])[:31])
        h = _band(sheet, section["title"], report.get("scope_text", ""), report.get("meta", ""), college_name, len(section["columns"]))
        end = _table(sheet, h, section["columns"], section["rows"])
        _page(sheet, h, end)
    return _save(wb)
