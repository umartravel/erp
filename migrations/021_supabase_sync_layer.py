"""
Phase SS-1.1 (2026-09-21): Supabase Sync Layer schema.

Konteks: Supabase project `web-umar` (Postgres 17.6) = backend website UMAR
+ intake admin marketing (FARAH, LINA). Tabel `closings` (339 rows) live —
16 rows dalam 7 hari terakhir. UMAR ERP SQLite stagnant 24 hari, 0 rows
baru. Sync one-way Supabase closings → UMAR jamaah supaya ERP layer
(PSAK/ops/finance) reflect reality bisnis.

Schema:
- `jamaah`: 9 kolom baru untuk mirror Supabase closings + tracking sync
- `sync_state`: key/value untuk simpan `last_sync_ts` + counters
- `sync_log`: audit trail per row per sync run (insert/update/soft_delete)

Idempotent: cek PRAGMA table_info sebelum ALTER; CREATE TABLE IF NOT EXISTS.

FK NOTES: `jamaah.external_id` VARCHAR UNIQUE via CREATE UNIQUE INDEX
(SQLite tidak support ADD COLUMN dgn UNIQUE constraint langsung).
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(jamaah)")}

    # Kolom mirror dari Supabase closings (source of truth per field).
    if "external_id" not in cols:
        # ID JAMAAH format Supabase: "151/IX/04/DEC-26" (varchar bebas).
        conn.execute("ALTER TABLE jamaah ADD COLUMN external_id TEXT")
    if "supabase_missing_since" not in cols:
        # Soft-delete detection: kalau Supabase closings row hilang, tag
        # timestamp di sini (BUKAN DELETE jamaah). UMAR append-only vs sync.
        conn.execute("ALTER TABLE jamaah ADD COLUMN supabase_missing_since DATETIME")
    if "admin_marketing" not in cols:
        # Track admin yg input closing (FARAH, LINA, dsb).
        conn.execute("ALTER TABLE jamaah ADD COLUMN admin_marketing TEXT")
    if "channel" not in cols:
        # Marketing channel: OFFLINE/ONLINE (dari Supabase CHANNEL).
        conn.execute("ALTER TABLE jamaah ADD COLUMN channel TEXT")
    if "sub_channel" not in cols:
        # Sub channel: AGEN/INSTAGRAM/WHATSAPP/dsb.
        conn.execute("ALTER TABLE jamaah ADD COLUMN sub_channel TEXT")
    if "dp_amount" not in cols:
        # DP awal saat closing (untuk trigger journal_lines PSAK).
        conn.execute("ALTER TABLE jamaah ADD COLUMN dp_amount INTEGER DEFAULT 0")
    if "package_ext_id" not in cols:
        # ID paket dari Supabase packages (cross-reference kalau ada).
        conn.execute("ALTER TABLE jamaah ADD COLUMN package_ext_id TEXT")
    if "agent_ext_id" not in cols:
        # ID_AGEN dari Supabase (matching ke UMAR agents post-process).
        conn.execute("ALTER TABLE jamaah ADD COLUMN agent_ext_id TEXT")
    if "agent_name_raw" not in cols:
        # NAMA_AGEN raw string dari Supabase (backup kalau agent_ext_id
        # tidak resolve ke UMAR agents.id).
        conn.execute("ALTER TABLE jamaah ADD COLUMN agent_name_raw TEXT")

    # Unique index untuk external_id (SQLite tidak bisa ADD COLUMN UNIQUE
    # langsung; harus CREATE UNIQUE INDEX). Partial index: hanya NOT NULL
    # values yg unique, supaya row lama tanpa external_id tidak conflict.
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_jamaah_external_id "
        "ON jamaah(external_id) WHERE external_id IS NOT NULL"
    )
    # Index untuk lookup cepat soft-deleted rows di page Sync Supabase.
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jamaah_supabase_missing "
        "ON jamaah(supabase_missing_since) WHERE supabase_missing_since IS NOT NULL"
    )

    # sync_state: key/value untuk konfigurasi runtime sync.
    # Contoh keys: last_sync_ts (ISO timestamp), last_backfill_ts,
    # consecutive_fail_count.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sync_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )

    # sync_log: audit trail per row per sync run. old_json + new_json
    # sebagai JSON string (SQLite tidak butuh JSON type). Cukup untuk
    # recover state jamaah tanggal berapapun via query history.
    conn.execute(
        """CREATE TABLE IF NOT EXISTS sync_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_ts DATETIME DEFAULT CURRENT_TIMESTAMP,
            action TEXT NOT NULL,
            external_id TEXT,
            old_json TEXT,
            new_json TEXT,
            error_msg TEXT
        )"""
    )
    # Index untuk query log recent per external_id (page Sync Supabase
    # tampilkan history per jamaah).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_log_ext_id "
        "ON sync_log(external_id, run_ts DESC)"
    )
    # Index untuk query log per action (mis. semua soft_delete recent).
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_sync_log_action "
        "ON sync_log(action, run_ts DESC)"
    )
