"""
Phase 8a-3: Excel exports bulanan.

4 endpoint (semua admin/mgmt/finance scope):
- GET /api/exports/jamaah-monthly.xlsx?month=YYYY-MM
  Closingan/pendaftaran jamaah di bulan itu (dari jamaah.order_date).
- GET /api/exports/packages-monthly.xlsx?month=YYYY-MM
  Paket berangkat + pulang di bulan itu (dari packages.departure_date/return_date).
- GET /api/exports/finance-monthly.xlsx?month=YYYY-MM
  Income + Expense di bulan itu (dari transactions.created_at).
- GET /api/exports/package-jamaah.xlsx?package_id=X
  Jamaah per paket (dari jamaah.package_type match packages.name).

Content-Disposition attachment supaya browser trigger Save-As.
"""
from datetime import datetime
from io import BytesIO

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

import db
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    require_role,
)
from deps.excel import Sheet, build_workbook


router = APIRouter(tags=["exports"])


_XLSX_MEDIA = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _valid_month(month: str) -> str:
    """Validasi format YYYY-MM. Raise 400 kalau invalid."""
    try:
        datetime.strptime(month, "%Y-%m")
    except (ValueError, TypeError):
        raise HTTPException(status_code=400, detail="Parameter 'month' harus format YYYY-MM (cth: 2025-01).")
    return month


def _xlsx_response(buf: BytesIO, filename: str) -> StreamingResponse:
    """Wrap BytesIO jadi StreamingResponse xlsx download."""
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(buf, media_type=_XLSX_MEDIA, headers=headers)


def _fmt_month_label(month: str) -> str:
    """'2025-01' -> 'Januari 2025' (untuk sheet title)."""
    dt = datetime.strptime(month, "%Y-%m")
    names = ["Januari", "Februari", "Maret", "April", "Mei", "Juni",
             "Juli", "Agustus", "September", "Oktober", "November", "Desember"]
    return f"{names[dt.month - 1]} {dt.year}"


@router.get("/api/exports/jamaah-monthly.xlsx")
async def export_jamaah_monthly(
    month: str = Query(..., description="Bulan format YYYY-MM"),
    user=Depends(authenticate_token),
):
    """Closingan/pendaftaran jamaah per bulan."""
    require_role(user, "admin", "management", "finance")
    _valid_month(month)

    rows = db.query_all(
        "SELECT j.nik, j.name, j.phone, j.package_type, j.total_price, j.paid_amount, "
        "j.status, j.order_date, a.name as agent_name, u.name as sales_name "
        "FROM jamaah j "
        "LEFT JOIN agents a ON j.agent_id = a.id "
        "LEFT JOIN users u ON j.sales_id = u.id "
        "WHERE strftime('%Y-%m', j.order_date) = ? "
        "ORDER BY j.order_date ASC",
        (month,),
    )

    sheet_rows = []
    for r in rows:
        sheet_rows.append([
            r["order_date"] or "",
            r["nik"] or "",
            r["name"] or "",
            r["phone"] or "",
            r["package_type"] or "",
            int(r["total_price"] or 0),
            int(r["paid_amount"] or 0),
            int((r["total_price"] or 0) - (r["paid_amount"] or 0)),
            r["status"] or "",
            r["sales_name"] or "-",
            r["agent_name"] or "-",
        ])

    sheet = Sheet(
        title=f"Closingan {_fmt_month_label(month)}",
        headers=["Tgl Order", "NIK", "Nama", "No. WA", "Paket",
                 "Total Harga", "Sudah Bayar", "Sisa Tagihan",
                 "Status", "Sales", "Agen"],
        rows=sheet_rows,
        currency_columns={5, 6, 7},
    )
    buf = build_workbook([sheet])
    return _xlsx_response(buf, f"closingan-jamaah-{month}.xlsx")


@router.get("/api/exports/packages-monthly.xlsx")
async def export_packages_monthly(
    month: str = Query(..., description="Bulan format YYYY-MM"),
    user=Depends(authenticate_token),
):
    """Paket berangkat + pulang per bulan."""
    require_role(user, "admin", "management", "finance", "ops")
    _valid_month(month)

    # Paket yg BERANGKAT di bulan ini
    berangkat = db.query_all(
        "SELECT p.name, p.departure_date, p.return_date, p.quota, "
        "(SELECT COUNT(*) FROM jamaah j WHERE j.package_type = p.name "
        "AND j.status NOT IN ('Cancelled')) as filled "
        "FROM packages p WHERE strftime('%Y-%m', p.departure_date) = ? "
        "ORDER BY p.departure_date ASC",
        (month,),
    )
    # Paket yg PULANG di bulan ini
    pulang = db.query_all(
        "SELECT p.name, p.departure_date, p.return_date, p.quota, "
        "(SELECT COUNT(*) FROM jamaah j WHERE j.package_type = p.name "
        "AND j.status NOT IN ('Cancelled')) as filled "
        "FROM packages p WHERE strftime('%Y-%m', p.return_date) = ? "
        "ORDER BY p.return_date ASC",
        (month,),
    )

    def _rows(items):
        out = []
        for p in items:
            quota = int(p["quota"] or 0)
            filled = int(p["filled"] or 0)
            out.append([
                p["name"], p["departure_date"] or "", p["return_date"] or "",
                quota, filled, max(0, quota - filled),
            ])
        return out

    sheets = [
        Sheet(
            title=f"Berangkat {_fmt_month_label(month)}",
            headers=["Nama Paket", "Tgl Berangkat", "Tgl Pulang", "Kuota", "Terisi", "Sisa"],
            rows=_rows(berangkat),
        ),
        Sheet(
            title=f"Pulang {_fmt_month_label(month)}",
            headers=["Nama Paket", "Tgl Berangkat", "Tgl Pulang", "Kuota", "Terisi", "Sisa"],
            rows=_rows(pulang),
        ),
    ]
    buf = build_workbook(sheets)
    return _xlsx_response(buf, f"paket-{month}.xlsx")


@router.get("/api/exports/finance-monthly.xlsx")
async def export_finance_monthly(
    month: str = Query(..., description="Bulan format YYYY-MM"),
    user=Depends(authenticate_token),
):
    """Income + Expense per bulan (dari transactions)."""
    require_role(user, "admin", "management", "finance")
    _valid_month(month)

    txs = db.query_all(
        "SELECT type, category, amount, description, package_name, created_at "
        "FROM transactions WHERE strftime('%Y-%m', created_at) = ? "
        "ORDER BY created_at ASC",
        (month,),
    )
    income = [t for t in txs if t["type"] == "income"]
    expense = [t for t in txs if t["type"] == "expense"]

    def _rows(items):
        out = []
        for t in items:
            out.append([
                (t["created_at"] or "").split(" ")[0] if t["created_at"] else "",
                t["category"] or "",
                int(t["amount"] or 0),
                t["description"] or "",
                t["package_name"] or "-",
            ])
        return out

    tot_in = sum(int(t["amount"] or 0) for t in income)
    tot_ex = sum(int(t["amount"] or 0) for t in expense)
    summary_rows = [
        ["Total Pemasukan", tot_in],
        ["Total Pengeluaran", tot_ex],
        ["Selisih (Net)", tot_in - tot_ex],
    ]

    sheets = [
        Sheet(
            title=f"Ringkasan {_fmt_month_label(month)}",
            headers=["Metrik", "Nominal"],
            rows=summary_rows,
            currency_columns={1},
        ),
        Sheet(
            title=f"Pemasukan {_fmt_month_label(month)}",
            headers=["Tanggal", "Kategori", "Nominal", "Deskripsi", "Paket"],
            rows=_rows(income),
            currency_columns={2},
        ),
        Sheet(
            title=f"Pengeluaran {_fmt_month_label(month)}",
            headers=["Tanggal", "Kategori", "Nominal", "Deskripsi", "Paket"],
            rows=_rows(expense),
            currency_columns={2},
        ),
    ]
    buf = build_workbook(sheets)
    return _xlsx_response(buf, f"keuangan-{month}.xlsx")


@router.get("/api/exports/package-jamaah.xlsx")
async def export_package_jamaah(
    package_id: int = Query(..., description="ID paket"),
    user=Depends(authenticate_token),
):
    """Jamaah per paket (individual per paket)."""
    require_role(user, "admin", "management", "finance", "ops")
    pkg = db.query_one("SELECT id, name FROM packages WHERE id = ?", (package_id,))
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan")

    rows = db.query_all(
        "SELECT j.nik, j.name, j.phone, j.room_type, j.room_number, j.status, "
        "j.total_price, j.paid_amount, j.order_date, j.mahram, "
        "j.passport_number, j.passport_expiry, j.bus_group "
        "FROM jamaah j WHERE j.package_type = ? "
        "ORDER BY j.name ASC",
        (pkg["name"],),
    )
    sheet_rows = []
    for r in rows:
        sheet_rows.append([
            r["nik"] or "", r["name"] or "", r["phone"] or "",
            r["room_type"] or "-", r["room_number"] or "-",
            int(r["total_price"] or 0), int(r["paid_amount"] or 0),
            r["status"] or "", r["order_date"] or "",
            r["passport_number"] or "-", r["passport_expiry"] or "-",
            r["mahram"] or "-", r["bus_group"] or "-",
        ])

    sheet = Sheet(
        title=f"{pkg['name'][:25]}",
        headers=["NIK", "Nama", "No. WA", "Tipe Kamar", "No. Kamar",
                 "Total", "Bayar", "Status", "Tgl Order",
                 "No. Paspor", "Expire Paspor", "Mahram", "Bus"],
        rows=sheet_rows,
        currency_columns={5, 6},
    )
    buf = build_workbook([sheet])
    # Filename friendly
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_" for c in pkg["name"])[:40]
    return _xlsx_response(buf, f"jamaah-{safe_name}.xlsx")
