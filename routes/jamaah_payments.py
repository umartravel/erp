"""
Router Pembayaran Jamaah -- Maker-Checker (Phase 13a).

Alur baru (menggantikan flow lama yg sales lapor manual ke finance):

  Sales (submit)  ->  status=Pending  ->  Finance (verify/reject)
                                              |
                                              +-> Verify (ACC):
                                              |     - jamaah.paid_amount += amount
                                              |     - insert transactions (income)
                                              |     - kalau baru Lunas & ada agent:
                                              |       auto-create commission_claim (Pending)
                                              |     - notif ke sales
                                              +-> Reject:
                                                    - simpan reject_reason
                                                    - notif ke sales

Endpoint:
- POST /api/jamaah/{jid}/payment-submissions        sales/admin submit
- GET  /api/payment-submissions?status=&scope=      finance list queue
- GET  /api/jamaah/{jid}/payment-submissions        history per jamaah
- PUT  /api/payment-submissions/{sid}/review        finance ACC/reject

RBAC:
- Submit: sales/admin (bukan finance -- finance verifikasi, bukan input)
- List queue: admin/finance/management
- History per jamaah: semua role kerja yg boleh lihat jamaah
- Review: admin/finance

Existing `PUT /api/jamaah/{jid}/payment` (admin/finance) tetap ada utk
koreksi manual/backfill oleh admin. Flow baru direkomendasikan utk operasi
normal supaya audit trail rapi.
"""
from fastapi import APIRouter

import db
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    json_body,
    log_action,
    notify,
    parse_int,
    require_role,
)
from deps.notifications import notify_role, notify_user

router = APIRouter(tags=["jamaah-payments"])


_KIND_ALLOWED = ("DP", "Cicilan", "Pelunasan")
_METHOD_ALLOWED = ("Transfer", "Cash", "VA", "Lainnya")


@router.post("/api/jamaah/{jid}/payment-submissions")
async def submission_create(jid: int, body: dict = Depends(json_body),
                            user=Depends(authenticate_token)):
    """Sales/admin submit pembayaran jamaah utk di-ACC finance.
    Status awal Pending -- belum apply ke paid_amount jamaah."""
    require_role(user, "admin", "sales")

    jamaah = db.query_one(
        "SELECT id, name, total_price, paid_amount, sales_id FROM jamaah WHERE id = ?",
        (jid,),
    )
    if not jamaah:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan.")

    amount = parse_int(body.get("amount"), "nominal pembayaran")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nominal harus > 0.")

    total = jamaah["total_price"] or 0
    paid = jamaah["paid_amount"] or 0
    pending_sum = (db.query_one(
        "SELECT COALESCE(SUM(amount), 0) s FROM jamaah_payment_submissions "
        "WHERE jamaah_id = ? AND status = 'Pending'", (jid,),
    ) or {}).get("s") or 0
    remaining = total - paid - pending_sum
    if total > 0 and amount > remaining:
        raise HTTPException(
            status_code=400,
            detail=f"Nominal melebihi sisa tagihan (setelah kurangi submission Pending): "
            f"Rp {remaining:,}.".replace(",", "."),
        )

    payment_kind = body.get("payment_kind") or "Cicilan"
    if payment_kind not in _KIND_ALLOWED:
        raise HTTPException(status_code=400,
                            detail=f"payment_kind harus salah satu: {', '.join(_KIND_ALLOWED)}")
    payment_method = body.get("payment_method")
    if payment_method and payment_method not in _METHOD_ALLOWED:
        raise HTTPException(status_code=400,
                            detail=f"payment_method harus salah satu: {', '.join(_METHOD_ALLOWED)}")

    last_id, _ = db.execute(
        "INSERT INTO jamaah_payment_submissions "
        "(jamaah_id, amount, payment_kind, payment_method, bank_account, notes, "
        "submitted_by, submitted_by_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (jid, amount, payment_kind, payment_method,
         body.get("bank_account"), body.get("notes"),
         user["name"], user["id"]),
    )
    log_action(user, "PAYMENT_SUBMIT",
               f"Ajukan {payment_kind} Rp {amount:,} utk jamaah {jamaah['name']}".replace(",", "."))
    notify("data_updated", "payment_submission")
    # Notif ke role finance -- ada verifikasi menunggu.
    notify_role(
        "finance", "payment_pending_verify",
        f"Pembayaran menunggu ACC: {jamaah['name']}",
        f"{payment_kind} Rp {amount:,} oleh {user['name']}. "
        f"Verifikasi uang masuk rekening lalu ACC.".replace(",", "."),
        "#page-finance-home",
    )
    return {"message": "Pembayaran diajukan, menunggu ACC finance.", "id": last_id}


@router.get("/api/payment-submissions")
async def submissions_list(status: str | None = None, scope: str = "all",
                           limit: int = 50, user=Depends(authenticate_token)):
    """List submission. Finance/admin/mgmt bisa 'all'; sales dapat 'my' (yg dia ajukan)."""
    role = user.get("role")
    if role not in ("admin", "finance", "management", "sales"):
        raise HTTPException(status_code=403, detail="Tidak berwenang.")

    where = []
    params: list = []
    if status:
        where.append("s.status = ?"); params.append(status)
    if role == "sales" or scope == "my":
        where.append("s.submitted_by_id = ?"); params.append(user["id"])
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    rows = db.query_all(
        f"SELECT s.*, j.name AS jamaah_name, j.package_type, j.total_price, j.paid_amount "
        f"FROM jamaah_payment_submissions s "
        f"LEFT JOIN jamaah j ON j.id = s.jamaah_id "
        f"{where_sql} "
        f"ORDER BY CASE s.status WHEN 'Pending' THEN 0 ELSE 1 END, "
        f"         s.submitted_at DESC LIMIT ?",
        tuple(params + [max(1, min(limit, 200))]),
    ) or []
    return rows


@router.get("/api/jamaah/{jid}/payment-submissions")
async def submissions_for_jamaah(jid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "sales", "finance", "management")
    return db.query_all(
        "SELECT * FROM jamaah_payment_submissions WHERE jamaah_id = ? "
        "ORDER BY submitted_at DESC", (jid,),
    ) or []


@router.put("/api/payment-submissions/{sid}/review")
async def submission_review(sid: int, body: dict = Depends(json_body),
                            user=Depends(authenticate_token)):
    """Finance ACC (verify) atau reject submission.

    Verify -> jamaah.paid_amount += amount, insert transactions,
    trigger commission_claim kalau baru Lunas + ada agent.
    Reject -> simpan reject_reason, tidak sentuh paid_amount.
    """
    require_role(user, "admin", "finance")
    action = body.get("action")
    if action not in ("verify", "reject"):
        raise HTTPException(status_code=400,
                            detail="action harus 'verify' atau 'reject'.")

    sub = db.query_one("SELECT * FROM jamaah_payment_submissions WHERE id = ?", (sid,))
    if not sub:
        raise HTTPException(status_code=404, detail="Submission tidak ditemukan.")
    if sub["status"] != "Pending":
        raise HTTPException(status_code=400,
                            detail=f"Submission sudah berstatus '{sub['status']}'.")

    jamaah = db.query_one(
        "SELECT j.*, a.name AS agent_name, p.default_commission_fee "
        "FROM jamaah j LEFT JOIN agents a ON j.agent_id = a.id "
        "LEFT JOIN packages p ON j.package_type = p.name WHERE j.id = ?",
        (sub["jamaah_id"],),
    )
    if not jamaah:
        raise HTTPException(status_code=404, detail="Jamaah target tidak ditemukan.")

    if action == "reject":
        reason = (body.get("reason") or "").strip()
        if not reason:
            raise HTTPException(status_code=400,
                                detail="Alasan tolak wajib diisi.")
        db.execute(
            "UPDATE jamaah_payment_submissions SET status = 'Rejected', "
            "reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP, reject_reason = ? "
            "WHERE id = ?",
            (user["name"], reason, sid),
        )
        log_action(user, "PAYMENT_REJECT",
                   f"Tolak pembayaran jamaah {jamaah['name']}: {reason}")
        notify("data_updated", "payment_submission")
        notify_user(
            sub["submitted_by_id"], "payment_rejected",
            f"Pembayaran ditolak: {jamaah['name']}",
            f"Nominal Rp {sub['amount']:,}. Alasan: {reason}".replace(",", "."),
            "#page-jamaah",
        )
        return {"message": "Pembayaran ditolak."}

    # === action == 'verify' ===
    amount = sub["amount"] or 0
    new_paid = (jamaah["paid_amount"] or 0) + amount
    total = jamaah["total_price"] or 0
    if total > 0 and new_paid >= total:
        new_payment = "Lunas"
    elif new_paid > 0:
        new_payment = "DP"
    else:
        new_payment = "Unpaid"
    new_pipeline = jamaah["pipeline_stage"]
    if new_paid > 0 and new_pipeline != "Cancelled":
        new_pipeline = "Booked"
    old_payment = jamaah["payment_status"]

    db.execute(
        "UPDATE jamaah SET paid_amount = ?, payment_status = ?, pipeline_stage = ? "
        "WHERE id = ?",
        (new_paid, new_payment, new_pipeline, jamaah["id"]),
    )
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, "
        "reference_id, package_name) VALUES (?, ?, ?, ?, ?, ?)",
        ("income", "payment", amount,
         f"Pembayaran Umroh: {jamaah['name']} ({sub['payment_kind']})",
         jamaah["id"], jamaah["package_type"]),
    )
    db.execute(
        "UPDATE jamaah_payment_submissions SET status = 'Verified', "
        "reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP, transaction_id = ? "
        "WHERE id = ?",
        (user["name"], tx_id, sid),
    )
    log_action(user, "PAYMENT_VERIFY",
               f"ACC pembayaran jamaah {jamaah['name']} Rp {amount:,}".replace(",", "."))
    notify("data_updated", "transaction")
    notify("data_updated", "payment_submission")

    # Baru jadi Lunas + ada agen -> auto-create commission_claim (Pending).
    if new_payment == "Lunas" and old_payment != "Lunas" and jamaah["agent_id"]:
        package_fee = db.query_one(
            "SELECT commission_fee FROM agent_package_fees "
            "WHERE agent_id = ? AND package_name = ?",
            (jamaah["agent_id"], jamaah["package_type"]),
        )
        fee = (package_fee["commission_fee"] if package_fee
               else (jamaah["default_commission_fee"] or 0))
        db.execute(
            "INSERT INTO commission_claims (agent_id, jamaah_id, amount, requested_by) "
            "VALUES (?, ?, ?, ?)",
            (jamaah["agent_id"], jamaah["id"], fee,
             "Sistem (Otomatis saat Lunas via verify)"),
        )
        notify("data_updated", "commission_claim")
        notify_role(
            "management", "commission_pending",
            f"Klaim komisi Rp {fee:,} pending review".replace(",", "."),
            f"Agen untuk jamaah {jamaah['name']} (paket {jamaah['package_type']})",
            "#page-agents",
        )

    # Notif ke sales pengaju bahwa pembayaran mereka di-ACC.
    notify_user(
        sub["submitted_by_id"], "payment_verified",
        f"Pembayaran di-ACC: {jamaah['name']}",
        f"{sub['payment_kind']} Rp {amount:,} sudah masuk kas. "
        f"Status jamaah -> {new_payment}.".replace(",", "."),
        "#page-jamaah",
    )
    return {"message": "Pembayaran di-ACC dan sudah masuk kas.",
            "transaction_id": tx_id, "new_payment_status": new_payment}
