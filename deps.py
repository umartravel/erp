"""
Dependencies + helpers bersama untuk file routes/*.py.

Tujuan: hindari circular import ke app.py. Router files hanya boleh import dari
`deps` + module infra (db, realtime, auth), bukan langsung dari app.py.

Re-export authenticate_token dan notify untuk kenyamanan (satu import di router).
"""
import os

from fastapi import Depends, HTTPException, Request

import db
from auth import authenticate_token
from realtime import notify

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")


def get_setting(key, default=None):
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row and row["value"] is not None else default


async def json_body(request: Request) -> dict:
    """Body parser longgar (meniru req.body Express) -- dipakai sebagai Depends()."""
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def require_role(user, *roles):
    if user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Akses Ditolak")


def log_action(user, action, details):
    db.execute(
        "INSERT INTO audit_logs (user_id, user_name, role, action, details) VALUES (?, ?, ?, ?, ?)",
        (user["id"], user["name"], user["role"], action, details),
    )
    notify("data_updated", "audit")


# ---------------------------------------------------------------------------
# Helper generik + jamaah-status (dipakai app.py dan routes/jamaah.py).
# Ditaruh di sini supaya kedua file punya satu sumber kebenaran, bukan
# duplikat definisi.
# ---------------------------------------------------------------------------
def parse_int(value, field="nilai"):
    """Parse angka dari input. Balikan HTTP 400 yang rapi bila tidak valid."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Input {field} harus berupa angka yang valid.")


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
    "Depends", "HTTPException",
    "authenticate_token", "notify",
    "json_body", "require_role", "log_action",
    "parse_int", "_derive_status", "sync_status_mirror", "status_to_dims",
    "_field_change", "assert_jamaah_access",
    "get_setting", "PUBLIC_DIR", "BASE_DIR",
]
