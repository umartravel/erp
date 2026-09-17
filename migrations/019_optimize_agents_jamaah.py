"""
Optimasi index database Jamaah + Keagenan.

Audit menemukan tabel `agents` (322 baris di prod, tumbuh terus) TIDAK punya
index sama sekali -- padahal query MY-scope sales (WHERE handler_cs_id = ?)
dan filter geografis (city, province) sering dijalankan.

Jamaah sudah punya 11 index, tapi ada 3 pola query yg belum ter-cover:
pipeline_stage, payment_status, dan composite (package_type, pipeline_stage).

Idempotent lewat `CREATE INDEX IF NOT EXISTS`.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    # agents: handler_cs_id filter MY-scope sales -- panel Agen Saya
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agents_handler_cs "
        "ON agents (handler_cs_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agents_phone "
        "ON agents (phone)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agents_city "
        "ON agents (city)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agents_province "
        "ON agents (province)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_agents_registered "
        "ON agents (registered_at DESC)"
    )

    # jamaah: pipeline_stage + payment_status + composite pkg_pipeline
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jamaah_pipeline "
        "ON jamaah (pipeline_stage)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jamaah_payment "
        "ON jamaah (payment_status)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jamaah_pkg_pipeline "
        "ON jamaah (package_type, pipeline_stage)"
    )
