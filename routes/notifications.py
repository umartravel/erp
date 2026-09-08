"""
Phase 8c-2: Endpoints In-app Notifications (bell icon).

- GET  /api/notifications?unread=1  -> list notif user login (top 50)
- GET  /api/notifications/count     -> {unread: N} (fast poll)
- PUT  /api/notifications/{nid}/read -> mark 1 sebagai read
- PUT  /api/notifications/read-all  -> mark semua unread milik user sebagai read

Scope: setiap user hanya bisa lihat/tandai notif miliknya sendiri.
"""
from fastapi import APIRouter, Query

import db
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
)


router = APIRouter(tags=["notifications"])


@router.get("/api/notifications")
async def notifications_list(
    unread: int = Query(0, description="1 = hanya unread"),
    limit: int = Query(50, ge=1, le=200),
    user=Depends(authenticate_token),
):
    """List notif milik user login, terbaru dulu. Optional filter unread."""
    where = "WHERE user_id = ?"
    params = [user["id"]]
    if unread:
        where += " AND is_read = 0"
    rows = db.query_all(
        f"SELECT id, kind, title, body, link, is_read, created_at, read_at "
        f"FROM user_notifications {where} ORDER BY id DESC LIMIT ?",
        (*params, limit),
    )
    return rows


@router.get("/api/notifications/count")
async def notifications_count(user=Depends(authenticate_token)):
    """Counter unread cepat (dipakai bell badge)."""
    row = db.query_one(
        "SELECT COUNT(*) as c FROM user_notifications "
        "WHERE user_id = ? AND is_read = 0",
        (user["id"],),
    )
    return {"unread": int(row["c"] or 0)}


@router.put("/api/notifications/{nid}/read")
async def notifications_read_one(nid: int, user=Depends(authenticate_token)):
    """Mark 1 notif sebagai read. Scope: user owner-nya sendiri."""
    row = db.query_one(
        "SELECT id, user_id FROM user_notifications WHERE id = ?", (nid,)
    )
    if not row:
        raise HTTPException(status_code=404, detail="Notifikasi tidak ditemukan.")
    if row["user_id"] != user["id"]:
        # Jangan ekspos existence -- kembalikan 404 (bukan 403) supaya penebakan
        # id notif user lain tidak leak informasi.
        raise HTTPException(status_code=404, detail="Notifikasi tidak ditemukan.")
    db.execute(
        "UPDATE user_notifications SET is_read = 1, read_at = CURRENT_TIMESTAMP "
        "WHERE id = ?", (nid,)
    )
    return {"message": "OK"}


@router.put("/api/notifications/read-all")
async def notifications_read_all(user=Depends(authenticate_token)):
    """Mark semua notif unread milik user sebagai read."""
    db.execute(
        "UPDATE user_notifications SET is_read = 1, read_at = CURRENT_TIMESTAMP "
        "WHERE user_id = ? AND is_read = 0", (user["id"],)
    )
    return {"message": "Semua notifikasi ditandai sudah dibaca."}
