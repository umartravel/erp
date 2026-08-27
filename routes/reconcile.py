"""
Router Rekonsiliasi Bank (F-E).
Alur: upload CSV mutasi bank -> parse -> insert Unmatched -> user pilih transaksi
cocok (auto-suggest amount + tanggal +-3 hari) -> Matched. Hanya finance + admin.
"""
import uuid

from fastapi import APIRouter

import db
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    json_body,
    log_action,
    notify,
    require_role,
)
from reconcile_importer import parse_csv as reconcile_parse_csv
from reconcile_importer import row_hash as reconcile_row_hash

router = APIRouter(prefix="/api/finance/reconcile", tags=["reconcile"])

_ROLES = ("admin", "finance")


@router.post("/import")
async def reconcile_import(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Upload CSV mutasi bank. Body: {csv_text: str, bank_hint?: str}."""
    require_role(user, *_ROLES)
    text = body.get("csv_text") or ""
    hint = body.get("bank_hint") or None
    if not text.strip():
        raise HTTPException(status_code=400, detail="CSV kosong.")
    result = reconcile_parse_csv(text, hint_bank=hint)
    bank = result["bank"]
    rows = result["rows"]
    if not rows:
        return {"batch": None, "bank": bank, "inserted": 0, "duplicates": 0, "warnings": result["warnings"]}

    batch = uuid.uuid4().hex[:12]
    inserted = 0
    duplicates = 0
    for r in rows:
        h = reconcile_row_hash(bank, r["mutation_date"], r["amount"], r["direction"], r["description"])
        try:
            db.execute(
                "INSERT INTO bank_mutations (import_batch, bank_name, mutation_date, description, "
                "amount, direction, reference, balance, row_hash, imported_by) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (batch, bank, r["mutation_date"], r["description"], r["amount"], r["direction"],
                 r["reference"], r["balance"], h, user["name"]),
            )
            inserted += 1
        except Exception:  # noqa: BLE001
            duplicates += 1

    log_action(user, "RECONCILE_IMPORT",
               f"Import batch {batch} ({bank}): {inserted} baru, {duplicates} duplikat")
    notify("data_updated", "bank_mutations")
    return {"batch": batch, "bank": bank, "inserted": inserted,
            "duplicates": duplicates, "warnings": result["warnings"]}


@router.get("/batches")
async def reconcile_batches(user=Depends(authenticate_token)):
    """Daftar batch import + summary counts per status."""
    require_role(user, *_ROLES)
    return db.query_all(
        "SELECT import_batch AS batch, bank_name AS bank, "
        "       MIN(created_at) AS imported_at, imported_by, "
        "       COUNT(*) AS total, "
        "       SUM(CASE WHEN match_status='Matched' THEN 1 ELSE 0 END) AS matched, "
        "       SUM(CASE WHEN match_status='Ignored' THEN 1 ELSE 0 END) AS ignored, "
        "       SUM(CASE WHEN match_status='Unmatched' THEN 1 ELSE 0 END) AS unmatched "
        "FROM bank_mutations GROUP BY import_batch, bank_name, imported_by ORDER BY imported_at DESC"
    )


@router.get("/mutations")
async def reconcile_mutations(batch: str = "", status: str = "",
                              user=Depends(authenticate_token)):
    """List mutasi. Filter opsional: batch, status (Unmatched/Matched/Ignored)."""
    require_role(user, *_ROLES)
    where = []
    params = []
    if batch:
        where.append("import_batch = ?")
        params.append(batch)
    if status:
        where.append("match_status = ?")
        params.append(status)
    clause = "WHERE " + " AND ".join(where) if where else ""
    return db.query_all(
        f"SELECT id, import_batch, bank_name, mutation_date, description, amount, direction, "
        f"       reference, balance, match_status, transaction_id, matched_by, matched_at "
        f"FROM bank_mutations {clause} ORDER BY mutation_date DESC, id DESC LIMIT 500",
        tuple(params),
    )


@router.get("/mutations/{mid}/candidates")
async def reconcile_candidates(mid: int, user=Depends(authenticate_token)):
    """Auto-suggest transaksi kandidat: amount sama + tanggal +-3 hari, belum ter-match."""
    require_role(user, *_ROLES)
    m = db.query_one("SELECT * FROM bank_mutations WHERE id = ?", (mid,))
    if not m:
        raise HTTPException(status_code=404, detail="Mutasi tidak ditemukan.")
    tx_type = "income" if m["direction"] == "credit" else "expense"
    return db.query_all(
        "SELECT t.id, t.type, t.category, t.amount, t.description, t.created_at, "
        "       ABS(julianday(date(t.created_at)) - julianday(?)) AS day_diff "
        "FROM transactions t "
        "WHERE t.type = ? AND t.amount = ? "
        "  AND ABS(julianday(date(t.created_at)) - julianday(?)) <= 3 "
        "  AND t.id NOT IN (SELECT transaction_id FROM bank_mutations WHERE transaction_id IS NOT NULL) "
        "ORDER BY day_diff ASC, t.id DESC LIMIT 20",
        (m["mutation_date"], tx_type, m["amount"], m["mutation_date"]),
    )


@router.post("/match")
async def reconcile_match(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Body: {mutation_id: int, transaction_id: int}. Link mutation ke transaksi."""
    require_role(user, *_ROLES)
    mid = body.get("mutation_id")
    tid = body.get("transaction_id")
    if not mid or not tid:
        raise HTTPException(status_code=400, detail="mutation_id dan transaction_id wajib.")
    m = db.query_one("SELECT * FROM bank_mutations WHERE id = ?", (mid,))
    t = db.query_one("SELECT * FROM transactions WHERE id = ?", (tid,))
    if not m or not t:
        raise HTTPException(status_code=404, detail="Data tidak ditemukan.")
    if m["match_status"] == "Matched":
        raise HTTPException(status_code=400, detail="Mutasi sudah ter-match.")
    dup = db.query_one("SELECT id FROM bank_mutations WHERE transaction_id = ?", (tid,))
    if dup:
        raise HTTPException(status_code=400, detail=f"Transaksi #{tid} sudah ter-match ke mutasi lain.")
    db.execute(
        "UPDATE bank_mutations SET match_status='Matched', transaction_id=?, "
        "matched_by=?, matched_at=CURRENT_TIMESTAMP WHERE id=?",
        (tid, user["name"], mid),
    )
    log_action(user, "RECONCILE_MATCH", f"Mutation #{mid} -> transaction #{tid}")
    notify("data_updated", "bank_mutations")
    return {"message": "Match tersimpan."}


@router.post("/unmatch")
async def reconcile_unmatch(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Body: {mutation_id: int}. Lepas link (kembali Unmatched)."""
    require_role(user, *_ROLES)
    mid = body.get("mutation_id")
    if not mid:
        raise HTTPException(status_code=400, detail="mutation_id wajib.")
    _, rc = db.execute(
        "UPDATE bank_mutations SET match_status='Unmatched', transaction_id=NULL, "
        "matched_by=NULL, matched_at=NULL WHERE id=?",
        (mid,),
    )
    if rc == 0:
        raise HTTPException(status_code=404, detail="Mutasi tidak ditemukan.")
    log_action(user, "RECONCILE_UNMATCH", f"Mutation #{mid} dilepas.")
    notify("data_updated", "bank_mutations")
    return {"message": "Match dilepas."}


@router.post("/ignore")
async def reconcile_ignore(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Body: {mutation_id: int, note?: str}. Tandai mutasi diabaikan (biaya admin, bunga, dll)."""
    require_role(user, *_ROLES)
    mid = body.get("mutation_id")
    if not mid:
        raise HTTPException(status_code=400, detail="mutation_id wajib.")
    _, rc = db.execute(
        "UPDATE bank_mutations SET match_status='Ignored', matched_by=?, "
        "matched_at=CURRENT_TIMESTAMP WHERE id=?",
        (user["name"], mid),
    )
    if rc == 0:
        raise HTTPException(status_code=404, detail="Mutasi tidak ditemukan.")
    log_action(user, "RECONCILE_IGNORE", f"Mutation #{mid} diabaikan.")
    notify("data_updated", "bank_mutations")
    return {"message": "Mutasi ditandai Ignored."}
