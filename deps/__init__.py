"""
Dependencies + helpers bersama untuk file routes/*.py.

Package ini adalah shim re-export: semua router boleh tetap pakai
`from deps import X` (backwards-compat) walau isinya kini tersebar ke 5
sub-modul fokus:

- deps.paths         : BASE_DIR, PUBLIC_DIR, UPLOAD_DIR, BRANDING_DIR
- deps.http          : json_body, require_role
- deps.utils         : parse_int, fmt_id, fire_and_forget
- deps.audit         : get_setting, log_action  (satu-satunya sub-modul yg
                       menyentuh DB langsung)
- deps.jamaah_status : _derive_status, sync_status_mirror, status_to_dims,
                       _field_change, assert_jamaah_access

Tujuan: hindari circular import ke app.py. Router files hanya boleh import
dari `deps` + module infra (db, realtime, auth), bukan langsung dari app.py.

Re-export authenticate_token, notify, Depends, HTTPException untuk kenyamanan
(satu import di router).
"""
from fastapi import Depends, HTTPException

from auth import authenticate_token
from realtime import notify

from .audit import get_setting, log_action
from .http import json_body, require_role
from .jamaah_status import (
    _derive_status,
    _field_change,
    assert_jamaah_access,
    status_to_dims,
    sync_status_mirror,
)
from .paths import BASE_DIR, BRANDING_DIR, PUBLIC_DIR, UPLOAD_DIR
from .utils import fire_and_forget, fmt_id, parse_int

__all__ = [
    # FastAPI + external re-exports
    "Depends", "HTTPException",
    "authenticate_token", "notify",
    # HTTP helpers
    "json_body", "require_role",
    # Utility
    "parse_int", "fmt_id", "fire_and_forget",
    # Audit + settings
    "get_setting", "log_action",
    # Jamaah status system
    "_derive_status", "sync_status_mirror", "status_to_dims",
    "_field_change", "assert_jamaah_access",
    # Paths
    "BASE_DIR", "PUBLIC_DIR", "UPLOAD_DIR", "BRANDING_DIR",
]
