"""
Phase 8c-1: In-app notification bell.

Menambah tabel `user_notifications` untuk delivery notif ter-scope per user
(bukan broadcast socket saja yang existing). Bell icon di sidebar tampilkan
count unread + list dropdown -- klik item navigate ke source lewat `link`.

Schema:
- user_id: target penerima (FK ke users.id, tanpa CASCADE supaya history
  tetap saat user dihapus -- history tidak menghilangkan actor lain).
- kind: kategori event ('expense_pending', 'refund_pending',
  'commission_pending', 'incident_critical', 'target_achieved',
  'vendor_at_risk', dll). Bebas free-text supaya trigger source bisa
  self-describe.
- title: 1 baris headline (max 200 char idealnya).
- body: detail opsional (nominal, jamaah name, dll).
- link: deep-link ke source (mis. '#page-expense?rid=42') -- FE parsing.
- is_read: 0 = unread, 1 = read. Index utk fast count unread per user.
- created_at / read_at: audit timestamp.

Index:
- (user_id, is_read) untuk query counter unread cepat.
- (user_id, created_at DESC) untuk list terbaru cepat.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS user_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            body TEXT,
            link TEXT,
            is_read INTEGER DEFAULT 0,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            read_at DATETIME
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_notif_unread "
        "ON user_notifications(user_id, is_read)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_user_notif_latest "
        "ON user_notifications(user_id, created_at DESC)"
    )
