"""
Sprint AK-1: Journal Lines + extend transactions dgn status lifecycle.

Table `journal_lines` = tabel jurnal double-entry. Setiap transactions punya
>= 2 baris (min Debit + Kredit) yang harus zero-sum
(SUM(debit) == SUM(credit) per transaction_id).

Zero-sum invariant di-enforce di application layer (journal_engine.py, Sprint
AK-2), bukan SQL trigger.

Extend `transactions`:
- `transaction_no` TEXT UNIQUE -- format TRX-UMAR-YYYY-NNNNNN, app-side.
- `status` TEXT default POSTED -- lifecycle DRAFT|POSTED|VOID|REVERSED.
- `reversal_of` FK ke transactions.id (untuk reversing entry di AK-5).
Row existing di-backfill status='POSTED'.
"""
import sqlite3


def up(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS journal_lines (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL,
            account_id INTEGER NOT NULL,
            debit INTEGER NOT NULL DEFAULT 0,
            credit INTEGER NOT NULL DEFAULT 0,
            memo TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (transaction_id) REFERENCES transactions(id) ON DELETE CASCADE,
            FOREIGN KEY (account_id) REFERENCES chart_of_accounts(id)
        )
    """)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jl_tx "
        "ON journal_lines (transaction_id)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_jl_account_created "
        "ON journal_lines (account_id, created_at DESC)"
    )

    cols = {r[1] for r in conn.execute("PRAGMA table_info(transactions)").fetchall()}
    if "transaction_no" not in cols:
        conn.execute("ALTER TABLE transactions ADD COLUMN transaction_no TEXT")
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_transactions_txno "
            "ON transactions (transaction_no) WHERE transaction_no IS NOT NULL"
        )
    if "status" not in cols:
        conn.execute(
            "ALTER TABLE transactions ADD COLUMN status TEXT "
            "CHECK(status IN ('DRAFT','POSTED','VOID','REVERSED'))"
        )
        conn.execute(
            "UPDATE transactions SET status = 'POSTED' WHERE status IS NULL"
        )
    if "reversal_of" not in cols:
        conn.execute(
            "ALTER TABLE transactions ADD COLUMN reversal_of INTEGER "
            "REFERENCES transactions(id) ON DELETE SET NULL"
        )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_transactions_status "
        "ON transactions (status, created_at DESC)"
    )
