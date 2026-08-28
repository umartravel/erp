"""
Router Procurement & Vendor: kontrak vendor B2B (hotel-blok/airline-seat/dll)
dengan alur maker-checker (Pending -> Aktif via review Admin/Mgmt) dan
pembayaran cicilan (deposit) yang otomatis memotong Buku Kas.

Plus endpoint dummy VA (untuk testing frontend payment flow).

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
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

router = APIRouter(tags=["procurement"])


@router.get("/api/procurement")
async def procurement_list(user=Depends(authenticate_token)):
    # Ops ikut bisa lihat (bukan cuma Finance/Management) -- mereka yang di lapangan
    # perlu tahu kontrak vendor apa saja yang aktif untuk koordinasi logistik. Membuat
    # kontrak baru (procurement_create) tetap dibatasi admin/finance karena menyangkut
    # deposit yang (setelah disetujui) memotong Buku Kas.
    require_role(user, "admin", "finance", "management", "ops")
    return db.query_all("SELECT * FROM procurement ORDER BY created_at DESC", ())


@router.post("/api/procurement")
async def procurement_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    # Alur maker-checker (sama pola dengan Refund/Klaim Komisi): kontrak baru masuk
    # sebagai 'Pending' dan BELUM memotong Kas. Kas baru terpotong setelah Admin/Management
    # menyetujui (procurement_review) dan pembayaran dicatat lewat procurement_payment.
    require_role(user, "admin", "finance")
    g = body.get
    vendor_name = (g("vendor_name") or "").strip()
    if not vendor_name:
        raise HTTPException(status_code=400, detail="Nama vendor wajib diisi.")
    total_stock = parse_int(g("total_stock"), "total kuota/blok")
    total_price = parse_int(g("total_price"), "total nilai kontrak")
    last_id, _ = db.execute(
        "INSERT INTO procurement (vendor_name, service_type, total_stock, total_price, "
        "deposit_paid, package_name, status, created_by) VALUES (?, ?, ?, ?, 0, ?, 'Pending', ?)",
        (vendor_name, g("service_type"), total_stock, total_price, g("package_name") or None, user["name"]),
    )
    log_action(user, "CREATE_PROCUREMENT", f"Mengajukan kontrak vendor: {vendor_name} ({g('service_type')})")
    notify("data_updated", "procurement")
    return {"message": "Kontrak vendor berhasil diajukan, menunggu persetujuan.", "id": last_id}


@router.put("/api/procurement/{pid}")
async def procurement_update(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")
    g = body.get
    row = db.query_one("SELECT * FROM procurement WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kontrak vendor tidak ditemukan")
    vendor_name = (g("vendor_name") or "").strip()
    if not vendor_name:
        raise HTTPException(status_code=400, detail="Nama vendor wajib diisi.")
    total_stock = parse_int(g("total_stock"), "total kuota/blok")
    total_price = parse_int(g("total_price"), "total nilai kontrak")
    if total_price < (row["deposit_paid"] or 0):
        raise HTTPException(
            status_code=400,
            detail=f"Total nilai kontrak tidak boleh lebih kecil dari total yang sudah dibayar "
            f"(Rp {row['deposit_paid']:,}).".replace(",", "."),
        )
    db.execute(
        "UPDATE procurement SET vendor_name = ?, service_type = ?, total_stock = ?, "
        "total_price = ?, package_name = ? WHERE id = ?",
        (vendor_name, g("service_type"), total_stock, total_price, g("package_name") or None, pid),
    )
    log_action(user, "UPDATE_PROCUREMENT", f"Mengubah data kontrak vendor: {row['vendor_name']} -> {vendor_name}")
    notify("data_updated", "procurement")
    return {"message": "Data kontrak vendor berhasil diperbarui."}


@router.delete("/api/procurement/{pid}")
async def procurement_delete(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin")
    row = db.query_one("SELECT * FROM procurement WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kontrak vendor tidak ditemukan")
    if (row["deposit_paid"] or 0) > 0:
        raise HTTPException(
            status_code=400,
            detail="Kontrak ini sudah punya riwayat pembayaran yang memotong Kas -- tidak bisa "
            "dihapus. Batalkan kontrak ini sebagai gantinya.",
        )
    db.execute("DELETE FROM procurement WHERE id = ?", (pid,))
    log_action(user, "DELETE_PROCUREMENT", f"Menghapus kontrak vendor: {row['vendor_name']}")
    notify("data_updated", "procurement")
    return {"message": "Kontrak vendor berhasil dihapus."}


@router.put("/api/procurement/{pid}/review")
async def procurement_review(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Aksi tidak valid.")
    row = db.query_one("SELECT * FROM procurement WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kontrak vendor tidak ditemukan")
    if row["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Kontrak ini sudah berstatus '{row['status']}'.")

    if action == "reject":
        reason = (body.get("reason") or "").strip()
        if not reason:
            raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")
        db.execute(
            "UPDATE procurement SET status = 'Ditolak', reviewed_by = ?, "
            "reviewed_at = CURRENT_TIMESTAMP, reject_reason = ? WHERE id = ?",
            (user["name"], reason, pid),
        )
        log_action(user, "REJECT_PROCUREMENT", f"Menolak kontrak vendor {row['vendor_name']}: {reason}")
    else:
        db.execute(
            "UPDATE procurement SET status = 'Aktif', reviewed_by = ?, "
            "reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user["name"], pid),
        )
        log_action(user, "APPROVE_PROCUREMENT", f"Menyetujui kontrak vendor {row['vendor_name']}")
    notify("data_updated", "procurement")
    return {"message": "Kontrak vendor berhasil " + ("ditolak." if action == "reject" else "disetujui.")}


@router.post("/api/procurement/{pid}/payment")
async def procurement_payment(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")
    row = db.query_one("SELECT * FROM procurement WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kontrak vendor tidak ditemukan")
    if row["status"] != "Aktif":
        raise HTTPException(
            status_code=400,
            detail="Pembayaran hanya bisa dicatat untuk kontrak yang sudah Aktif (disetujui).",
        )
    amount = parse_int(body.get("amount"), "nominal pembayaran")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nominal pembayaran harus lebih dari 0.")
    sisa = (row["total_price"] or 0) - (row["deposit_paid"] or 0)
    if amount > sisa:
        raise HTTPException(
            status_code=400, detail=f"Nominal melebihi sisa tagihan (Rp {sisa:,}).".replace(",", "."),
        )
    db.execute("UPDATE procurement SET deposit_paid = deposit_paid + ? WHERE id = ?", (amount, pid))
    db.execute(
        "INSERT INTO transactions (type, category, amount, description, reference_id, package_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("expense", "procurement_payment", amount,
         f"Pembayaran Vendor {row['service_type']}: {row['vendor_name']} (Blok {row['total_stock']} pax)",
         pid, row["package_name"]),
    )
    log_action(
        user, "PAY_PROCUREMENT",
        f"Mencatat pembayaran Rp {amount:,} ke vendor {row['vendor_name']}".replace(",", "."),
    )
    notify("data_updated", "transaction")
    notify("data_updated", "procurement")
    return {"message": "Pembayaran vendor berhasil dicatat."}


@router.put("/api/procurement/{pid}/status")
async def procurement_set_status(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    new_status = body.get("status")
    if new_status not in ("Selesai", "Dibatalkan"):
        raise HTTPException(status_code=400, detail="Status tidak valid.")
    row = db.query_one("SELECT * FROM procurement WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kontrak vendor tidak ditemukan")
    if row["status"] not in ("Aktif", "Pending"):
        raise HTTPException(status_code=400, detail=f"Kontrak ini sudah berstatus '{row['status']}'.")
    db.execute("UPDATE procurement SET status = ? WHERE id = ?", (new_status, pid))
    log_action(
        user, "UPDATE_PROCUREMENT_STATUS",
        f"Mengubah status kontrak vendor {row['vendor_name']} -> {new_status}",
    )
    notify("data_updated", "procurement")
    return {"message": f"Kontrak vendor ditandai sebagai {new_status}."}


@router.get("/api/procurement/{pid}/payments")
async def procurement_payments_history(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management", "ops")
    return db.query_all(
        "SELECT * FROM transactions WHERE reference_id = ? AND category = 'procurement_payment' "
        "ORDER BY created_at ASC", (pid,)
    )


# ===========================================================================
# DUMMY VIRTUAL ACCOUNT (untuk testing frontend payment flow)
# ===========================================================================
@router.post("/api/dummy-payment/va")
async def dummy_va(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    phone = body.get("phone") or "000000000"
    amount = body.get("amount")
    va_number = "988" + phone[-9:]
    return {"message": "Virtual Account BNI (Dummy) berhasil dibuat.", "va_number": va_number, "amount": amount}
