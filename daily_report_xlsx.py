"""
Phase DT-5: Generator XLSX Digest Laporan Harian Tim.

Dipanggil oleh routes/daily_reports.py di endpoint:
- GET /api/daily-reports/export.xlsx?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&role=X

Layout ringkas dgn openpyxl -- 2 sheet (Ringkasan + Detail Karyawan).
Palet Luxury UMAR (charcoal header, gold accent, cream zebra).

Payload shape sama dgn PDF exporter -- lihat daily_report_pdf.py.
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

_HEADER_FILL = PatternFill("solid", fgColor="1D1D1B")
_HEADER_FONT = Font(name="Calibri", size=10, bold=True, color="FFFFFF")
_TOTAL_FILL = PatternFill("solid", fgColor="FBF7EE")
_TOTAL_FONT = Font(name="Calibri", size=10, bold=True, color="1D1D1B")
_TITLE_FONT = Font(name="Calibri", size=14, bold=True, color="1D1D1B")
_SUB_FONT = Font(name="Calibri", size=9, color="6B7280")
_INK_FONT = Font(name="Calibri", size=10, color="1F1F1D")
_BORDER = Border(
    left=Side(style="thin", color="E5DFCE"),
    right=Side(style="thin", color="E5DFCE"),
    top=Side(style="thin", color="E5DFCE"),
    bottom=Side(style="thin", color="E5DFCE"),
)
_PCT_FMT = '0.0%'
_INT_FMT = '#,##0'

_ROLE_LABEL = {
    "sales": "Sales", "ops": "Operasional", "finance": "Finance",
    "management": "Management", "admin": "Admin",
}


def _role_label(role_key):
    return _ROLE_LABEL.get(role_key or "", role_key or "-")


def _write_header_row(ws, row, headers):
    for col, h in enumerate(headers, 1):
        c = ws.cell(row=row, column=col, value=h)
        c.fill = _HEADER_FILL
        c.font = _HEADER_FONT
        c.alignment = Alignment(horizontal="left", vertical="center")
        c.border = _BORDER


def _autosize(ws, widths):
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w


def _write_meta_block(ws, title, subtitle):
    ws["A1"] = title
    ws["A1"].font = _TITLE_FONT
    ws["A2"] = subtitle
    ws["A2"].font = _SUB_FONT


def _rate_font(pct):
    """Warna angka rate sesuai severity: >=80 hijau, >=50 amber, <50 merah."""
    if pct >= 80:
        return Font(name="Calibri", size=10, bold=True, color="166534")
    if pct >= 50:
        return Font(name="Calibri", size=10, bold=True, color="B45309")
    return Font(name="Calibri", size=10, bold=True, color="B91C1C")


def build_daily_digest_xlsx(payload) -> bytes:
    """payload: {date_from, date_to, total_days, role_filter, users:[...],
    generated_by, generated_at}. Returns raw XLSX bytes (ZIP magic PK\\x03\\x04)."""
    wb = Workbook()
    date_from = payload.get("date_from", "-")
    date_to = payload.get("date_to", "-")
    role_filter = payload.get("role_filter") or "semua"
    role_label = (_role_label(role_filter)
                  if role_filter != "semua" else "Semua Role")

    ws = wb.active
    ws.title = "Ringkasan"
    _write_meta_block(
        ws,
        f"Digest Laporan Harian {date_from} s/d {date_to}",
        (f"Filter: {role_label}  |  Digenerate "
         f"{payload.get('generated_by', '-')} pada "
         f"{payload.get('generated_at', '-')}"),
    )

    users = payload.get("users") or []
    total_users = len(users)
    total_days = payload.get("total_days", 0)
    total_submits = sum((u.get("submitted_days") or 0) for u in users)
    total_possible = total_users * total_days if total_users and total_days else 0
    avg_submit_rate = (
        round(total_submits * 100.0 / total_possible, 1) if total_possible else 0.0)
    total_tasks = sum((u.get("total_tasks") or 0) for u in users)
    total_done = sum((u.get("done_tasks") or 0) for u in users)
    avg_done_rate = round(total_done * 100.0 / total_tasks, 1) if total_tasks else 0.0

    kpi_rows = [
        ("Total Karyawan", total_users, None),
        ("Rentang Hari", total_days, None),
        ("Total Submit", total_submits, None),
        ("Total Slot Kemungkinan", total_possible, None),
        ("Rate Submit Rata-Rata", avg_submit_rate / 100.0, _PCT_FMT),
        ("Total Task", total_tasks, None),
        ("Total Task Selesai", total_done, None),
        ("Rate Task Done", avg_done_rate / 100.0, _PCT_FMT),
    ]
    for i, (label, val, fmt) in enumerate(kpi_rows):
        row = 4 + i
        ws.cell(row=row, column=1, value=label).font = Font(
            size=9, bold=True, color="B8860B")
        c = ws.cell(row=row, column=2, value=val)
        c.font = Font(size=11, bold=True, color="1D1D1B")
        if fmt:
            c.number_format = fmt
    _autosize(ws, [30, 22])

    ws2 = wb.create_sheet("Detail Karyawan")
    _write_meta_block(
        ws2, "Detail per Karyawan",
        f"Sorted by submit rate ASC (yang jarang lapor di atas)  |  "
        f"{date_from} s/d {date_to}",
    )
    _write_header_row(ws2, 4, [
        "Karyawan", "Username", "Role",
        "Submit / Total Hari", "Rate Submit", "Total Task",
        "Task Done", "Done Rate",
    ])
    sorted_users = sorted(users, key=lambda u: (u.get("submit_rate_pct") or 0))
    r = 5
    for u in sorted_users:
        submitted = u.get("submitted_days") or 0
        rate_pct = u.get("submit_rate_pct") or 0
        done_pct = u.get("done_pct") or 0

        ws2.cell(row=r, column=1, value=u.get("user_name") or "-").font = _INK_FONT
        ws2.cell(row=r, column=2, value=u.get("username") or "-").font = _INK_FONT
        ws2.cell(row=r, column=3, value=_role_label(u.get("role"))).font = _INK_FONT
        ws2.cell(row=r, column=4, value=f"{submitted} / {total_days}").font = _INK_FONT
        c5 = ws2.cell(row=r, column=5, value=rate_pct / 100.0)
        c5.number_format = _PCT_FMT
        c5.font = _rate_font(rate_pct)
        ws2.cell(row=r, column=6, value=u.get("total_tasks") or 0).number_format = _INT_FMT
        ws2.cell(row=r, column=7, value=u.get("done_tasks") or 0).number_format = _INT_FMT
        c8 = ws2.cell(row=r, column=8, value=done_pct / 100.0)
        c8.number_format = _PCT_FMT
        c8.font = _rate_font(done_pct)
        for col in range(1, 9):
            ws2.cell(row=r, column=col).border = _BORDER
        r += 1

    if sorted_users:
        ws2.cell(row=r, column=1, value="TOTAL")
        ws2.cell(row=r, column=4,
                 value=f"{total_submits} / {total_possible}")
        c_rate = ws2.cell(row=r, column=5, value=avg_submit_rate / 100.0)
        c_rate.number_format = _PCT_FMT
        ws2.cell(row=r, column=6, value=total_tasks).number_format = _INT_FMT
        ws2.cell(row=r, column=7, value=total_done).number_format = _INT_FMT
        c_done = ws2.cell(row=r, column=8, value=avg_done_rate / 100.0)
        c_done.number_format = _PCT_FMT
        for col in range(1, 9):
            cell = ws2.cell(row=r, column=col)
            cell.fill = _TOTAL_FILL
            cell.font = _TOTAL_FONT
            cell.border = _BORDER
    _autosize(ws2, [22, 14, 14, 18, 12, 12, 12, 12])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()
