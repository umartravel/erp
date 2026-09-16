"""
Phase F4: Generator Excel Analisis Keuangan (yearly + monthly).

Dipanggil oleh routes/finance_categories.py di endpoint:
- GET /api/finance/export/year.xlsx?year=YYYY
- GET /api/finance/export/month.xlsx?year=YYYY&month=MM

Layout ringkas dgn openpyxl -- 3-4 sheet per workbook, format ribuan IDR.
"""
import io

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter

_MONTH_NAMES = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]

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
_IDR_FMT = '_-"Rp" * #,##0_-;-"Rp" * #,##0_-;_-"Rp" * "-"_-;_-@_-'
_PCT_FMT = '0.0%'


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


def _write_kpi_grid(ws, start_row, kpis):
    """kpis: list of (label, value, is_currency)."""
    for i, (label, val, is_cur) in enumerate(kpis):
        row = start_row + i
        ws.cell(row=row, column=1, value=label).font = Font(size=9, bold=True, color="B8860B")
        c = ws.cell(row=row, column=2, value=val)
        c.font = Font(size=11, bold=True, color="1D1D1B")
        if is_cur:
            c.number_format = _IDR_FMT
    return start_row + len(kpis)


def _write_category_sheet(wb, sheet_name, cats, group_filter):
    ws = wb.create_sheet(sheet_name)
    label = "pemasukan" if group_filter == "income" else "pengeluaran"
    _write_meta_block(ws, sheet_name, f"Breakdown {label} per kategori (parent + subkategori)")
    _write_header_row(ws, 4, ["Kategori", "Nominal", "% Kontribusi"])

    filtered = [c for c in (cats or []) if c.get("group") == group_filter and c.get("total", 0) > 0]
    if not filtered:
        ws.cell(row=5, column=1, value=f"Tidak ada {label} tercatat.").font = _SUB_FONT
        _autosize(ws, [40, 20, 16])
        return

    r = 5
    grand = sum(c["total"] for c in filtered) or 1
    for c in filtered:
        ws.cell(row=r, column=1, value=c["name"]).font = Font(bold=True, color="1F1F1D")
        cell_total = ws.cell(row=r, column=2, value=int(c["total"]))
        cell_total.number_format = _IDR_FMT
        cell_total.font = Font(bold=True, color="1F1F1D")
        pct_cell = ws.cell(row=r, column=3, value=c["total"] / grand)
        pct_cell.number_format = _PCT_FMT
        for col in range(1, 4):
            ws.cell(row=r, column=col).border = _BORDER
        r += 1
        for sub in c.get("subcategories") or []:
            if not sub.get("total"):
                continue
            ws.cell(row=r, column=1, value=f"    {sub['name']}").font = Font(italic=True, color="6B7280")
            sc = ws.cell(row=r, column=2, value=int(sub["total"]))
            sc.number_format = _IDR_FMT
            spc = ws.cell(row=r, column=3, value=sub["total"] / grand)
            spc.number_format = _PCT_FMT
            for col in range(1, 4):
                ws.cell(row=r, column=col).border = _BORDER
            r += 1
    ws.cell(row=r, column=1, value="TOTAL")
    ws.cell(row=r, column=2, value=int(grand)).number_format = _IDR_FMT
    ws.cell(row=r, column=3, value=1.0).number_format = _PCT_FMT
    for col in range(1, 4):
        cell = ws.cell(row=r, column=col)
        cell.fill = _TOTAL_FILL
        cell.font = _TOTAL_FONT
        cell.border = _BORDER
    _autosize(ws, [42, 22, 16])


def _write_project_sheet(wb, projects):
    """Phase EX-6: sheet Project Breakdown -- 4 kolom Project|Count|Nominal|%."""
    ws = wb.create_sheet("Project Breakdown")
    _write_meta_block(ws, "Project Breakdown",
                      "Breakdown pengeluaran per project (dari Expense Report).")
    _write_header_row(ws, 4, ["Project", "Jumlah Expense", "Nominal", "% Kontribusi"])

    filtered = [p for p in (projects or []) if p.get("total", 0) > 0]
    if not filtered:
        ws.cell(row=5, column=1, value="Tidak ada expense project tercatat.").font = _SUB_FONT
        _autosize(ws, [42, 18, 22, 16])
        return

    r = 5
    grand = sum(p["total"] for p in filtered) or 1
    for p in filtered:
        ws.cell(row=r, column=1, value=p["project_name"]).font = Font(bold=True, color="1F1F1D")
        ws.cell(row=r, column=2, value=int(p.get("count", 0)))
        cell_total = ws.cell(row=r, column=3, value=int(p["total"]))
        cell_total.number_format = _IDR_FMT
        cell_total.font = Font(bold=True, color="1F1F1D")
        pct_cell = ws.cell(row=r, column=4, value=p["total"] / grand)
        pct_cell.number_format = _PCT_FMT
        for col in range(1, 5):
            ws.cell(row=r, column=col).border = _BORDER
        r += 1
    ws.cell(row=r, column=1, value="TOTAL")
    ws.cell(row=r, column=2, value=sum(int(p.get("count", 0)) for p in filtered))
    ws.cell(row=r, column=3, value=int(grand)).number_format = _IDR_FMT
    ws.cell(row=r, column=4, value=1.0).number_format = _PCT_FMT
    for col in range(1, 5):
        cell = ws.cell(row=r, column=col)
        cell.fill = _TOTAL_FILL
        cell.font = _TOTAL_FONT
        cell.border = _BORDER
    _autosize(ws, [42, 18, 22, 16])


def build_year_report_xlsx(payload) -> bytes:
    """payload: hasil summary_year + {'generated_by', 'generated_at'}."""
    wb = Workbook()
    year = payload.get("year", "-")

    ws = wb.active
    ws.title = "Ringkasan"
    _write_meta_block(
        ws, f"Analisis Keuangan {year}",
        f"Digenerate {payload.get('generated_by', '-')} pada {payload.get('generated_at', '-')}",
    )
    monthly = payload.get("monthly") or []
    surplus_count = sum(1 for m in monthly if m.get("net", 0) > 0)
    _write_kpi_grid(ws, 4, [
        ("Total Pemasukan", payload.get("total_income", 0), True),
        ("Total Pengeluaran", payload.get("total_expense", 0), True),
        ("Saldo Bersih", payload.get("net_saldo", 0), True),
        ("Bulan Surplus", f"{surplus_count} / 12", False),
    ])
    _autosize(ws, [26, 24])

    ws2 = wb.create_sheet("Tren Bulanan")
    _write_meta_block(ws2, f"Tren Bulanan {year}", "Ringkasan pemasukan/pengeluaran/saldo per bulan")
    _write_header_row(ws2, 4, ["Bulan", "Pemasukan", "Pengeluaran", "Saldo Bersih"])
    r = 5
    for m in monthly:
        ws2.cell(row=r, column=1, value=_MONTH_NAMES[m["month"]]).font = _INK_FONT
        ws2.cell(row=r, column=2, value=int(m.get("income", 0))).number_format = _IDR_FMT
        ws2.cell(row=r, column=3, value=int(m.get("expense", 0))).number_format = _IDR_FMT
        c = ws2.cell(row=r, column=4, value=int(m.get("net", 0)))
        c.number_format = _IDR_FMT
        c.font = Font(bold=True, color="166534" if m.get("net", 0) >= 0 else "B91C1C")
        for col in range(1, 5):
            ws2.cell(row=r, column=col).border = _BORDER
        r += 1
    ws2.cell(row=r, column=1, value="TOTAL").font = _TOTAL_FONT
    ws2.cell(row=r, column=2, value=int(payload.get("total_income", 0))).number_format = _IDR_FMT
    ws2.cell(row=r, column=3, value=int(payload.get("total_expense", 0))).number_format = _IDR_FMT
    ws2.cell(row=r, column=4, value=int(payload.get("net_saldo", 0))).number_format = _IDR_FMT
    for col in range(1, 5):
        cell = ws2.cell(row=r, column=col)
        cell.fill = _TOTAL_FILL
        cell.font = _TOTAL_FONT
        cell.border = _BORDER
    _autosize(ws2, [16, 20, 20, 22])

    _write_category_sheet(wb, "Pemasukan Kategori", payload.get("by_category"), "income")
    _write_category_sheet(wb, "Pengeluaran Kategori", payload.get("by_category"), "expense")
    _write_project_sheet(wb, payload.get("by_project"))  # Phase EX-6

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()


def build_month_report_xlsx(payload) -> bytes:
    """payload: hasil summary_month + {'generated_by', 'generated_at'}."""
    wb = Workbook()
    year = payload.get("year", "-")
    month = payload.get("month", 0)
    month_label = f"{_MONTH_NAMES[month]} {year}" if 1 <= month <= 12 else str(year)

    ws = wb.active
    ws.title = "Ringkasan"
    _write_meta_block(
        ws, f"Analisis Keuangan {month_label}",
        f"Digenerate {payload.get('generated_by', '-')} pada {payload.get('generated_at', '-')}",
    )
    _write_kpi_grid(ws, 4, [
        ("Total Pemasukan", payload.get("total_income", 0), True),
        ("Total Pengeluaran", payload.get("total_expense", 0), True),
        ("Saldo Bersih", payload.get("net", 0), True),
        ("Jumlah Transaksi", len(payload.get("transactions") or []), False),
    ])
    _autosize(ws, [26, 24])

    _write_category_sheet(wb, "Pemasukan Kategori", payload.get("by_category"), "income")
    _write_category_sheet(wb, "Pengeluaran Kategori", payload.get("by_category"), "expense")
    _write_project_sheet(wb, payload.get("by_project"))  # Phase EX-6

    ws4 = wb.create_sheet("Transaksi")
    _write_meta_block(ws4, f"Detail Transaksi -- {month_label}",
                      "50 transaksi terakhir bulan ini")
    _write_header_row(ws4, 4, ["Tanggal", "Tipe", "Kategori", "Deskripsi", "Nominal"])
    r = 5
    for tx in (payload.get("transactions") or []):
        tanggal = (tx.get("created_at") or "")[:10]
        tipe = "Masuk" if tx.get("type") == "income" else "Keluar"
        kategori = tx.get("category_name") or "Tanpa Kategori"
        if tx.get("parent_name"):
            kategori = f"{tx['parent_name']} / {kategori}"
        ws4.cell(row=r, column=1, value=tanggal).font = _INK_FONT
        c2 = ws4.cell(row=r, column=2, value=tipe)
        c2.font = Font(bold=True,
                       color="166534" if tx.get("type") == "income" else "B91C1C")
        ws4.cell(row=r, column=3, value=kategori).font = _INK_FONT
        ws4.cell(row=r, column=4, value=tx.get("description") or "").font = _INK_FONT
        c5 = ws4.cell(row=r, column=5, value=int(tx.get("amount") or 0))
        c5.number_format = _IDR_FMT
        for col in range(1, 6):
            ws4.cell(row=r, column=col).border = _BORDER
        r += 1
    _autosize(ws4, [14, 10, 30, 48, 18])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf.getvalue()
