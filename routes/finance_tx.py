"""
Router Finance Transactions & Reports:
- Buku Kas: GET list + POST expense manual + DELETE (koreksi admin, cascading
  rollback ke jamaah.paid_amount kalau yang dihapus adalah kategori 'payment').
- Payroll: POST bulanan otomatis (idempoten per bulan, opsi 'force' untuk THR).
- Reports: /reports/pnl (per paket) + /reports/expense-matrix (grid paket x kategori).

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
"""
from fastapi import APIRouter

import db
import journal_engine  # Sprint AK-2: payroll + generic income/expense journal
from deps import (
    CAT_GAJI_TUNJANGAN,
    Depends,
    HTTPException,
    authenticate_token,
    json_body,
    log_action,
    notify,
    require_role,
    resolve_cat_id,
    sync_status_mirror,
)

router = APIRouter(tags=["finance-tx"])


# ===========================================================================
# REPORTS
# ===========================================================================
@router.get("/api/reports/pnl")
async def reports_pnl(user=Depends(authenticate_token)):
    # SECURITY: omzet + expense per paket, angka finansial. Sales/ops tidak
    # perlu lihat -- restrict ke admin/finance/management.
    require_role(user, "admin", "finance", "management")
    return db.query_all(
        "SELECT p.name as package_name, "
        "COALESCE((SELECT SUM(total_price) FROM jamaah WHERE package_type = p.name "
        "AND status NOT IN ('Lead - Follow Up', 'Cancelled')), 0) as omset_kotor, "
        "COALESCE((SELECT SUM(paid_amount) FROM jamaah WHERE package_type = p.name "
        "AND status NOT IN ('Lead - Follow Up', 'Cancelled')), 0) as total_income, "
        "COALESCE((SELECT SUM(amount) FROM transactions WHERE type = 'expense' "
        "AND package_name = p.name), 0) as total_expense FROM packages p",
        (),
    )


# Kolom matrik pengeluaran per projek -- payroll SENGAJA dikecualikan (overhead perusahaan,
# bukan biaya proyek). Kategori pengeluaran manual yang teksnya bebas ketik (di luar 4
# kategori baku di bawah) dikelompokkan ke kolom "lainnya" supaya kolom tidak membengkak.
EXPENSE_MATRIX_COLUMNS = [
    ("procurement_payment", "Vendor/Procurement"),
    ("refund", "Refund"),
    ("commission", "Komisi Agen"),
    ("expense_report", "Reimbursement Karyawan"),
    ("lainnya", "Operasional Lainnya"),
]


@router.get("/api/reports/expense-matrix")
async def reports_expense_matrix(user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    packages = db.query_all("SELECT name FROM packages ORDER BY name ASC", ())
    projects = db.query_all(
        "SELECT id, name FROM expense_projects WHERE is_active = 1 ORDER BY name ASC", ()
    )
    txs = db.query_all(
        "SELECT category, amount, package_name, reference_id FROM transactions "
        "WHERE type = 'expense' AND category != 'payroll'", ()
    )
    # Transaksi expense_report tidak menyimpan package_name -- proyeknya (expense_projects)
    # ditelusuri lewat reference_id -> expense_reports.project_id.
    report_project = {
        r["id"]: r["project_id"] for r in db.query_all("SELECT id, project_id FROM expense_reports", ())
    }
    project_names = {p["id"]: p["name"] for p in projects}

    def col_key(category):
        return category if category in dict(EXPENSE_MATRIX_COLUMNS) else "lainnya"

    rows = {}

    def get_row(key, name, rtype):
        if key not in rows:
            rows[key] = {
                "type": rtype, "name": name,
                "values": {k: 0 for k, _ in EXPENSE_MATRIX_COLUMNS}, "total": 0,
            }
        return rows[key]

    for p in packages:
        get_row(f"paket:{p['name']}", p["name"], "Paket")
    for pr in projects:
        get_row(f"proj:{pr['id']}", pr["name"], "Anggaran")
    umum = get_row("umum", "Umum / Tidak Terkait Projek", "Umum")

    for t in txs:
        amount = t["amount"] or 0
        if t["category"] == "expense_report":
            pid = report_project.get(t["reference_id"])
            pname = project_names.get(pid)
            row = get_row(f"proj:{pid}", pname, "Anggaran") if pname else umum
        elif t["package_name"]:
            row = get_row(f"paket:{t['package_name']}", t["package_name"], "Paket")
        else:
            row = umum
        col = col_key(t["category"])
        row["values"][col] += amount
        row["total"] += amount

    type_order = {"Paket": 0, "Anggaran": 1, "Umum": 2}
    result = sorted(rows.values(), key=lambda r: (type_order[r["type"]], -r["total"]))
    return {
        "columns": [{"key": k, "label": label} for k, label in EXPENSE_MATRIX_COLUMNS],
        "rows": result,
    }


# ===========================================================================
# TRANSAKSI (Buku Kas) & PAYROLL
# ===========================================================================
@router.get("/api/transactions")
async def transactions_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    return db.query_all("SELECT * FROM transactions ORDER BY created_at DESC", ())


@router.post("/api/transactions/expense")
async def transactions_expense(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")
    g = body.get
    # Phase F2: category_id opsional (FK expense_categories). category (text)
    # tetap disimpan utk backward-compat + display cepat tanpa join.
    category_id = g("category_id") or None
    if category_id:
        cat = db.query_one(
            "SELECT id, group_type FROM expense_categories WHERE id = ? AND is_active = 1",
            (category_id,))
        if not cat:
            raise HTTPException(status_code=400,
                                detail="category_id tidak valid atau nonaktif.")
        if cat["group_type"] != "expense":
            raise HTTPException(status_code=400,
                                detail="category_id harus group_type='expense'.")
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, package_name, category_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("expense", g("category"), int(g("amount")), g("description"),
         g("package_name") or None, category_id),
    )
    # Sprint AK-2: post double-entry -- Dr <default_account or 6201 fallback>, Cr Kas.
    default_acc_id = None
    if category_id:
        cat_full = db.query_one(
            "SELECT default_account_id FROM expense_categories WHERE id = ?",
            (category_id,))
        if cat_full:
            default_acc_id = cat_full["default_account_id"]
    try:
        journal_engine.post_generic_expense(
            tx_id, int(g("amount")), default_acc_id,
            g("description") or f"Pengeluaran {g('category') or ''}",
        )
    except Exception as exc:  # noqa: BLE001
        log_action(user, "JOURNAL_POST_FAIL",
                   f"tx #{tx_id} expense manual: {exc}")
    log_action(user, "EXPENSE", f"Catat pengeluaran Rp {g('amount')} ({g('category')})")
    notify("data_updated", "transaction")
    return {"message": "Pengeluaran operasional berhasil dicatat."}


@router.post("/api/transactions/income")
async def transactions_income(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Phase F2: Catat pemasukan NON-jamaah (bunga bank, komisi vendor, dsb)
    dgn kategori. Pemasukan jamaah tetap via jamaah_payment_submissions flow."""
    require_role(user, "admin", "finance")
    g = body.get
    amount = int(g("amount") or 0)
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Jumlah harus > 0.")
    category_id = g("category_id") or None
    if category_id:
        cat = db.query_one(
            "SELECT id, group_type FROM expense_categories WHERE id = ? AND is_active = 1",
            (category_id,))
        if not cat:
            raise HTTPException(status_code=400,
                                detail="category_id tidak valid atau nonaktif.")
        if cat["group_type"] != "income":
            raise HTTPException(status_code=400,
                                detail="category_id harus group_type='income'.")
    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, category_id) "
        "VALUES (?, ?, ?, ?, ?)",
        ("income", g("category") or "other", amount,
         g("description") or "", category_id),
    )
    # Sprint AK-2: post double-entry -- Dr Kas, Cr <default_account or 4104 fallback>.
    credit_acc_id = None
    if category_id:
        cat_full = db.query_one(
            "SELECT default_account_id FROM expense_categories WHERE id = ?",
            (category_id,))
        if cat_full:
            credit_acc_id = cat_full["default_account_id"]
    try:
        journal_engine.post_generic_income(
            tx_id, amount, credit_acc_id,
            g("description") or f"Pemasukan {g('category') or ''}",
        )
    except Exception as exc:  # noqa: BLE001
        log_action(user, "JOURNAL_POST_FAIL",
                   f"tx #{tx_id} income manual: {exc}")
    log_action(user, "INCOME", f"Catat pemasukan Rp {amount}")
    notify("data_updated", "transaction")
    return {"message": "Pemasukan berhasil dicatat."}


@router.post("/api/payroll")
async def payroll(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")

    # FIX (integritas): cegah penggajian dobel di bulan yang sama. Tanpa ini,
    # tombol payroll bisa diklik berkali-kali dan mencatat gaji ganda di buku kas.
    already = db.query_one(
        "SELECT COUNT(*) as c FROM transactions WHERE category = 'payroll' "
        "AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')"
    )
    if already and already["c"] > 0 and not body.get("force"):
        raise HTTPException(
            status_code=400,
            detail="Penggajian bulan ini sudah diproses. Centang 'Paksa Ulang' bila memang ingin "
            "menggaji lagi (mis. THR), atau hapus entri payroll lama lebih dulu.",
        )

    users = db.query_all("SELECT id, name, base_salary FROM users WHERE base_salary > 0", ())
    processed = 0
    for u in users:
        tx_id, _ = db.execute(
            "INSERT INTO transactions (type, category, amount, description, reference_id, category_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("expense", "payroll", u["base_salary"], f"Gaji Karyawan: {u['name']}", u["id"],
             resolve_cat_id(CAT_GAJI_TUNJANGAN)),
        )
        # Sprint AK-2: post double-entry -- Dr 6101 Beban Gaji, Cr Kas.
        try:
            journal_engine.post_payroll(tx_id, u["base_salary"], u["name"])
        except Exception as exc:  # noqa: BLE001
            log_action(user, "JOURNAL_POST_FAIL",
                       f"tx #{tx_id} payroll user #{u['id']}: {exc}")
        processed += 1
    log_action(user, "PAYROLL", f"Memproses penggajian untuk {processed} karyawan")
    notify("data_updated", "transaction")
    return {"message": f"Penggajian untuk {processed} karyawan berhasil diproses ke buku kas."}


@router.delete("/api/transactions/{tid}")
async def transactions_delete(tid: int, user=Depends(authenticate_token)):
    require_role(user, "admin")

    tx = db.query_one(
        "SELECT type, category, amount, reference_id, status "
        "FROM transactions WHERE id = ?", (tid,))
    if not tx:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")

    # Sprint AK-5: Immutable posting -- transaksi POSTED tidak boleh
    # dihapus (akan bikin journal_lines hantu di ledger). Wajib pakai
    # POST /api/finance/reverse/{tid} yang menghasilkan reversing entry.
    if tx["status"] == "POSTED":
        raise HTTPException(
            status_code=400,
            detail="Transaksi POSTED tidak boleh dihapus (immutable). "
                   "Gunakan POST /api/finance/reverse/{id} untuk membalikkan.",
        )
    if tx["status"] == "REVERSED":
        raise HTTPException(
            status_code=400,
            detail="Transaksi sudah REVERSED, tidak perlu dihapus.",
        )

    # Legacy path: hanya tx status=DRAFT/NULL yang boleh true-delete
    # (mis. tx pre-AK1 belum ke-set status, atau flow lama yg tidak melalui
    # journal_engine).
    db.execute("DELETE FROM transactions WHERE id = ?", (tid,))

    if tx["category"] == "payment" and tx["reference_id"]:
        jamaah = db.query_one(
            "SELECT paid_amount, total_price FROM jamaah WHERE id = ?", (tx["reference_id"],)
        )
        if jamaah:
            new_paid = max(0, (jamaah["paid_amount"] or 0) - (tx["amount"] or 0))
            total = jamaah["total_price"] or 0
            if total > 0 and new_paid >= total:
                new_payment = "Lunas"
            elif new_paid > 0:
                new_payment = "DP"
            else:
                new_payment = "Unpaid"
            db.execute(
                "UPDATE jamaah SET paid_amount = ?, payment_status = ? WHERE id = ?",
                (new_paid, new_payment, tx["reference_id"]),
            )
            sync_status_mirror(tx["reference_id"])
            notify("data_updated", "jamaah")

    log_action(user, "DELETE_TX", f"Koreksi Admin: Hapus transaksi Buku Kas ID {tid}")
    notify("data_updated", "transaction")
    return {"message": "Transaksi berhasil dihapus (Koreksi Admin)."}


# ===========================================================================
# Sprint AK-5: Reversal endpoint + Recent Ledger Entries
# ===========================================================================


@router.post("/api/finance/reverse/{tid}")
async def transactions_reverse(tid: int, body: dict = Depends(json_body),
                               user=Depends(authenticate_token)):
    """Reverse tx POSTED lewat journal_engine (bikin new tx REVERSED + swap
    journal_lines). Ini pengganti sah utk DELETE tx POSTED -- audit trail utuh,
    Neraca+LR tetap balanced.

    Body: {reason: str}. Admin+finance only.
    """
    require_role(user, "admin", "finance")
    reason = (body.get("reason") or "").strip()
    if len(reason) < 5:
        raise HTTPException(
            status_code=400,
            detail="Alasan reversal wajib diisi (min 5 karakter).",
        )
    try:
        new_tx = journal_engine.reverse_journal(tid, reason, user_name=user["name"])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    log_action(user, "REVERSE_TX",
               f"Reverse tx #{tid} -> new tx #{new_tx}. Alasan: {reason}")
    notify("data_updated", "transaction")
    return {
        "message": f"Transaksi #{tid} berhasil di-reverse. Reversing entry: #{new_tx}.",
        "original_tx_id": tid,
        "reversing_tx_id": new_tx,
    }


@router.get("/api/finance/ledger/recent")
async def ledger_recent(limit: int = 20, user=Depends(authenticate_token)):
    """List tx terakhir + journal_lines pair-nya. Untuk section Recent Ledger
    Entries di dashboard finance."""
    require_role(user, "admin", "finance", "management")
    if limit < 1 or limit > 100:
        limit = 20
    tx_rows = db.query_all(
        "SELECT id, type, category, amount, description, reference_id, "
        "  package_name, status, transaction_no, reversal_of, created_at "
        "FROM transactions "
        "WHERE status != 'REVERSED' "
        "ORDER BY id DESC LIMIT ?", (limit,),
    )
    tx_ids = [t["id"] for t in tx_rows]
    lines_by_tx: dict[int, list[dict]] = {tid: [] for tid in tx_ids}
    if tx_ids:
        placeholders = ",".join("?" for _ in tx_ids)
        lines = db.query_all(
            f"SELECT jl.transaction_id, jl.debit, jl.credit, jl.memo, "
            f"  ca.account_code, ca.account_name, ca.account_group "
            f"FROM journal_lines jl "
            f"JOIN chart_of_accounts ca ON ca.id = jl.account_id "
            f"WHERE jl.transaction_id IN ({placeholders}) "
            f"ORDER BY jl.id",
            tuple(tx_ids),
        )
        for ln in lines:
            lines_by_tx.setdefault(ln["transaction_id"], []).append(ln)
    for t in tx_rows:
        t["journal_lines"] = lines_by_tx.get(t["id"], [])
    return {"entries": tx_rows}
