"""
Jamaah 4-dimensi status system + akses guard.

4 dimensi bersih di tabel `jamaah`:
- pipeline_stage: Lead / Waitlisted / Registered / Booked / Cancelled
- payment_status: Unpaid / DP / Lunas
- visa_status: Belum Proses / Proses Kedutaan / Visa Approved / ...
- trip_status: NotStarted / OnTrip / Completed

Plus kolom cermin `status` (legacy label string) yang di-derive otomatis via
`sync_status_mirror(jid)`. Frontend membaca `status`, bukan dimensi.

Referensi aturan lengkap: ARCHITECTURE.md#jamaah-status-system.
"""
from fastapi import HTTPException

import db


def _derive_status(pipeline, payment, visa, trip):
    """Hasilkan label `status` legacy (kompatibel frontend) dari 4 dimensi bersih."""
    if trip == "OnTrip":
        return "On Trip"
    if pipeline == "Cancelled":
        return "Cancelled"
    if visa == "Visa Approved":
        return "Visa Approved"
    if payment == "Lunas":
        return "Lunas"
    if payment == "DP":
        return "DP Masuk"
    if pipeline == "Registered":
        return "Terdaftar"
    if pipeline == "Waitlisted":
        return "Waitlisted"
    return "Lead - Follow Up"


def sync_status_mirror(jid):
    """Tulis ulang kolom `status` legacy agar konsisten dengan dimensi terkini."""
    j = db.query_one(
        "SELECT pipeline_stage, payment_status, visa_status, trip_status FROM jamaah WHERE id = ?",
        (jid,),
    )
    if not j:
        return None
    label = _derive_status(
        j["pipeline_stage"], j["payment_status"], j["visa_status"], j["trip_status"]
    )
    db.execute("UPDATE jamaah SET status = ? WHERE id = ?", (label, jid))
    return label


def status_to_dims(status, paid, total):
    """Petakan label legacy + nominal bayar -> (pipeline, payment, trip)."""
    paid = paid or 0
    total = total or 0
    if paid > 0 and total > 0 and paid >= total:
        payment = "Lunas"
    elif paid > 0:
        payment = "DP"
    else:
        payment = "Unpaid"
    trip = "OnTrip" if status == "On Trip" else "NotStarted"
    if status == "Cancelled":
        pipeline = "Cancelled"
    elif status == "Lead - Follow Up":
        pipeline = "Lead"
    elif status == "Waitlisted":
        pipeline = "Waitlisted"
    elif status == "Terdaftar":
        pipeline = "Registered"
    else:
        pipeline = "Booked"
    return pipeline, payment, trip


def _field_change(label, old_val, new_val):
    """Deskripsi perubahan satu field untuk audit trail."""
    old_val, new_val = old_val or "-", new_val or "-"
    return f"{label} ({old_val} -> {new_val})" if old_val != new_val else None


def assert_jamaah_access(jid, user):
    """Sales hanya boleh mengakses jamaah miliknya sendiri."""
    if user["role"] == "sales":
        row = db.query_one("SELECT sales_id FROM jamaah WHERE id = ?", (jid,))
        if not row:
            raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")
        if row["sales_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Akses Ditolak: bukan jamaah Anda.")


__all__ = [
    "_derive_status", "sync_status_mirror", "status_to_dims",
    "_field_change", "assert_jamaah_access",
]
