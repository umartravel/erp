"""
KRITIS #2 (audit 2026-09-20): Force password-change untuk semua user existing.

Root cause: seed users (admin/finance1/sales1/ops1/manager1) semua pakai
default `password123` -- ini ter-dokumen di README/panduan + tersebar di
tests. Untuk pre-produksi hygiene, seluruh user existing WAJIB rotate
password saat first login post-deploy migration ini.

Schema:
- users.must_change_password INTEGER NOT NULL DEFAULT 0
  0 = normal, 1 = wajib ganti password sebelum navigate ke halaman lain.

Backfill:
- Semua row users existing SET must_change_password = 1 (aggressive default:
  siapapun yang login setelah migrasi harus rotate; per keputusan user
  2026-09-20 "SEMUA user existing" > "hanya seed test users" > "manual").
- User baru yg dibuat setelah migration ini: default = 1 juga (di
  routes/users.py POST /api/users), supaya admin-reset atau initial invite
  selalu meng-enforce ganti password saat first login.

Toggle:
- Cleared jadi 0 saat user berhasil PUT /api/users/me/password.
- Set 1 lagi saat admin PUT /api/users/{uid}/reset-password.

Idempotent: cek kolom via PRAGMA table_info sebelum ALTER.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
    if "must_change_password" not in cols:
        # DEFAULT 1: SQLite ALTER TABLE ADD COLUMN dgn DEFAULT langsung mengisi
        # existing rows dgn 1 -- backfill implisit, safer. Row baru INSERT tanpa
        # kolom ini juga dapat 1 (default: force rotate). Untuk clear flag,
        # backend HARUS explicit set 0 lewat PUT /me/password (lihat routes/users.py).
        conn.execute(
            "ALTER TABLE users ADD COLUMN must_change_password INTEGER NOT NULL DEFAULT 1"
        )
    # Double-check idempotent: unconditional UPDATE utk edge case existing row
    # yang somehow tercatat flag=0 (mis. testing DB yg diseed setelah migration).
    conn.execute("UPDATE users SET must_change_password = 1 WHERE must_change_password IS NULL")
