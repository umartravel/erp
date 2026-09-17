"""
Sprint AK-3: Endpoint tutup buku bulanan.

- GET  /api/finance/close-month/preview?year=Y&month=M -- dry-run (fin/admin/mgmt)
- POST /api/finance/close-month body {year,month,notes?} -- jalankan (admin/mgmt)
- GET  /api/finance/close-month/history?limit=12 -- daftar closing terakhir
- DELETE /api/finance/close-month/{id} -- undo (admin only, reverse jurnal)
"""
from fastapi import APIRouter

import db
import month_end_closer
import journal_engine as je
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    json_body,
    log_action,
    notify,
    require_role,
)

router = APIRouter(tags=["close-month"])

_FINANCE_READ = ("admin", "finance", "management")
_FINANCE_WRITE = ("admin", "management")


@router.get("/api/finance/close-month/preview")
async def close_preview(year: int, month: int,
                        user=Depends(authenticate_token)):
    require_role(user, *_FINANCE_READ)
    try:
        return month_end_closer.preview_month(year, month)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/api/finance/close-month")
async def close_run(body: dict = Depends(json_body),
                    user=Depends(authenticate_token)):
    require_role(user, *_FINANCE_WRITE)
    try:
        year = int(body.get("year"))
        month = int(body.get("month"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="year & month wajib integer.")
    notes = (body.get("notes") or "").strip() or None

    try:
        result = month_end_closer.close_month(
            year, month, closed_by=user["name"], notes=notes,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    log_action(user, "CLOSE_MONTH",
               f"Tutup buku {year}-{month:02d}: {result['entries_count']} entri, "
               f"Revenue Rp {result['revenue_realized']:,}, "
               f"COGS Rp {result['cogs_recognized']:,}".replace(",", "."))
    notify("data_updated", "month_closing")
    return {
        "message": f"Buku {year}-{month:02d} berhasil ditutup.",
        **result,
    }


@router.get("/api/finance/close-month/history")
async def close_history(limit: int = 12, user=Depends(authenticate_token)):
    require_role(user, *_FINANCE_READ)
    if limit < 1 or limit > 60:
        limit = 12
    rows = db.query_all(
        "SELECT id, year, month, closed_at, closed_by, entries_count, "
        "  revenue_realized, cogs_recognized, notes "
        "FROM month_end_closings "
        "ORDER BY year DESC, month DESC LIMIT ?", (limit,),
    )
    return {"closings": rows}


@router.delete("/api/finance/close-month/{closing_id}")
async def close_undo(closing_id: int, user=Depends(authenticate_token)):
    require_role(user, "admin")
    row = db.query_one(
        "SELECT year, month, closed_at FROM month_end_closings WHERE id = ?",
        (closing_id,))
    if not row:
        raise HTTPException(status_code=404, detail="Record closing tidak ditemukan.")

    closing_txs = db.query_all(
        "SELECT id FROM transactions "
        "WHERE type = 'closing' "
        "AND category IN ('revenue_realized','cogs_recognized') "
        "AND status = 'POSTED' "
        "AND datetime(created_at) >= datetime(?)",
        (row["closed_at"],),
    )

    reversed_count = 0
    for tx in closing_txs:
        try:
            je.reverse_journal(
                tx["id"],
                f"Undo closing {row['year']}-{row['month']:02d}",
                user_name=user["name"],
            )
            reversed_count += 1
        except Exception as exc:  # noqa: BLE001 -- 1 gagal ≠ block seluruh undo
            log_action(user, "UNDO_CLOSING_TX_FAIL",
                       f"Closing #{closing_id} tx #{tx['id']}: {exc}")
            continue

    db.execute("DELETE FROM month_end_closings WHERE id = ?", (closing_id,))
    log_action(user, "CLOSE_MONTH_UNDO",
               f"Undo closing {row['year']}-{row['month']:02d}: "
               f"{reversed_count} tx di-reverse")
    notify("data_updated", "month_closing")
    return {
        "message": f"Closing {row['year']}-{row['month']:02d} berhasil dibatalkan.",
        "reversed_count": reversed_count,
    }
