"""
Dependencies + helpers bersama untuk file routes/*.py.

Tujuan: hindari circular import ke app.py. Router files hanya boleh import dari
`deps` + module infra (db, realtime, auth), bukan langsung dari app.py.

Re-export authenticate_token dan notify untuk kenyamanan (satu import di router).
"""
from fastapi import Depends, HTTPException, Request

import db
from auth import authenticate_token
from realtime import notify


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


__all__ = [
    "Depends", "HTTPException",
    "authenticate_token", "notify",
    "json_body", "require_role", "log_action",
]
