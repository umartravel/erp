"""
Router Keagenan: mitra/agen + fee komisi khusus per paket + klaim komisi
(lifecycle Pending -> Disetujui/Ditolak (Manajemen) -> Dicairkan (Finance)).

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
"""
from fastapi import APIRouter

import db
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

router = APIRouter(tags=["agents"])


@router.get("/api/agents")
async def agents_list(scope: str | None = None, user=Depends(authenticate_token)):
    """List agen. Sales role default hanya lihat agen yang mereka handle (handler_cs_id
    = user.id). Toggle ?scope=all untuk lihat semua. Role lain (admin/mgmt/finance)
    default semua."""
    base = ("SELECT a.*, u.name as handler_cs_name FROM agents a "
            "LEFT JOIN users u ON a.handler_cs_id = u.id ")
    if user.get("role") == "sales" and scope != "all":
        return db.query_all(base + "WHERE a.handler_cs_id = ? ORDER BY a.name ASC", (user["id"],))
    return db.query_all(base + "ORDER BY a.name ASC", ())


@router.get("/api/agents/performance")
async def agents_performance(user=Depends(authenticate_token)):
    """Leaderboard mitra/agen berdasarkan jumlah jamaah yang berhasil dibawa
    (di luar Lead & Cancelled, konsisten dengan definisi 'top agent' di dashboard)."""
    board = db.query_all(
        "SELECT a.id, a.name, a.phone, "
        "COUNT(CASE WHEN j.status NOT IN ('Cancelled', 'Lead - Follow Up') THEN j.id END) as total_jamaah "
        "FROM agents a LEFT JOIN jamaah j ON a.id = j.agent_id "
        "GROUP BY a.id ORDER BY total_jamaah DESC, a.name ASC",
        (),
    )
    return {"leaderboard": board}


@router.post("/api/agents")
async def agents_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    g = body.get

    # CS penanggung jawab rekrut -- default ke yang klik Terima, kecuali calon agen
    # sudah pilih preferensi CS lewat form publik (sama pola dengan jamaah.sales_id).
    handler_cs_id = user["id"]
    source_submission_id = g("source_submission_id")
    if source_submission_id:
        sub_cs = db.query_one(
            "SELECT preferred_cs_id FROM pendaftaran_agen_publik WHERE id = ?", (source_submission_id,)
        )
        if sub_cs and sub_cs["preferred_cs_id"]:
            handler_cs_id = sub_cs["preferred_cs_id"]

    last_id, _ = db.execute(
        "INSERT INTO agents (name, phone, province, city, subdistrict, village, address, "
        "instagram, email, agreement_number, kit_banner_wide, kit_x_banner, kit_kartu_nama, "
        "kit_id_card, handler_cs_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            g("name"), g("phone"), g("province"), g("city"), g("subdistrict"), g("village"),
            g("address"), g("instagram"), g("email"), g("agreement_number"),
            1 if g("kit_banner_wide") else 0, 1 if g("kit_x_banner") else 0,
            1 if g("kit_kartu_nama") else 0, 1 if g("kit_id_card") else 0, handler_cs_id,
        ),
    )
    log_action(user, "CREATE_AGENT", f"Menambah mitra/agen baru: {g('name')}")

    # Bila agen ini berasal dari form pendaftaran publik (lihat /api/public/agent-registration),
    # tautkan balik & tandai submission itu sebagai diterima -- reuse endpoint ini sebagai
    # satu-satunya jalur pembuatan agen, sama seperti pola pendaftaran jamaah.
    if source_submission_id:
        sub = db.query_one(
            "SELECT status FROM pendaftaran_agen_publik WHERE id = ?", (source_submission_id,)
        )
        if sub and sub["status"] == "Pending":
            db.execute(
                "UPDATE pendaftaran_agen_publik SET status = 'Diterima', agent_id = ?, "
                "reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (last_id, user["name"], source_submission_id),
            )
            notify("data_updated", "pendaftaran_agen_publik")

    notify("data_updated", "agent")
    return {"message": "Mitra/Agen berhasil ditambahkan."}


@router.put("/api/agents/{aid}")
async def agents_update(aid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    g = body.get
    agent = db.query_one("SELECT * FROM agents WHERE id = ?", (aid,))
    if not agent:
        raise HTTPException(status_code=404, detail="Mitra/Agen tidak ditemukan")
    db.execute(
        "UPDATE agents SET name = ?, phone = ?, province = ?, city = ?, subdistrict = ?, "
        "village = ?, address = ?, instagram = ?, email = ?, agreement_number = ?, "
        "kit_banner_wide = ?, kit_x_banner = ?, kit_kartu_nama = ?, kit_id_card = ? WHERE id = ?",
        (
            g("name"), g("phone"), g("province"), g("city"), g("subdistrict"), g("village"),
            g("address"), g("instagram"), g("email"), g("agreement_number"),
            1 if g("kit_banner_wide") else 0, 1 if g("kit_x_banner") else 0,
            1 if g("kit_kartu_nama") else 0, 1 if g("kit_id_card") else 0, aid,
        ),
    )
    log_action(user, "UPDATE_AGENT", f"Mengubah data mitra/agen: {agent['name']} -> {g('name')}")
    notify("data_updated", "agent")
    return {"message": "Data mitra/agen berhasil diperbarui."}


# Phase 7b-1: Transfer 1 agen dari CS lama ke CS baru. Dibuat terpisah dari
# agents_update() sengaja -- reassignment adalah operasi admin (bukan sales),
# butuh audit trail tersendiri (log-nya menampilkan from->to CS), dan
# frontend butuh endpoint yg simple (cukup kirim 1 field). Guard `admin only`
# per keputusan di project_edit_jamaah_agent_transfer_plan.md.
@router.put("/api/agents/{aid}/handler")
async def agents_transfer_handler(
    aid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    require_role(user, "admin")
    new_cs_id = parse_int(body.get("handler_cs_id"), "handler CS")
    note = (body.get("note") or "").strip()

    agent = db.query_one("SELECT * FROM agents WHERE id = ?", (aid,))
    if not agent:
        raise HTTPException(status_code=404, detail="Mitra/Agen tidak ditemukan")

    target = db.query_one("SELECT id, name, role FROM users WHERE id = ?", (new_cs_id,))
    if not target:
        raise HTTPException(status_code=404, detail="User CS tujuan tidak ditemukan")
    if target["role"] != "sales":
        raise HTTPException(
            status_code=400,
            detail=f"User tujuan ('{target['name']}') role-nya '{target['role']}', bukan CS/sales.",
        )
    if agent["handler_cs_id"] == new_cs_id:
        raise HTTPException(
            status_code=400, detail="Agen ini sudah dihandle CS tersebut."
        )

    old_cs = None
    if agent["handler_cs_id"]:
        old_cs = db.query_one("SELECT name FROM users WHERE id = ?", (agent["handler_cs_id"],))
    from_label = old_cs["name"] if old_cs else "(tanpa CS)"

    db.execute("UPDATE agents SET handler_cs_id = ? WHERE id = ?", (new_cs_id, aid))
    detail = f"Agen '{agent['name']}' dipindah dari {from_label} ke {target['name']}"
    if note:
        detail += f" -- catatan: {note}"
    log_action(user, "TRANSFER_AGENT", detail)
    notify("data_updated", "agent")
    return {
        "message": f"Agen '{agent['name']}' berhasil dipindah ke {target['name']}.",
        "agent_id": aid,
        "from_cs_id": agent["handler_cs_id"],
        "to_cs_id": new_cs_id,
    }


# Pengecualian fee komisi per (agen, paket) -- opsional. Kalau tidak ada baris yang
# cocok untuk kombinasi agen+paket tertentu, dipakai fee default milik paket itu sendiri
# (packages.default_commission_fee).
@router.get("/api/agents/{aid}/package-fees")
async def agent_package_fees_list(aid: int, user=Depends(authenticate_token)):
    return db.query_all(
        "SELECT * FROM agent_package_fees WHERE agent_id = ? ORDER BY package_name ASC", (aid,)
    )


@router.post("/api/agents/{aid}/package-fees")
async def agent_package_fee_upsert(
    aid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    require_role(user, "admin", "sales")
    package_name = (body.get("package_name") or "").strip()
    if not package_name:
        raise HTTPException(status_code=400, detail="Paket wajib dipilih.")
    fee = parse_int(body.get("commission_fee"), "fee komisi")

    existing = db.query_one(
        "SELECT id FROM agent_package_fees WHERE agent_id = ? AND package_name = ?",
        (aid, package_name),
    )
    if existing:
        db.execute(
            "UPDATE agent_package_fees SET commission_fee = ? WHERE id = ?",
            (fee, existing["id"]),
        )
    else:
        db.execute(
            "INSERT INTO agent_package_fees (agent_id, package_name, commission_fee) VALUES (?, ?, ?)",
            (aid, package_name, fee),
        )
    log_action(user, "UPDATE_AGENT_PACKAGE_FEE", f"Set fee komisi agen ID {aid} untuk paket {package_name}: Rp {fee}")
    notify("data_updated", "agent")
    return {"message": "Fee komisi khusus paket berhasil disimpan."}


@router.delete("/api/agents/{aid}/package-fees/{fee_id}")
async def agent_package_fee_delete(aid: int, fee_id: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    db.execute("DELETE FROM agent_package_fees WHERE id = ? AND agent_id = ?", (fee_id, aid))
    log_action(user, "DELETE_AGENT_PACKAGE_FEE", f"Menghapus pengecualian fee komisi (agen ID {aid})")
    notify("data_updated", "agent")
    return {"message": "Pengecualian fee komisi berhasil dihapus."}


# ===========================================================================
# KLAIM KOMISI AGEN: dibuat otomatis (Pending) saat jamaah Lunas -> Manajemen
# setuju/tolak -> Finance cairkan. Pola sama persis dengan Refund.
# ===========================================================================
@router.get("/api/commission-claims")
async def commission_claims_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "management", "finance")
    return db.query_all(
        "SELECT c.*, a.name as agent_name, j.name as jamaah_name, j.package_type "
        "FROM commission_claims c "
        "LEFT JOIN agents a ON c.agent_id = a.id "
        "LEFT JOIN jamaah j ON c.jamaah_id = j.id "
        "ORDER BY c.requested_at DESC",
        (),
    )


@router.put("/api/commission-claims/{cid}/review")
async def commission_claim_review(
    cid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    require_role(user, "admin", "management")
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Aksi harus 'approve' atau 'reject'.")

    c = db.query_one("SELECT * FROM commission_claims WHERE id = ?", (cid,))
    if not c:
        raise HTTPException(status_code=404, detail="Klaim komisi tidak ditemukan")
    if c["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Klaim ini berstatus '{c['status']}', tidak bisa direview ulang.")

    note = (body.get("note") or "").strip()
    if action == "reject" and not note:
        raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")

    new_status = "Disetujui" if action == "approve" else "Ditolak"
    db.execute(
        "UPDATE commission_claims SET status = ?, approved_by = ?, approved_at = CURRENT_TIMESTAMP, "
        "reject_reason = ? WHERE id = ?",
        (new_status, user["name"], note if action == "reject" else None, cid),
    )
    log_action(
        user, "APPROVE_COMMISSION_CLAIM" if action == "approve" else "REJECT_COMMISSION_CLAIM",
        f"{new_status} klaim komisi #{cid} (Rp {c['amount']})",
    )
    notify("data_updated", "commission_claim")
    return {"message": f"Klaim komisi berhasil di-{'setujui' if action == 'approve' else 'tolak'}."}


@router.put("/api/commission-claims/{cid}/disburse")
async def commission_claim_disburse(cid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")

    c = db.query_one("SELECT * FROM commission_claims WHERE id = ?", (cid,))
    if not c:
        raise HTTPException(status_code=404, detail="Klaim komisi tidak ditemukan")
    if c["status"] != "Disetujui":
        raise HTTPException(
            status_code=400,
            detail="Hanya klaim berstatus 'Disetujui' (oleh manajemen) yang bisa dicairkan.",
        )

    agent = db.query_one("SELECT * FROM agents WHERE id = ?", (c["agent_id"],))
    jamaah = db.query_one("SELECT name, package_type FROM jamaah WHERE id = ?", (c["jamaah_id"],))

    last_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, reference_id, package_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            "expense", "commission", c["amount"],
            f"Pencairan Komisi: {agent['name'] if agent else '-'} (Jamaah: {jamaah['name'] if jamaah else '-'})",
            c["agent_id"], jamaah["package_type"] if jamaah else None,
        ),
    )
    db.execute(
        "UPDATE commission_claims SET status = 'Dicairkan', disbursed_by = ?, "
        "disbursed_at = CURRENT_TIMESTAMP, transaction_id = ? WHERE id = ?",
        (user["name"], last_id, cid),
    )
    log_action(user, "DISBURSE_COMMISSION_CLAIM", f"Mencairkan komisi Rp {c['amount']} untuk agen ID {c['agent_id']}")
    notify("data_updated", "commission_claim")
    notify("data_updated", "transaction")
    return {"message": "Komisi berhasil dicairkan dan tercatat di Buku Kas."}


@router.get("/api/agents/{aid}/jamaah")
async def agent_jamaah_detail(
    aid: int, month: int = None, year: int = None, user=Depends(authenticate_token)
):
    """Drill-down untuk Dashboard Keagenan: seluruh jamaah rujukan satu agen,
    lengkap status pembayaran & status klaim komisinya. Filter bulan/tahun
    opsional berdasarkan tanggal daftar jamaah (registrasi/referral)."""
    query = (
        "SELECT j.id as jamaah_id, j.name as jamaah_name, j.package_type, j.total_price, "
        "j.paid_amount, j.status, j.created_at, "
        "c.id as claim_id, c.status as claim_status, c.amount as claim_amount "
        "FROM jamaah j LEFT JOIN commission_claims c ON c.jamaah_id = j.id "
        "WHERE j.agent_id = ?"
    )
    params = [aid]
    if month and year:
        query += " AND strftime('%Y-%m', j.created_at) = ?"
        params.append(f"{int(year):04d}-{int(month):02d}")
    elif year:
        query += " AND strftime('%Y', j.created_at) = ?"
        params.append(f"{int(year):04d}")
    query += " ORDER BY j.created_at DESC"
    return db.query_all(query, tuple(params))


@router.get("/api/agents/expand-report")
async def agents_expand_report(month: int = None, year: int = None, user=Depends(authenticate_token)):
    """Ranking lintas-agen untuk periode tertentu (jumlah closing & total komisi) --
    dipakai Manajemen untuk melihat agen paling produktif per bulan/tahun. 'Closing'
    dihitung dari klaim komisi yang terbentuk (selalu dibuat otomatis saat jamaah Lunas)."""
    # Filter periode HARUS masuk ke klausa ON (bukan WHERE) -- kalau di WHERE, agen yang
    # tidak punya klaim di periode itu akan hilang total dari hasil (bukan tampil 0),
    # karena LEFT JOIN yang tidak match menghasilkan NULL yang gagal lolos WHERE.
    join_extra = ""
    params = []
    if month and year:
        join_extra = " AND strftime('%Y-%m', c.requested_at) = ?"
        params.append(f"{int(year):04d}-{int(month):02d}")
    elif year:
        join_extra = " AND strftime('%Y', c.requested_at) = ?"
        params.append(f"{int(year):04d}")
    query = (
        "SELECT a.id, a.name, a.phone, "
        # total_closing dihitung dari SEMUA klaim (closing tetap terjadi walau klaimnya
        # nanti ditolak -- itu soal komisi, bukan soal penjualannya batal). total_komisi
        # SENGAJA mengecualikan klaim Ditolak, supaya tidak menyesatkan Manajemen dengan
        # nominal yang sudah tidak berlaku.
        "COUNT(c.id) as total_closing, "
        "COALESCE(SUM(CASE WHEN c.status != 'Ditolak' THEN c.amount ELSE 0 END), 0) as total_komisi "
        "FROM agents a LEFT JOIN commission_claims c ON c.agent_id = a.id" + join_extra +
        " GROUP BY a.id ORDER BY total_closing DESC, a.name ASC"
    )
    return db.query_all(query, tuple(params))
