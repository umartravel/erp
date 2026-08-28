"""
Router Jamaah Actions: sub-resource jamaah + refund workflow.

Cakupan:
- delete/cancel jamaah (permission jamaah scope)
- comments internal + activities (lead follow-up)
- payments history read-only
- refund-requests suite (ajukan CS -> review Mgmt -> disburse Finance)

Router lain untuk jamaah (per split iterasi 7):
- routes/jamaah_read.py  : GET + POST /api/jamaah (list + create)
- routes/jamaah_write.py : bulk-ops + PUT/{jid} big + doc + documents + payment
                           + check-visa + ops verbs (paling sensitif; auto-create
                           commission_claim di payment endpoint)
"""
from fastapi import APIRouter

import db
from deps import (
    Depends,
    HTTPException,
    assert_jamaah_access,
    authenticate_token,
    json_body,
    log_action,
    notify,
    parse_int,
    require_role,
    sync_status_mirror,
)

router = APIRouter(tags=["jamaah-actions"])


# ===========================================================================
# HAPUS / BATALKAN JAMAAH
# ===========================================================================
@router.delete("/api/jamaah/{jid}")
async def jamaah_delete(jid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    row = db.query_one("SELECT status FROM jamaah WHERE id = ?", (jid,))
    if not row:
        raise HTTPException(status_code=404, detail="Data tidak ditemukan")
    if row["status"] != "Lead - Follow Up" and user["role"] != "admin":
        raise HTTPException(
            status_code=400,
            detail="Hanya status Lead yang boleh dihapus permanen. Gunakan fitur Batalkan (Cancel) untuk data Booking.",
        )
    db.execute("DELETE FROM jamaah WHERE id = ?", (jid,))
    log_action(user, "DELETE_JAMAAH", f"Menghapus data Lead ID: {jid}")
    notify("data_updated", "jamaah")
    return {"message": "Data Leads dihapus permanen."}


@router.put("/api/jamaah/{jid}/cancel")
async def jamaah_cancel(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    reason = body.get("reason")
    if not reason:
        raise HTTPException(
            status_code=400, detail="Alasan pembatalan wajib diisi untuk keperluan Audit."
        )
    db.execute(
        "UPDATE jamaah SET pipeline_stage = 'Cancelled', cancel_reason = ? WHERE id = ?", (reason, jid)
    )
    sync_status_mirror(jid)
    log_action(user, "CANCEL_JAMAAH", f"Membatalkan booking Jamaah ID: {jid}. Alasan: {reason}")
    notify("data_updated", "jamaah")
    return {"message": "Booking Jamaah berhasil dibatalkan."}


# ===========================================================================
# CATATAN INTERNAL ANTAR DIVISI
# ===========================================================================
@router.get("/api/jamaah/{jid}/comments")
async def jamaah_comments(jid: int, user=Depends(authenticate_token)):
    return db.query_all(
        "SELECT * FROM jamaah_comments WHERE jamaah_id = ? ORDER BY created_at ASC", (jid,)
    )


@router.post("/api/jamaah/{jid}/comments")
async def jamaah_comment_add(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    db.execute(
        "INSERT INTO jamaah_comments (jamaah_id, user_name, comment) VALUES (?, ?, ?)",
        (jid, user["name"], body.get("comment")),
    )
    return {"message": "Catatan internal berhasil ditambahkan."}


# ===========================================================================
# CRM SALES: AKTIVITAS LEAD + PAYMENT HISTORY (read-only)
# ===========================================================================
@router.get("/api/jamaah/{jid}/activities")
async def lead_activities_list(jid: int, user=Depends(authenticate_token)):
    assert_jamaah_access(jid, user)
    return db.query_all(
        "SELECT * FROM lead_activities WHERE jamaah_id = ? ORDER BY created_at DESC", (jid,)
    )


@router.get("/api/jamaah/{jid}/payments")
async def jamaah_payments_history(jid: int, user=Depends(authenticate_token)):
    assert_jamaah_access(jid, user)
    return db.query_all(
        "SELECT amount, created_at FROM transactions "
        "WHERE reference_id = ? AND category = 'payment' AND type = 'income' "
        "ORDER BY created_at ASC",
        (jid,),
    )


@router.post("/api/jamaah/{jid}/activities")
async def lead_activity_add(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    assert_jamaah_access(jid, user)
    activity_type = body.get("activity_type") or "Catatan"
    note = body.get("note")
    next_follow_up = body.get("next_follow_up") or None
    db.execute(
        "INSERT INTO lead_activities (jamaah_id, user_name, activity_type, note, next_follow_up) "
        "VALUES (?, ?, ?, ?, ?)",
        (jid, user["name"], activity_type, note, next_follow_up),
    )
    # Denormalisasi ke jamaah agar query follow-up & 'kontak terakhir' cepat.
    db.execute(
        "UPDATE jamaah SET next_follow_up = ?, last_contact = CURRENT_TIMESTAMP WHERE id = ?",
        (next_follow_up, jid),
    )
    log_action(user, "LEAD_ACTIVITY", f"{activity_type} ke Jamaah ID {jid}")
    notify("data_updated", "jamaah")
    return {"message": "Aktivitas follow-up berhasil dicatat."}


# ===========================================================================
# REFUND: CS ajukan -> Manajemen setuju/tolak -> Finance cairkan dana.
# Sengaja terpisah dari Batalkan Booking (pipeline_stage tidak diubah di sini)
# agar dua aksi tsb tidak saling tercampur.
# ===========================================================================
@router.get("/api/jamaah/{jid}/refund-requests")
async def jamaah_refund_requests_list(jid: int, user=Depends(authenticate_token)):
    assert_jamaah_access(jid, user)
    return db.query_all(
        "SELECT * FROM refund_requests WHERE jamaah_id = ? ORDER BY requested_at ASC", (jid,)
    )


@router.post("/api/jamaah/{jid}/refund-requests")
async def jamaah_refund_request_create(
    jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    require_role(user, "admin", "sales")
    assert_jamaah_access(jid, user)

    amount = parse_int(body.get("amount"), "nominal refund")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Alasan refund wajib diisi.")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nominal refund harus lebih dari 0.")

    jamaah = db.query_one("SELECT name, paid_amount FROM jamaah WHERE id = ?", (jid,))
    if not jamaah:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")

    already_requested = db.query_one(
        "SELECT COALESCE(SUM(amount), 0) as total FROM refund_requests "
        "WHERE jamaah_id = ? AND status IN ('Pending', 'Disetujui')",
        (jid,),
    )["total"]
    if amount > (jamaah["paid_amount"] or 0) - already_requested:
        raise HTTPException(
            status_code=400,
            detail="Nominal refund melebihi sisa dana yang bisa direfund (sudah dikurangi pengajuan lain yang masih berjalan).",
        )

    cancel_booking = 1 if body.get("cancel_booking") else 0
    db.execute(
        "INSERT INTO refund_requests (jamaah_id, amount, reason, requested_by, cancel_booking) VALUES (?, ?, ?, ?, ?)",
        (jid, amount, reason, user["name"], cancel_booking),
    )
    log_action(user, "REQUEST_REFUND", f"Mengajukan refund Rp {amount} untuk {jamaah['name']}: {reason}")
    notify("data_updated", "refund_request")
    return {"message": "Pengajuan refund berhasil dikirim, menunggu persetujuan manajemen."}


@router.get("/api/refund-requests")
async def refund_requests_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "management", "finance")
    return db.query_all(
        "SELECT r.*, j.name as jamaah_name, j.nik as jamaah_nik FROM refund_requests r "
        "LEFT JOIN jamaah j ON r.jamaah_id = j.id ORDER BY r.requested_at DESC"
    )


@router.put("/api/refund-requests/{rid}/review")
async def refund_request_review(
    rid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    require_role(user, "admin", "management")
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Aksi harus 'approve' atau 'reject'.")

    r = db.query_one("SELECT * FROM refund_requests WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Pengajuan refund tidak ditemukan")
    if r["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Pengajuan ini berstatus '{r['status']}', tidak bisa direview ulang.")

    note = (body.get("note") or "").strip()
    if action == "reject" and not note:
        raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")

    new_status = "Disetujui" if action == "approve" else "Ditolak"
    db.execute(
        "UPDATE refund_requests SET status = ?, approved_by = ?, approved_at = CURRENT_TIMESTAMP, "
        "reject_reason = ? WHERE id = ?",
        (new_status, user["name"], note if action == "reject" else None, rid),
    )
    log_action(
        user, "APPROVE_REFUND" if action == "approve" else "REJECT_REFUND",
        f"{new_status} pengajuan refund #{rid} (Rp {r['amount']})",
    )
    notify("data_updated", "refund_request")
    return {"message": f"Pengajuan refund berhasil di-{'setujui' if action == 'approve' else 'tolak'}."}


@router.put("/api/refund-requests/{rid}/disburse")
async def refund_request_disburse(rid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")

    r = db.query_one("SELECT * FROM refund_requests WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Pengajuan refund tidak ditemukan")
    if r["status"] != "Disetujui":
        raise HTTPException(
            status_code=400,
            detail="Hanya pengajuan berstatus 'Disetujui' (oleh manajemen) yang bisa dicairkan.",
        )

    jamaah = db.query_one("SELECT * FROM jamaah WHERE id = ?", (r["jamaah_id"],))
    if not jamaah:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")

    new_paid = (jamaah["paid_amount"] or 0) - r["amount"]
    total = jamaah["total_price"] or 0
    if total > 0 and new_paid >= total:
        new_payment = "Lunas"
    elif new_paid > 0:
        new_payment = "DP"
    else:
        new_payment = "Unpaid"

    last_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, reference_id, package_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("expense", "refund", r["amount"], f"Refund: {jamaah['name']}", jamaah["id"], jamaah["package_type"]),
    )
    db.execute(
        "UPDATE jamaah SET paid_amount = ?, payment_status = ? WHERE id = ?",
        (new_paid, new_payment, jamaah["id"]),
    )
    # Pembatalan booking (bila dicentang saat pengajuan) baru dijalankan di titik ini --
    # saat uang benar-benar keluar dari Buku Kas, bukan sekadar disetujui manajemen --
    # supaya status booking selalu mencerminkan kondisi uang yang sebenarnya.
    if r["cancel_booking"] and jamaah["pipeline_stage"] != "Cancelled":
        db.execute(
            "UPDATE jamaah SET pipeline_stage = 'Cancelled', cancel_reason = ? WHERE id = ?",
            (r["reason"], jamaah["id"]),
        )
    sync_status_mirror(jamaah["id"])
    db.execute(
        "UPDATE refund_requests SET status = 'Dicairkan', disbursed_by = ?, disbursed_at = CURRENT_TIMESTAMP, "
        "transaction_id = ? WHERE id = ?",
        (user["name"], last_id, rid),
    )
    log_action(
        user, "DISBURSE_REFUND",
        f"Mencairkan refund Rp {r['amount']} untuk {jamaah['name']}"
        + (" (sekaligus membatalkan booking)" if r["cancel_booking"] else ""),
    )
    notify("data_updated", "refund_request")
    notify("data_updated", "transaction")
    notify("data_updated", "jamaah")
    return {"message": "Refund berhasil dicairkan dan tercatat di Buku Kas."}


# NOTE: PUT /api/jamaah/bulk-ops SENGAJA tetap di app.py karena harus didaftarkan
# SEBELUM PUT /api/jamaah/{jid} literal endpoint (FastAPI matching order). Router
# ini di-include belakangan sehingga jika bulk-ops ada di sini, request akan
# di-shadow oleh {jid} yang gagal cast "bulk-ops" -> int (422).
