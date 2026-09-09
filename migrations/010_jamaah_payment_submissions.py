"""
Phase 13a: Maker-checker Pembayaran Jamaah.

Sales input DP/Cicilan/Pelunasan -> status Pending. Finance verifikasi
uang benar masuk rekening -> ACC (verify) -> baru apply ke jamaah.paid_amount
dan insert ke transactions.

Sebelumnya endpoint PUT /api/jamaah/{jid}/payment cuma bisa admin/finance,
sehingga sales harus laporan manual ke finance untuk input. Sekarang sales
punya endpoint POST /api/jamaah/{jid}/payment-submissions untuk submit,
lalu finance review via PUT /api/payment-submissions/{sid}/review.

Schema jamaah_payment_submissions:
- jamaah_id: FK ke jamaah
- amount: nominal
- payment_kind: 'DP' | 'Cicilan' | 'Pelunasan' (label utk UI)
- payment_method: 'Transfer' | 'Cash' | 'VA' | 'Lainnya'
- bank_account: rekening tujuan (mis. "BCA 1234-56789" atau "Kas Kantor")
- notes: keterangan
- status: 'Pending' | 'Verified' | 'Rejected'
- submitted_by / submitted_by_id: sales
- reviewed_by: finance yg ACC
- reject_reason: kalau ditolak
- transaction_id: FK ke transactions row saat verify (audit trail)

Index (jamaah_id) supaya listing history per jamaah cepat.
Index (status) supaya queue Pending cepat di-list finance.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS jamaah_payment_submissions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            jamaah_id INTEGER NOT NULL,
            amount INTEGER NOT NULL,
            payment_kind TEXT NOT NULL DEFAULT 'Cicilan',
            payment_method TEXT,
            bank_account TEXT,
            notes TEXT,
            status TEXT NOT NULL DEFAULT 'Pending',
            submitted_by TEXT,
            submitted_by_id INTEGER,
            submitted_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            reviewed_by TEXT,
            reviewed_at DATETIME,
            reject_reason TEXT,
            transaction_id INTEGER
        )"""
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jps_jamaah "
        "ON jamaah_payment_submissions (jamaah_id, submitted_at DESC)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jps_status "
        "ON jamaah_payment_submissions (status, submitted_at DESC)"
    )
