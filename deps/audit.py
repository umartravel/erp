"""
Helper DB-writing: audit log + setting getter.

Dipisah dari deps/utils.py karena ini SATU-SATUNYA sub-modul di deps/ yang
menyentuh DB langsung (INSERT ke audit_logs, SELECT ke settings).
"""
import db
from realtime import notify


def get_setting(key, default=None):
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row and row["value"] is not None else default


def log_action(user, action, details):
    db.execute(
        "INSERT INTO audit_logs (user_id, user_name, role, action, details) VALUES (?, ?, ?, ?, ?)",
        (user["id"], user["name"], user["role"], action, details),
    )
    notify("data_updated", "audit")


__all__ = ["get_setting", "log_action"]
