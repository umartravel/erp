"""
Sprint AK-3: Table month_end_closings utk audit trail tutup buku bulanan.

Setiap kali admin/management panggil `POST /api/finance/close-month?year=Y&month=M`,
`month_end_closer.close_month()` bikin 1 record di sini yang menjelaskan:
- Kapan closing dijalankan + siapa
- Berapa jamaah yang di-amortisasi (2101 -> 4xxx)
- Berapa procurement yang direalisasi (1108 -> 5xxx)
- Total Rp Revenue yg diakui bulan tsb
- Total Rp COGS yg dibebankan

UNIQUE(year, month): tolak double-close. Kalau perlu re-close (misal ada
correction), user harus DELETE row lalu re-run -- tidak silent overwrite.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS month_end_closings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            year INTEGER NOT NULL,
            month INTEGER NOT NULL CHECK(month BETWEEN 1 AND 12),
            closed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
            closed_by TEXT,
            entries_count INTEGER NOT NULL DEFAULT 0,
            revenue_realized INTEGER NOT NULL DEFAULT 0,
            cogs_recognized INTEGER NOT NULL DEFAULT 0,
            notes TEXT,
            UNIQUE(year, month)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_mec_period "
        "ON month_end_closings (year DESC, month DESC)"
    )
