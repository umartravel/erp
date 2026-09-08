"""
Phase 8a-2: Helper Excel Export.

Ringkas: build_workbook(sheets: list[Sheet]) -> BytesIO (siap dikirim
sbg StreamingResponse). Setiap Sheet punya title + headers + rows.

Style: header row bold + fill emerald muted + auto-fit column width
(perkiraan dari max content length -- openpyxl tidak punya native
auto-fit, jadi kita hitung sendiri).

Dipakai oleh routes/exports.py untuk 4 endpoint bulanan.
"""
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter


HEADER_FONT = Font(bold=True, color="1D1D1B")
HEADER_FILL = PatternFill(start_color="F4F1EA", end_color="F4F1EA", fill_type="solid")
HEADER_ALIGN = Alignment(horizontal="center", vertical="center", wrap_text=True)


@dataclass
class Sheet:
    """Definisi 1 tab/sheet di workbook."""
    title: str
    headers: list[str]
    rows: list[list[Any]] = field(default_factory=list)
    # Optional: kolom yg mau di-format currency (0-based index)
    currency_columns: set[int] = field(default_factory=set)


def _column_width(header: str, sample_values: list[Any]) -> float:
    """Perkiraan lebar kolom -- max panjang string dari header + up to 30 rows."""
    max_len = len(header)
    for v in sample_values[:30]:
        s = str(v) if v is not None else ""
        if len(s) > max_len:
            max_len = len(s)
    # +2 padding, cap 60 supaya tidak kelewat lebar
    return min(60.0, max(8.0, max_len + 2))


def build_workbook(sheets: list[Sheet]) -> BytesIO:
    """Bangun workbook dari list Sheet, return BytesIO siap kirim."""
    wb = Workbook()
    # openpyxl default bikin 1 sheet "Sheet" -- kita replace pakai yg pertama.
    wb.remove(wb.active)

    for sheet in sheets:
        # Excel sheet title max 31 char + no colon/backslash/etc.
        safe_title = sheet.title[:31]
        for bad in "[]:*?/\\":
            safe_title = safe_title.replace(bad, "_")
        ws = wb.create_sheet(title=safe_title)

        # Header
        for col_idx, header in enumerate(sheet.headers, start=1):
            cell = ws.cell(row=1, column=col_idx, value=header)
            cell.font = HEADER_FONT
            cell.fill = HEADER_FILL
            cell.alignment = HEADER_ALIGN

        # Rows
        for r_idx, row in enumerate(sheet.rows, start=2):
            for c_idx, val in enumerate(row, start=1):
                ws.cell(row=r_idx, column=c_idx, value=val)

        # Currency format (Rp Indonesia)
        for c_idx in sheet.currency_columns:
            col_letter = get_column_letter(c_idx + 1)  # +1 karena openpyxl 1-based
            for r_idx in range(2, len(sheet.rows) + 2):
                ws[f"{col_letter}{r_idx}"].number_format = '"Rp "#,##0'

        # Column widths
        for c_idx, header in enumerate(sheet.headers):
            sample = [r[c_idx] if c_idx < len(r) else None for r in sheet.rows]
            ws.column_dimensions[get_column_letter(c_idx + 1)].width = _column_width(header, sample)

        # Freeze header row
        ws.freeze_panes = "A2"

    # Fallback: kalau list sheets kosong, tambah 1 sheet dummy supaya file valid
    if not sheets:
        wb.create_sheet(title="Empty")

    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf
