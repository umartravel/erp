"""
Phase SS-1.5 (2026-09-21): Admin endpoints untuk Supabase Sync Layer.

Endpoints:
- GET  /api/supabase/sync/status          (admin/mgmt) last sync info + counters
- GET  /api/supabase/sync/log             (admin/mgmt) audit log recent entries
- GET  /api/supabase/sync/soft-deleted    (admin/mgmt) list rows tagged missing
- POST /api/supabase/sync/run             (admin/mgmt) trigger sync incremental
- POST /api/supabase/sync/full-backfill   (admin only) trigger full backfill
- POST /api/supabase/restore/{ext_id}     (admin only) restore soft-deleted row
"""
from fastapi import APIRouter

import db
import supabase_client
import supabase_sync
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    log_action,
    notify,
    require_role,
)

router = APIRouter(tags=["supabase-sync"])


@router.get("/api/supabase/sync/status")
async def sync_status(user=Depends(authenticate_token)):
    """Kembalikan last_sync_ts + counters dari run terakhir + config availability."""
    require_role(user, "admin", "management")
    state_rows = db.query_all("SELECT key, value, updated_at FROM sync_state")
    state = {r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in state_rows}
    soft_del_row = db.query_one(
        "SELECT COUNT(*) as c FROM jamaah WHERE supabase_missing_since IS NOT NULL"
    )
    soft_deleted_now = soft_del_row["c"] if soft_del_row else 0
    return {
        "config_available": supabase_client.check_config_available(),
        "state": state,
        "soft_deleted_current": soft_deleted_now,
    }


@router.get("/api/supabase/sync/log")
async def sync_log_list(limit: int = 50, action: str | None = None,
                        user=Depends(authenticate_token)):
    """Recent audit log entries. Filter opsional per action (insert/update/soft_delete/error/restore)."""
    require_role(user, "admin", "management")
    if limit < 1 or limit > 500:
        limit = 50
    if action:
        rows = db.query_all(
            "SELECT id, run_ts, action, external_id, error_msg "
            "FROM sync_log WHERE action = ? "
            "ORDER BY run_ts DESC LIMIT ?",
            (action, limit),
        )
    else:
        rows = db.query_all(
            "SELECT id, run_ts, action, external_id, error_msg "
            "FROM sync_log ORDER BY run_ts DESC LIMIT ?",
            (limit,),
        )
    return {"entries": rows, "count": len(rows)}


@router.get("/api/supabase/sync/soft-deleted")
async def sync_soft_deleted(user=Depends(authenticate_token)):
    """List semua jamaah yg tagged supabase_missing_since (untuk UI restore panel)."""
    require_role(user, "admin", "management")
    rows = db.query_all(
        "SELECT id, external_id, name, package_type, supabase_missing_since "
        "FROM jamaah WHERE supabase_missing_since IS NOT NULL "
        "ORDER BY supabase_missing_since DESC"
    )
    return {"soft_deleted": rows, "count": len(rows)}


@router.post("/api/supabase/sync/run")
async def sync_run(user=Depends(authenticate_token)):
    """Trigger sync incremental. Query closings WHERE updated_at > last_sync_ts."""
    require_role(user, "admin", "management")
    if not supabase_client.check_config_available():
        raise HTTPException(
            status_code=503,
            detail="Supabase config tidak tersedia. Set env SUPABASE_URL+KEY atau "
                   "buat file .supabase_config.json.",
        )
    try:
        result = await supabase_sync.sync_closings(full=False)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Sync gagal: {e}")

    log_action(user, "SUPABASE_SYNC_RUN",
               f"Incremental: {result['inserted']}i / {result['updated']}u / "
               f"{result['errors']}e dari {result['rows_processed']} rows")
    notify("data_updated", "supabase_sync")
    return result


@router.post("/api/supabase/sync/full-backfill")
async def sync_full_backfill(user=Depends(authenticate_token)):
    """Full backfill: query SEMUA closings + detect soft-deletes. Slow tapi
    thorough. Admin only karena bisa insert banyak rows sekaligus."""
    require_role(user, "admin")
    if not supabase_client.check_config_available():
        raise HTTPException(
            status_code=503,
            detail="Supabase config tidak tersedia. Set env SUPABASE_URL+KEY.",
        )
    try:
        result = await supabase_sync.sync_closings(full=True)
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Full backfill gagal: {e}")

    log_action(user, "SUPABASE_SYNC_FULL_BACKFILL",
               f"Full: {result['inserted']}i / {result['updated']}u / "
               f"{result['soft_deleted']}sd / {result['errors']}e "
               f"dari {result['rows_processed']} rows")
    notify("data_updated", "supabase_sync")
    return result


@router.post("/api/supabase/restore/{external_id:path}")
async def restore_row(external_id: str, user=Depends(authenticate_token)):
    """Recovery: push balik row UMAR yg tagged supabase_missing_since ke Supabase.

    Note: `external_id:path` supaya format Supabase seperti '151/IX/04/DEC-26'
    (yg punya slash) tidak di-parse sebagai path segment.
    """
    require_role(user, "admin")
    if not supabase_client.check_config_available():
        raise HTTPException(status_code=503, detail="Supabase config tidak tersedia.")
    try:
        result = await supabase_sync.restore_to_supabase(external_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=f"Restore gagal: {e}")

    log_action(user, "SUPABASE_RESTORE",
               f"Restore ext_id={external_id} ke Supabase closings")
    notify("data_updated", "supabase_sync")
    return {"message": "Row berhasil di-restore ke Supabase", "result": result}
