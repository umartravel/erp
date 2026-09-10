"""
Phase DT-1a: Daily Task Reports module.

Fitur baru untuk visibility management terhadap kerja tiap role. Karyawan
(semua role) isi laporan harian: ringkasan free-text + todo list task item
(optional link ke jamaah/paket/incident). Management lihat di halaman
dedicated + digest PDF/Excel + feedback loop 2-arah.

Skema (3 tabel):

1. daily_reports -- parent per (user, tanggal), UNIQUE index utk enforce
   1 report per hari per user. Status Draft -> Submitted.

2. daily_task_items -- child todo item per report. ON DELETE CASCADE
   supaya hapus report otomatis bersihin items. linked_entity_{type,id}
   opsional untuk drill-down dari mgmt (klik jamaah -> detail modal).

3. daily_report_feedback -- comment thread mgmt<->karyawan. Style Whatsapp
   (kiri/kanan by is_from_management flag). CASCADE dgn report.

Index utama:
- daily_reports (user_id, report_date DESC) -- history karyawan cepat
- daily_reports (report_date DESC, status)  -- mgmt aggregate view
- daily_task_items (report_id, sort_order)  -- render list dgn urutan
- daily_report_feedback (report_id, created_at) -- thread order
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            report_date DATE NOT NULL,
            summary_text TEXT,
            mood TEXT,
            status TEXT NOT NULL DEFAULT 'Draft',
            submitted_at DATETIME,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (user_id, report_date)
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dr_user_date "
        "ON daily_reports (user_id, report_date DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dr_date_status "
        "ON daily_reports (report_date DESC, status)"
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS daily_task_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES daily_reports(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            description TEXT,
            status TEXT NOT NULL DEFAULT 'Pending',
            priority TEXT NOT NULL DEFAULT 'medium',
            linked_entity_type TEXT,
            linked_entity_id INTEGER,
            completed_at DATETIME,
            sort_order INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_dti_report "
        "ON daily_task_items (report_id, sort_order)"
    )

    conn.execute(
        """CREATE TABLE IF NOT EXISTS daily_report_feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id INTEGER NOT NULL REFERENCES daily_reports(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL,
            comment_text TEXT NOT NULL,
            is_from_management INTEGER NOT NULL DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_drf_report "
        "ON daily_report_feedback (report_id, created_at)"
    )
