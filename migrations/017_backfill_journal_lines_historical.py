"""
Sprint AK-2: Backfill journal_lines untuk transactions historis (pre-AK2).

Target: 284+ transactions existing di prod DB yang belum punya journal_lines.
Tanpa backfill, endpoint /api/journal/{tx_id} return lines kosong, dan
reversing entry gagal (butuh journal_lines utk mirror-swap).

Strategi konservatif per kategori legacy:

| category (legacy)         | Dr           | Cr                        |
|---------------------------|--------------|---------------------------|
| payment                   | 1102 Bank    | 2101 Unearned Revenue     |
| refund                    | 2101 Unearned| 1102 Bank                 |
| expense_report            | <cat.default_account or 6201>  | 1102 Bank |
| procurement_payment       | 5101 COGS    | 1102 Bank                 |
| commission                | 6102 Beban Komisi | 1102 Bank             |
| payroll                   | 6101 Beban Gaji  | 1102 Bank              |
| lainnya (income)          | 1102 Bank    | 4104 Pendapatan Tiket     |
| lainnya (expense)         | 6201 Beban Adm Bank | 1102 Bank          |

Procurement historis di-assume masuk COGS 5101 (paket lama biasanya sudah
berangkat); ini bisa di-fix retroaktif Sprint AK-3 saat closing bulan.

Idempotent: skip tx yang sudah punya journal_lines.
"""
import sqlite3


_LEGACY_MAP: dict[str, tuple[str, str]] = {
    "payment": ("1102", "2101"),
    "refund": ("2101", "1102"),
    "commission": ("6102", "1102"),
    "payroll": ("6101", "1102"),
    "procurement_payment": ("5101", "1102"),
}


def _coa_ids(conn: sqlite3.Connection) -> dict[str, int]:
    rows = conn.execute(
        "SELECT id, account_code FROM chart_of_accounts WHERE is_active = 1"
    ).fetchall()
    return {r[1]: r[0] for r in rows}


def up(conn: sqlite3.Connection) -> None:
    coa = _coa_ids(conn)
    if "1102" not in coa or "2101" not in coa:
        return

    tx_rows = conn.execute(
        "SELECT t.id, t.type, t.category, t.amount, t.description, t.category_id, "
        "  t.package_name, t.status "
        "FROM transactions t "
        "LEFT JOIN journal_lines jl ON jl.transaction_id = t.id "
        "WHERE jl.id IS NULL "
        "GROUP BY t.id "
        "ORDER BY t.id"
    ).fetchall()

    backfilled = 0
    skipped = 0

    for tx in tx_rows:
        tx_id, tx_type, tx_cat, amount, desc, cat_id, _pkg, status = tx
        amount = int(amount or 0)
        if amount <= 0:
            skipped += 1
            continue

        dr_code = None
        cr_code = None

        if tx_cat in _LEGACY_MAP:
            dr_code, cr_code = _LEGACY_MAP[tx_cat]
        elif tx_cat == "expense_report":
            if cat_id:
                cat_row = conn.execute(
                    "SELECT ca.account_code FROM expense_categories ec "
                    "LEFT JOIN chart_of_accounts ca ON ca.id = ec.default_account_id "
                    "WHERE ec.id = ?", (cat_id,)
                ).fetchone()
                if cat_row and cat_row[0]:
                    dr_code = cat_row[0]
            if not dr_code:
                dr_code = "6201"
            cr_code = "1102"
        elif tx_type == "income":
            dr_code = "1102"
            cr_code = "4104"
        elif tx_type == "expense":
            dr_code = "6201"
            cr_code = "1102"
        else:
            skipped += 1
            continue

        if dr_code not in coa or cr_code not in coa:
            skipped += 1
            continue

        dr_id = coa[dr_code]
        cr_id = coa[cr_code]
        memo = f"[Backfill AK-2] {desc or tx_cat or ''}"[:255]

        conn.execute(
            "INSERT INTO journal_lines (transaction_id, account_id, debit, credit, memo) "
            "VALUES (?, ?, ?, ?, ?)",
            (tx_id, dr_id, amount, 0, memo),
        )
        conn.execute(
            "INSERT INTO journal_lines (transaction_id, account_id, debit, credit, memo) "
            "VALUES (?, ?, ?, ?, ?)",
            (tx_id, cr_id, 0, amount, memo),
        )
        if status is None:
            conn.execute(
                "UPDATE transactions SET status = 'POSTED' WHERE id = ?", (tx_id,)
            )
        backfilled += 1

    imbalance = conn.execute(
        "SELECT transaction_id, SUM(debit) d, SUM(credit) c "
        "FROM journal_lines GROUP BY transaction_id "
        "HAVING SUM(debit) != SUM(credit)"
    ).fetchall()
    if imbalance:
        raise RuntimeError(
            f"017 backfill: {len(imbalance)} tx unbalanced setelah backfill. "
            f"Contoh: {imbalance[:3]}"
        )

    print(f"[017] backfilled {backfilled} tx, skipped {skipped}")
