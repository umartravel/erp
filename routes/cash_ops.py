"""
Sprint AK-6: Operasi kas khusus -- Setor Tunai (2-leg) + Prive Pemilik.

Endpoints:
- POST /api/finance/setor-tunai                     (finance/admin) leg 1
- POST /api/finance/setor-tunai/{tid}/confirm       (finance/admin) leg 2
- GET  /api/finance/setor-tunai/pending             (fin/admin/mgmt)
- POST /api/finance/prive                           (admin/mgmt only)
- GET  /api/finance/prive/history                   (fin/admin/mgmt)

Setor Tunai 2-leg:
  Uang keluar dari Kas Kecil (1101) menuju Bank -- ada delay 1-3 hari.
  Selama gap itu, uang ada di 'Kas Kliring' (1109) supaya Neraca tidak salah.

  LEG 1 (saat setor teller/ATM):
    Dr 1109 Kas Kliring    Rp X
    Cr 1101 Kas Kecil      Rp X

  LEG 2 (saat rekening koran show incoming, confirm manual):
    Dr 1102 Bank           Rp X
    Cr 1109 Kas Kliring    Rp X

Prive Pemilik (Owner Draw): Dr 3102 / Cr Bank -- mengurangi Ekuitas.
"""
from fastapi import APIRouter

import db
import journal_engine as je
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    json_body,
    log_action,
    notify,
    parse_int,
    require_role,
)

router = APIRouter(tags=["cash-ops"])


@router.post("/api/finance/setor-tunai")
async def setor_tunai_create(body: dict = Depends(json_body),
                             user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")
    amount = parse_int(body.get("amount"), "nominal setor")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nominal harus > 0.")
    to_bank = (body.get("to_bank") or "1102").strip()
    if to_bank not in ("1102", "1103"):
        raise HTTPException(status_code=400,
                            detail="to_bank harus '1102' (Mandiri) atau '1103' (BSI).")
    note = (body.get("note") or "").strip()
    memo = f"Setor tunai Kas Kecil -> Bank {to_bank}" + (f": {note}" if note else "")

    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, status) "
        "VALUES ('cash_ops', 'setor_tunai', ?, ?, 'POSTED')",
        (amount, memo),
    )
    try:
        je.post_setor_tunai(tx_id, amount, memo)
    except Exception as e:  # noqa: BLE001
        db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        raise HTTPException(status_code=500, detail=f"Journal post gagal: {e}")

    log_action(user, "SETOR_TUNAI_LEG1",
               f"Setor tunai Rp {amount:,} ke Bank {to_bank}".replace(",", "."))
    notify("data_updated", "transaction")
    return {
        "message": "Setor tunai leg 1 tercatat. Menunggu konfirmasi bank.",
        "tx_id": tx_id,
        "amount": amount,
        "to_bank": to_bank,
    }


@router.post("/api/finance/setor-tunai/{leg1_tx_id}/confirm")
async def setor_tunai_confirm(leg1_tx_id: int, body: dict = Depends(json_body),
                              user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")

    leg1 = db.query_one(
        "SELECT id, amount, description, category, status "
        "FROM transactions WHERE id = ?", (leg1_tx_id,))
    if not leg1:
        raise HTTPException(status_code=404, detail="Tx leg 1 tidak ditemukan.")
    if leg1["category"] != "setor_tunai":
        raise HTTPException(status_code=400,
                            detail="Tx bukan setor_tunai (category mismatch).")

    already = db.query_one(
        "SELECT id FROM transactions "
        "WHERE category = 'setor_tunai_confirm' AND reference_id = ?",
        (leg1_tx_id,))
    if already:
        raise HTTPException(status_code=400,
                            detail=f"Setor #{leg1_tx_id} sudah dikonfirmasi (leg 2 tx #{already['id']}).")

    to_bank = (body.get("to_bank") or "1102").strip()
    if to_bank not in ("1102", "1103"):
        raise HTTPException(status_code=400,
                            detail="to_bank harus '1102' atau '1103'.")
    memo = f"[Konfirmasi bank] {leg1['description']}"

    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, "
        "reference_id, status) "
        "VALUES ('cash_ops', 'setor_tunai_confirm', ?, ?, ?, 'POSTED')",
        (leg1["amount"], memo, leg1_tx_id),
    )
    try:
        je.post_journal(tx_id, [
            (je.coa_id(to_bank), leg1["amount"], 0, memo),
            (je.coa_id(je.COA_KAS_KLIRING), 0, leg1["amount"], memo),
        ])
    except Exception as e:  # noqa: BLE001
        db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        raise HTTPException(status_code=500, detail=f"Journal post gagal: {e}")

    log_action(user, "SETOR_TUNAI_LEG2",
               f"Konfirmasi setor #{leg1_tx_id} Rp {leg1['amount']:,} "
               f"masuk Bank {to_bank}".replace(",", "."))
    notify("data_updated", "transaction")
    return {
        "message": "Setor tunai berhasil dikonfirmasi masuk bank.",
        "leg1_tx_id": leg1_tx_id,
        "leg2_tx_id": tx_id,
        "amount": leg1["amount"],
        "to_bank": to_bank,
    }


@router.get("/api/finance/setor-tunai/pending")
async def setor_tunai_pending(user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    rows = db.query_all(
        "SELECT t1.id, t1.amount, t1.description, t1.created_at "
        "FROM transactions t1 "
        "LEFT JOIN transactions t2 "
        "  ON t2.reference_id = t1.id AND t2.category = 'setor_tunai_confirm' "
        "WHERE t1.category = 'setor_tunai' AND t2.id IS NULL "
        "  AND t1.status = 'POSTED' "
        "ORDER BY t1.created_at DESC LIMIT 50",
    )
    total = sum(int(r["amount"] or 0) for r in rows)
    return {"pending": rows, "count": len(rows), "total": total}


@router.post("/api/finance/prive")
async def prive_create(body: dict = Depends(json_body),
                       user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    amount = parse_int(body.get("amount"), "nominal prive")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nominal harus > 0.")
    from_bank = (body.get("from_bank") or "1102").strip()
    if from_bank not in ("1101", "1102", "1103"):
        raise HTTPException(status_code=400,
                            detail="from_bank harus '1101', '1102', atau '1103'.")
    note = (body.get("note") or "").strip()
    if not note:
        raise HTTPException(status_code=400, detail="Catatan/keterangan prive wajib diisi.")
    memo = f"Prive Pemilik: {note}"

    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, status) "
        "VALUES ('cash_ops', 'prive', ?, ?, 'POSTED')",
        (amount, memo),
    )
    try:
        je.post_prive(tx_id, amount, memo, cash_account_code=from_bank)
    except Exception as e:  # noqa: BLE001
        db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        raise HTTPException(status_code=500, detail=f"Journal post gagal: {e}")

    log_action(user, "PRIVE",
               f"Prive Rp {amount:,} dari {from_bank}: {note}".replace(",", "."))
    notify("data_updated", "transaction")
    return {
        "message": "Prive pemilik tercatat di ekuitas.",
        "tx_id": tx_id,
        "amount": amount,
        "from_bank": from_bank,
    }


@router.get("/api/finance/prive/history")
async def prive_history(limit: int = 12, user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    if limit < 1 or limit > 100:
        limit = 12
    rows = db.query_all(
        "SELECT id, amount, description, created_at "
        "FROM transactions "
        "WHERE category = 'prive' AND status = 'POSTED' "
        "ORDER BY created_at DESC LIMIT ?", (limit,),
    )
    total = sum(int(r["amount"] or 0) for r in rows)
    return {"prive": rows, "count": len(rows), "total": total}
