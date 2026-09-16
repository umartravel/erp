"""
Sprint AK-2: Jurnal Engine (double-entry) untuk transaksi ERP UMAR.

Modul ini adalah SATU-satunya pintu resmi utk tulis `journal_lines`. Semua
flow (payment jamaah, refund, expense pay, procurement pay, payroll, komisi
agen, setor tunai, prive) memanggil `post_journal(tx_id, entries)` sesudah
INSERT transactions.

Invariant utama (zero-sum PSAK): SUM(debit) == SUM(credit) per transaction_id.
Diperiksa sebelum INSERT; kalau gagal -> ValueError, tidak ada baris tersimpan.

Reversing entry (`reverse_journal`) menghasilkan `transactions` BARU dgn
`status='REVERSED'` dan `reversal_of` FK ke tx original -- lalu tulis
journal_lines yang debit<->credit di-swap. Transaction asli di-set
`status='REVERSED'` juga (immutable, tidak dihapus). Dipakai Sprint AK-5
utk gantiin flow DELETE transactions.

Convention: semua nilai di argumen `entries` berupa integer rupiah
(bukan float). Kolom `debit`/`credit` di DB tipe INTEGER (rupiah bulat).
"""
from typing import Iterable

import db


# --- Cache lookup COA ---------------------------------------------------
# `chart_of_accounts` isinya statis (35 baris seed + tambahan admin). Cache
# `account_code -> id` di process supaya post_journal ga hit DB tiap panggil.
_CODE_TO_ID: dict[str, int] = {}


def coa_id(account_code: str) -> int:
    """Lookup chart_of_accounts.id via account_code (4-digit).

    Raise ValueError kalau kode tidak ada. Caller diharapkan pakai kode COA
    valid (yg di-seed di migration 015).
    """
    if account_code in _CODE_TO_ID:
        return _CODE_TO_ID[account_code]
    row = db.query_one(
        "SELECT id FROM chart_of_accounts WHERE account_code = ? AND is_active = 1",
        (account_code,),
    )
    if not row:
        raise ValueError(f"COA account_code '{account_code}' tidak ada / nonaktif.")
    _CODE_TO_ID[account_code] = row["id"]
    return row["id"]


def coa_id_safe(account_code: str) -> int | None:
    """Versi yg return None kalau tidak ada (utk backfill non-mandatory)."""
    try:
        return coa_id(account_code)
    except ValueError:
        return None


def invalidate_coa_cache() -> None:
    """Test hook: clear cache setelah migration 015 di-apply di test fixture."""
    _CODE_TO_ID.clear()


# --- Constants: kode COA yg sering dipakai flow -------------------------
COA_KAS_KECIL = "1101"
COA_BANK_MANDIRI = "1102"
COA_BANK_BSI = "1103"
COA_PREPAID_UMRAH = "1108"
COA_KAS_KLIRING = "1109"
COA_UNEARNED_REVENUE = "2101"
COA_HUTANG_VENDOR = "2102"
COA_PRIVE = "3102"
COA_REVENUE_UMRAH = "4101"
COA_COGS_TIKET = "5101"
COA_BEBAN_GAJI = "6101"
COA_BEBAN_KOMISI = "6102"
COA_BEBAN_ADMIN_BANK = "6201"


def default_cash_account() -> int:
    """COA default utk sisi Kas. Sementara: Bank Mandiri (1102).

    Ketika Sprint AK-6 (Cash-in-Transit) ada, flow tertentu akan pakai
    1101 Kas Kecil / 1109 Kliring secara eksplisit. Sisa flow tetap 1102.
    """
    return coa_id(COA_BANK_MANDIRI)


# --- Core: post_journal --------------------------------------------------


def post_journal(
    transaction_id: int,
    entries: Iterable[tuple[int, int, int, str | None]],
) -> list[int]:
    """Insert baris jurnal double-entry utk 1 transactions.

    Args:
        transaction_id: transactions.id yg sudah di-INSERT sebelumnya.
        entries: iterable of (account_id, debit, credit, memo).
                 Setiap baris HARUS punya salah satu debit atau credit > 0,
                 dan tidak boleh keduanya > 0.

    Returns:
        list of inserted journal_lines.id.

    Raises:
        ValueError kalau (a) entries kosong, (b) zero-sum gagal, (c) suatu
        baris punya debit>0 DAN credit>0 sekaligus. Tidak ada baris ditulis
        kalau gagal (fail-fast, sebelum INSERT).
    """
    rows = [
        (int(acc_id), int(deb or 0), int(cred or 0), memo)
        for (acc_id, deb, cred, memo) in entries
    ]
    if not rows:
        raise ValueError("post_journal: entries wajib >= 1 baris.")

    total_debit = 0
    total_credit = 0
    for i, (_aid, deb, cred, _memo) in enumerate(rows):
        if deb < 0 or cred < 0:
            raise ValueError(
                f"post_journal entry #{i}: debit/credit tidak boleh negatif."
            )
        if deb > 0 and cred > 0:
            raise ValueError(
                f"post_journal entry #{i}: debit DAN credit tidak boleh dua-duanya > 0."
            )
        if deb == 0 and cred == 0:
            raise ValueError(
                f"post_journal entry #{i}: minimal salah satu debit/credit > 0."
            )
        total_debit += deb
        total_credit += cred

    if total_debit != total_credit:
        raise ValueError(
            f"post_journal ZERO-SUM GAGAL utk tx #{transaction_id}: "
            f"SUM(debit)={total_debit:,} != SUM(credit)={total_credit:,}"
        )

    inserted_ids = []
    for acc_id, deb, cred, memo in rows:
        line_id, _ = db.execute(
            "INSERT INTO journal_lines (transaction_id, account_id, debit, credit, memo) "
            "VALUES (?, ?, ?, ?, ?)",
            (transaction_id, acc_id, deb, cred, memo),
        )
        inserted_ids.append(line_id)

    db.execute(
        "UPDATE transactions SET status = 'POSTED' WHERE id = ? AND status IS NULL",
        (transaction_id,),
    )
    return inserted_ids


# --- Reversing entry (Sprint AK-5, sudah siap sejak AK-2) ---------------


def reverse_journal(transaction_id: int, reason: str,
                    user_name: str | None = None) -> int:
    """Bikin transactions baru dgn journal_lines yg swap debit<->credit.

    Args:
        transaction_id: tx original yg mau di-reverse.
        reason: catatan alasan reversal.
        user_name: nama user yg trigger reversal (audit).

    Returns:
        transactions.id baru (reversing entry).

    Raises:
        ValueError kalau tx original tidak ada, sudah REVERSED, atau tidak
        punya journal_lines.
    """
    orig = db.query_one(
        "SELECT id, type, category, amount, description, status, reversal_of, "
        "  reference_id, package_name, category_id "
        "FROM transactions WHERE id = ?", (transaction_id,))
    if not orig:
        raise ValueError(f"Transaksi #{transaction_id} tidak ditemukan.")
    if orig["status"] == "REVERSED":
        raise ValueError(f"Transaksi #{transaction_id} sudah di-reverse.")
    if orig["reversal_of"]:
        raise ValueError(
            f"Transaksi #{transaction_id} sendiri adalah reversing entry, "
            f"tidak bisa di-reverse lagi."
        )

    lines = db.query_all(
        "SELECT account_id, debit, credit, memo FROM journal_lines "
        "WHERE transaction_id = ? ORDER BY id", (transaction_id,))
    if not lines:
        raise ValueError(
            f"Transaksi #{transaction_id} belum punya journal_lines "
            f"(mungkin data historis pre-AK1). Backfill dulu."
        )

    new_desc = f"REVERSAL: {orig['description'] or ''} | Alasan: {reason}"
    new_tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, "
        "reference_id, package_name, category_id, status, reversal_of) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 'REVERSED', ?)",
        (orig["type"], orig["category"], orig["amount"], new_desc,
         orig["reference_id"], orig["package_name"], orig["category_id"],
         transaction_id),
    )

    swapped = [
        (ln["account_id"], ln["credit"], ln["debit"],
         f"Reversal of tx #{transaction_id}: {ln['memo'] or ''}".strip(": "))
        for ln in lines
    ]
    post_journal(new_tx_id, swapped)

    db.execute(
        "UPDATE transactions SET status = 'REVERSED' WHERE id = ?",
        (transaction_id,),
    )
    return new_tx_id


# --- Convenience wrappers utk flow-flow spesifik -------------------------


def post_jamaah_dp(tx_id: int, amount: int, jamaah_name: str,
                   cash_account_code: str = COA_BANK_MANDIRI) -> None:
    """DP / Cicilan / Pelunasan jamaah -> Dr Kas, Cr Unearned Revenue (2101).

    Sesuai PSAK 72/115: pendapatan baru diakui saat jamaah berangkat
    (performance obligation terpenuhi). Sampai itu ini kewajiban perusahaan.
    Realisasi Rev 4101 dilakukan bulk oleh month-end closing (AK-3).
    """
    memo = f"DP/Cicilan Umrah: {jamaah_name}"
    post_journal(tx_id, [
        (coa_id(cash_account_code), amount, 0, memo),
        (coa_id(COA_UNEARNED_REVENUE), 0, amount, memo),
    ])


def post_jamaah_refund(tx_id: int, amount: int, jamaah_name: str,
                       cash_account_code: str = COA_BANK_MANDIRI) -> None:
    """Refund jamaah (uang keluar) -> Dr Unearned Revenue, Cr Kas."""
    memo = f"Refund Umrah: {jamaah_name}"
    post_journal(tx_id, [
        (coa_id(COA_UNEARNED_REVENUE), amount, 0, memo),
        (coa_id(cash_account_code), 0, amount, memo),
    ])


def post_expense_paid(tx_id: int, amount: int, default_account_id: int | None,
                      description: str,
                      cash_account_code: str = COA_BANK_MANDIRI) -> None:
    """Expense Report paid -> Dr <default_account>, Cr Kas.

    Fallback ke 6201 Beban Administrasi Bank kalau kategori belum di-map ke
    COA (admin dpt fix mapping di UI Kelola Kategori kapan saja).
    """
    dr_account = default_account_id or coa_id(COA_BEBAN_ADMIN_BANK)
    post_journal(tx_id, [
        (dr_account, amount, 0, description),
        (coa_id(cash_account_code), 0, amount, description),
    ])


def post_procurement_paid(tx_id: int, amount: int, vendor_name: str,
                          departure_date: str | None,
                          today_iso: str,
                          cash_account_code: str = COA_BANK_MANDIRI) -> None:
    """Procurement paid.

    Kalau `departure_date > today` -> Dr Prepaid Umrah (1108).
    Kalau paket sudah berangkat / tidak ada tanggal -> Dr COGS Tiket (5101).
    Split per komponen HPP (5102 hotel, 5103 visa, dll) dilakukan di
    Sprint AK-3 closing bulan.
    """
    if departure_date and departure_date > today_iso:
        dr_account = coa_id(COA_PREPAID_UMRAH)
    else:
        dr_account = coa_id(COA_COGS_TIKET)
    memo = f"Bayar vendor: {vendor_name}"
    post_journal(tx_id, [
        (dr_account, amount, 0, memo),
        (coa_id(cash_account_code), 0, amount, memo),
    ])


def post_payroll(tx_id: int, amount: int, employee_name: str,
                 cash_account_code: str = COA_BANK_MANDIRI) -> None:
    """Payroll bulanan -> Dr Beban Gaji (6101), Cr Kas."""
    memo = f"Gaji: {employee_name}"
    post_journal(tx_id, [
        (coa_id(COA_BEBAN_GAJI), amount, 0, memo),
        (coa_id(cash_account_code), 0, amount, memo),
    ])


def post_commission(tx_id: int, amount: int, agent_name: str, jamaah_name: str,
                    cash_account_code: str = COA_BANK_MANDIRI) -> None:
    """Pencairan komisi agen -> Dr Beban Komisi (6102), Cr Kas."""
    memo = f"Komisi agen {agent_name} (jamaah: {jamaah_name})"
    post_journal(tx_id, [
        (coa_id(COA_BEBAN_KOMISI), amount, 0, memo),
        (coa_id(cash_account_code), 0, amount, memo),
    ])


def post_setor_tunai(tx_id: int, amount: int, memo: str) -> None:
    """Setor tunai Kas Kecil -> Bank -- LEG 1 (belum di-confirm bank).

    Dr Kas Kliring (1109), Cr Kas Kecil (1101).
    Leg 2 (Dr Bank, Cr 1109) dijalankan saat auto-match reconcile bank atau
    tombol manual "Konfirmasi Terima". Implementasi UI di Sprint AK-6.
    """
    post_journal(tx_id, [
        (coa_id(COA_KAS_KLIRING), amount, 0, memo),
        (coa_id(COA_KAS_KECIL), 0, amount, memo),
    ])


def post_prive(tx_id: int, amount: int, memo: str,
               cash_account_code: str = COA_BANK_MANDIRI) -> None:
    """Prive pemilik (owner draw) -> Dr Prive (3102), Cr Kas."""
    post_journal(tx_id, [
        (coa_id(COA_PRIVE), amount, 0, memo),
        (coa_id(cash_account_code), 0, amount, memo),
    ])


def post_generic_income(tx_id: int, amount: int, credit_account_id: int | None,
                        description: str,
                        cash_account_code: str = COA_BANK_MANDIRI) -> None:
    """Income non-jamaah generic (bunga bank, komisi vendor, dll).

    Dr Kas, Cr <credit_account>. Fallback ke 4104 Pendapatan Penjualan Tiket
    & Visa kalau kategori belum mapped.
    """
    cr_account = credit_account_id or coa_id_safe("4104") or coa_id(COA_REVENUE_UMRAH)
    post_journal(tx_id, [
        (coa_id(cash_account_code), amount, 0, description),
        (cr_account, 0, amount, description),
    ])


def post_generic_expense(tx_id: int, amount: int, debit_account_id: int | None,
                         description: str,
                         cash_account_code: str = COA_BANK_MANDIRI) -> None:
    """Expense non-report generic (transaksi manual admin/finance).

    Dr <debit_account>, Cr Kas. Fallback ke 6201 Beban Admin Bank.
    """
    dr_account = debit_account_id or coa_id(COA_BEBAN_ADMIN_BANK)
    post_journal(tx_id, [
        (dr_account, amount, 0, description),
        (coa_id(cash_account_code), 0, amount, description),
    ])
