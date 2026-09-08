"""
Router Jamaah Write (paling sensitif):
- PUT /api/jamaah/bulk-ops       (ops team edit multi-jamaah -- HARUS DIDAFTARKAN
                                  DULUAN sebelum /{jid} agar tidak di-shadow oleh
                                  cast int("bulk-ops"))
- PUT /api/jamaah/{jid}           (big update; gate DP+non-admin -> field terkunci)
- PUT /api/jamaah/{jid}/doc-completeness (paspor + dokumen submitted)
- POST /api/jamaah/{jid}/documents (upload PII base64 -> UPLOAD_DIR, kolom doc_<type>)
- PUT /api/jamaah/{jid}/payment   (PALING SENSITIF: recompute payment status +
                                   auto-create commission_claim saat BARU Lunas +
                                   INSERT transaction income + WA invoice)
- POST /api/jamaah/{jid}/check-visa (external Playwright visa_checker)
- PUT /api/jamaah/{jid}/ops       (single jamaah ops verb: visa+room+doc+bus)

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
"""
import asyncio
import base64
import json
import os

from fastapi import APIRouter

import db
import visa_checker
import whatsapp as wa
from deps import (
    Depends,
    HTTPException,
    UPLOAD_DIR,
    _field_change,
    authenticate_token,
    fire_and_forget,
    fmt_id,
    json_body,
    log_action,
    notify,
    parse_int,
    require_role,
    status_to_dims,
    sync_status_mirror,
)

router = APIRouter(tags=["jamaah-write"])


# ===========================================================================
# NB: /api/jamaah/bulk-ops HARUS didaftarkan SEBELUM /api/jamaah/{jid} literal
# endpoint -- FastAPI matching order. Sudah dijamin karena router di-include
# sekali & endpoint di-scan sesuai urutan definisi di file ini.
# ===========================================================================
@router.put("/api/jamaah/bulk-ops")
async def jamaah_bulk_ops(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    updates = body.get("updates")
    if not isinstance(updates, list) or len(updates) == 0:
        raise HTTPException(status_code=400, detail="Tidak ada data untuk diupdate")

    success = 0
    changed_count = 0
    for u in updates:
        try:
            row = db.query_one("SELECT * FROM jamaah WHERE id = ?", (u.get("id"),))
            if not row:
                continue
            db.execute(
                "UPDATE jamaah SET visa_status = ?, room_type = ?, room_number = ?, doc_status = ?, "
                "passport_expiry = ?, passport_location = ?, bus_group = ? WHERE id = ?",
                (u.get("visa_status"), u.get("room_type"), u.get("room_number"), u.get("doc_status"),
                 u.get("passport_expiry"), u.get("passport_location"), u.get("bus_group"), u.get("id")),
            )
            sync_status_mirror(u.get("id"))
            success += 1
            # Log per-jamaah (bukan satu baris ringkasan) supaya "Simpan Semua" tetap
            # bisa dilacak field apa yang berubah per orang -- sama presisinya dengan
            # simpan satu-satu. Baris yang tidak ada perubahan sama sekali dilewati.
            changes = list(filter(None, [
                _field_change("Visa Status", row["visa_status"], u.get("visa_status")),
                _field_change("Tipe Kamar", row["room_type"], u.get("room_type")),
                _field_change("No. Kamar", row["room_number"], u.get("room_number")),
                _field_change("Status Dokumen", row["doc_status"], u.get("doc_status")),
                _field_change("Masa Berlaku Paspor", row["passport_expiry"], u.get("passport_expiry")),
                _field_change("Lokasi Paspor", row["passport_location"], u.get("passport_location")),
                _field_change("Rombongan Bus", row["bus_group"], u.get("bus_group")),
            ]))
            if changes:
                changed_count += 1
                log_action(user, "UPDATE_OPS", f"Update Ops (Simpan Semua) {row['name']}: " + "; ".join(changes))
        except Exception:  # noqa: BLE001
            pass
    notify("data_updated", "jamaah")
    return {"message": f"Berhasil menyimpan data untuk {success} jamaah sekaligus ({changed_count} ada perubahan)."}


@router.put("/api/jamaah/{jid}")
async def jamaah_update(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    g = body.get
    row = db.query_one("SELECT * FROM jamaah WHERE id = ?", (jid,))
    if not row:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")

    # Phase 7a-1: tambahan field biodata/dokumen -- optional. Kalau body tidak
    # kirim key-nya, preserve nilai lama dari row (partial update-safe).
    # Modal Edit Jamaah tabbed (Phase 7a-2) akan kirim semua field sekaligus,
    # tapi caller lama (edit dari script/legacy) tidak break.
    def _pref(key, default_val):
        return body[key] if key in body else default_val
    doc_number = _pref("passport_number", row["passport_number"])
    doc_issued = _pref("passport_issued", row["passport_issued"])
    doc_expiry = _pref("passport_expiry", row["passport_expiry"])
    doc_location = _pref("passport_location", row["passport_location"])
    doc_issuer_city = _pref("passport_issuer_city", row["passport_issuer_city"])
    doc_room_num = _pref("room_number", row["room_number"])
    doc_bus_group = _pref("bus_group", row["bus_group"])
    doc_equipment = _pref("equipment_package", row["equipment_package"])
    # submitted_documents = JSON string di DB. Kalau body kirim list, jsonify;
    # kalau tidak, keep raw string dari row.
    if "submitted_documents" in body:
        val = body["submitted_documents"]
        doc_submitted = json.dumps(val or []) if isinstance(val, list) else val
    else:
        doc_submitted = row["submitted_documents"]

    final_package = g("package_type")
    final_price = g("total_price")

    # Gatekeeper: bila sudah ada DP & bukan admin -> harga/paket dikunci
    if row["paid_amount"] and row["paid_amount"] > 0 and user["role"] != "admin":
        changes = list(filter(None, [
            _field_change("NIK", row["nik"], g("nik")),
            _field_change("Nama", row["name"], g("name")),
            _field_change("No. WA", row["phone"], g("phone")),
        ]))
        db.execute(
            "UPDATE jamaah SET nik = ?, name = ?, phone = ?, health_history = ?, mahram = ?, "
            "orderer_name = ?, gender = ?, birth_place = ?, birth_date = ?, citizenship = ?, identity_type = ?, "
            "family_phone = ?, email = ?, father_name = ?, education = ?, job = ?, marital_status = ?, "
            "relation = ?, address = ?, province = ?, city = ?, subdistrict = ?, village = ?, "
            "passport_number = ?, passport_issued = ?, passport_expiry = ?, passport_location = ?, "
            "passport_issuer_city = ?, submitted_documents = ?, equipment_package = ?, "
            "room_number = ?, bus_group = ? WHERE id = ?",
            (
                g("nik"), g("name"), g("phone"), g("health_history"), g("mahram"),
                g("orderer_name"), g("gender"), g("birth_place"), g("birth_date"), g("citizenship"),
                g("identity_type"), g("family_phone"), g("email"), g("father_name"),
                g("education"), g("job"), g("marital_status"), g("relation"), g("address"),
                g("province"), g("city"), g("subdistrict"), g("village"),
                doc_number, doc_issued, doc_expiry, doc_location, doc_issuer_city,
                doc_submitted, doc_equipment, doc_room_num, doc_bus_group, jid,
            ),
        )
        log_action(
            user, "UPDATE_JAMAAH",
            f"Update profil {row['name']}: " + ("; ".join(changes) if changes else "tidak ada perubahan field utama"),
        )
        notify("data_updated", "jamaah")
        return {"message": "Profil diupdate. Paket/Harga dikunci karena sudah ada DP (Hubungi Finance)."}

    # Gatekeeper kuota bila paket berubah
    if row["package_type"] != final_package:
        pkg = db.query_one(
            "SELECT quota, (SELECT COUNT(*) FROM jamaah WHERE package_type = p.name "
            "AND status NOT IN ('Cancelled')) as filled FROM packages p WHERE p.name = ?",
            (final_package,),
        )
        if pkg and pkg["filled"] >= pkg["quota"] and user["role"] != "admin":
            raise HTTPException(
                status_code=400, detail="Update Ditolak! Stok kuota kursi untuk paket baru sudah penuh."
            )

    # Perubahan Total Harga wajib disertai keterangan (jejak audit kenapa harga berubah)
    old_price = row["total_price"] or 0
    new_price = int(final_price) if final_price not in (None, "") else 0
    price_change_note = (g("price_change_note") or "").strip()
    if new_price != old_price and not price_change_note:
        raise HTTPException(
            status_code=400, detail="Keterangan wajib diisi saat mengubah Total Harga."
        )

    # Phase 4c: BOQ re-snapshot kalau boq_id di body berubah dari row saat ini.
    # Jamaah tanpa BOQ (row.boq_id NULL) + edit dgn boq_id baru -> attach.
    # Jamaah dgn BOQ existing + edit dgn boq_id berbeda -> re-snapshot dari BOQ baru.
    # Body tanpa boq_id key -> keep row apa adanya (untuk edit yg tidak sentuh BOQ).
    boq_id_new = g("boq_id")
    boq_snapshot_price_new = row["boq_snapshot_price"]
    boq_snapshot_at_sql = "boq_snapshot_at"  # keep existing kalau tidak berubah
    boq_change_log = None

    if "boq_id" in body:  # body eksplisit mention boq_id
        try:
            boq_id_int = int(boq_id_new) if boq_id_new not in (None, "") else None
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="boq_id tidak valid.")

        if boq_id_int is not None:
            # Validate BOQ belongs to (new) package + is Approved.
            boq_row = db.query_one(
                "SELECT b.*, p.name AS pkg_name FROM package_boq b "
                "LEFT JOIN packages p ON p.id = b.package_id "
                "WHERE b.id = ? AND b.status = 'Approved'",
                (boq_id_int,),
            )
            if not boq_row:
                raise HTTPException(status_code=400, detail="BOQ tidak Approved atau tidak ditemukan.")
            if boq_row["pkg_name"] != final_package:
                raise HTTPException(status_code=400, detail="BOQ tidak milik paket ini.")
            # Re-snapshot price per room_type.
            from routes.boq import _compute_totals  # local import
            totals = _compute_totals(
                boq_id_int, boq_row["target_pax"], boq_row["target_margin_pct"],
                boq_row["extra_triple"], boq_row["extra_double"],
            )
            rt = g("room_type")
            if rt == "TRIPLE":
                boq_snapshot_price_new = int(totals["price_triple"] or 0)
            elif rt == "DOUBLE":
                boq_snapshot_price_new = int(totals["price_double"] or 0)
            else:
                boq_snapshot_price_new = int(totals["price_quad"] or 0)
            boq_snapshot_at_sql = "CURRENT_TIMESTAMP"
        else:
            # Clear BOQ (boq_id explicitly None).
            boq_snapshot_price_new = None
            boq_snapshot_at_sql = "NULL"

        if (row["boq_id"] or None) != (boq_id_int or None):
            boq_change_log = (
                f"BOQ diubah: #{row['boq_id'] or '-'} -> #{boq_id_int or '-'} "
                f"(snapshot Rp {fmt_id(boq_snapshot_price_new or 0)})"
            )
        boq_id_to_save = boq_id_int
    else:
        boq_id_to_save = row["boq_id"]

    db.execute(
        f"UPDATE jamaah SET nik = ?, name = ?, phone = ?, status = ?, health_history = ?, mahram = ?, "
        f"package_type = ?, total_price = ?, orderer_name = ?, gender = ?, birth_place = ?, birth_date = ?, "
        f"citizenship = ?, identity_type = ?, family_phone = ?, email = ?, father_name = ?, education = ?, "
        f"job = ?, marital_status = ?, relation = ?, address = ?, province = ?, city = ?, subdistrict = ?, "
        f"village = ?, room_type = ?, passport_number = ?, passport_issued = ?, passport_expiry = ?, "
        f"passport_location = ?, passport_issuer_city = ?, submitted_documents = ?, equipment_package = ?, "
        f"room_number = ?, bus_group = ?, "
        f"boq_id = ?, boq_snapshot_price = ?, boq_snapshot_at = {boq_snapshot_at_sql} "
        f"WHERE id = ?",
        (
            g("nik"), g("name"), g("phone"), g("status"), g("health_history"), g("mahram"),
            final_package, final_price, g("orderer_name"), g("gender"), g("birth_place"), g("birth_date"),
            g("citizenship"), g("identity_type"), g("family_phone"), g("email"), g("father_name"),
            g("education"), g("job"), g("marital_status"), g("relation"), g("address"),
            g("province"), g("city"), g("subdistrict"), g("village"), g("room_type"),
            doc_number, doc_issued, doc_expiry, doc_location, doc_issuer_city,
            doc_submitted, doc_equipment, doc_room_num, doc_bus_group,
            boq_id_to_save, boq_snapshot_price_new,
            jid,
        ),
    )
    # Selaraskan dimensi: payment selalu dari nominal asli; pipeline hanya digeser
    # bila jamaah masih di funnel awal (jangan mundurkan yang sudah Booked/On Trip).
    paid_now = row["paid_amount"] or 0
    total_now = new_price
    cand_pipeline, new_payment, _ = status_to_dims(g("status"), paid_now, total_now)
    cur = db.query_one("SELECT pipeline_stage FROM jamaah WHERE id = ?", (jid,))
    set_pipeline = (
        cand_pipeline
        if (cur and cur["pipeline_stage"] in ("Lead", "Waitlisted", "Registered"))
        else (cur["pipeline_stage"] if cur else cand_pipeline)
    )
    db.execute(
        "UPDATE jamaah SET pipeline_stage = ?, payment_status = ? WHERE id = ?",
        (set_pipeline, new_payment, jid),
    )
    sync_status_mirror(jid)

    changes = list(filter(None, [
        _field_change("NIK", row["nik"], g("nik")),
        _field_change("Nama", row["name"], g("name")),
        _field_change("No. WA", row["phone"], g("phone")),
        _field_change("Status", row["status"], g("status")),
        _field_change("Paket", row["package_type"], final_package),
        _field_change("Tipe Kamar", row["room_type"], g("room_type")),
    ]))
    if new_price != old_price:
        changes.append(f"Total Harga (Rp {fmt_id(old_price)} -> Rp {fmt_id(new_price)}) [Keterangan: {price_change_note}]")
    if boq_change_log:
        changes.append(boq_change_log)

    log_action(
        user, "UPDATE_JAMAAH",
        f"Update data jamaah {row['name']}: " + ("; ".join(changes) if changes else "tidak ada perubahan field utama"),
    )
    notify("data_updated", "jamaah")
    return {"message": "Data Jamaah berhasil diupdate."}


@router.put("/api/jamaah/{jid}/doc-completeness")
async def jamaah_doc_completeness(
    jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    require_role(user, "admin", "ops", "sales")
    g = body.get
    db.execute(
        "UPDATE jamaah SET passport_number = ?, passport_issued = ?, passport_expiry = ?, "
        "passport_issuer_city = ?, submitted_documents = ?, equipment_package = ?, room_type = ? WHERE id = ?",
        (
            g("passport_number"), g("passport_issued"), g("passport_expiry"),
            g("passport_issuer_city"), json.dumps(g("submitted_documents") or []),
            g("equipment_package"), g("room_type"), jid,
        ),
    )
    notify("data_updated", "jamaah")
    return {"message": "Kelengkapan dokumen jamaah berhasil disimpan."}


@router.post("/api/jamaah/{jid}/documents")
async def jamaah_documents(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    doc_type = body.get("docType")
    file_base64 = body.get("fileBase64", "")
    ext = (body.get("ext") or "").lower().lstrip(".")
    if doc_type not in ("ktp", "kk", "passport", "vaccine"):
        raise HTTPException(status_code=400, detail="Tipe dokumen tidak valid")
    # SECURITY: allowlist ext -- tanpa ini user bisa set ext="jpg/../../evil"
    # -> path traversal keluar UPLOAD_DIR saat os.path.join+open dieksekusi.
    if ext not in ("png", "jpg", "jpeg", "webp", "pdf"):
        raise HTTPException(status_code=400, detail="Format file harus png/jpg/webp/pdf.")

    file_name = f"doc_{doc_type}_{jid}_{int(asyncio.get_event_loop().time()*1000)}.{ext}"
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    b64 = file_base64.split(",")[1] if "," in file_base64 else file_base64
    with open(os.path.join(UPLOAD_DIR, file_name), "wb") as f:
        f.write(base64.b64decode(b64))

    db.execute(f"UPDATE jamaah SET doc_{doc_type} = ? WHERE id = ?", (f"/uploads/{file_name}", jid))
    log_action(user, "UPLOAD_DOCUMENT", f"Mengunggah dokumen {doc_type.upper()} untuk Jamaah ID {jid}")
    notify("data_updated", "jamaah")
    return {"message": f"Dokumen {doc_type.upper()} berhasil disimpan.", "path": f"/uploads/{file_name}"}


# ===========================================================================
# PEMBAYARAN (FINANCE) -- PALING SENSITIF: auto-create commission_claim saat
# baru menjadi Lunas + INSERT transactions income + WA invoice.
# ===========================================================================
@router.put("/api/jamaah/{jid}/payment")
async def jamaah_payment(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    if user["role"] not in ("admin", "finance"):
        raise HTTPException(status_code=403, detail="Hanya Divisi Keuangan yang dapat update pembayaran.")

    paid_amount = parse_int(body.get("paid_amount"), "nominal pembayaran")
    jamaah = db.query_one(
        "SELECT j.*, a.name as agent_name, p.default_commission_fee FROM jamaah j "
        "LEFT JOIN agents a ON j.agent_id = a.id "
        "LEFT JOIN packages p ON j.package_type = p.name WHERE j.id = ?",
        (jid,),
    )
    if not jamaah:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")

    new_paid = (jamaah["paid_amount"] or 0) + paid_amount
    total = jamaah["total_price"] or 0
    if total > 0 and new_paid >= total:
        new_payment = "Lunas"
    elif new_paid > 0:
        new_payment = "DP"
    else:
        new_payment = "Unpaid"
    # Membayar berarti minimal sudah Booked (kecuali sudah dibatalkan)
    new_pipeline = jamaah["pipeline_stage"]
    if new_paid > 0 and new_pipeline != "Cancelled":
        new_pipeline = "Booked"
    old_payment = jamaah["payment_status"]

    db.execute(
        "UPDATE jamaah SET paid_amount = ?, payment_status = ?, pipeline_stage = ? WHERE id = ?",
        (new_paid, new_payment, new_pipeline, jid),
    )
    new_status = sync_status_mirror(jid)
    db.execute(
        "INSERT INTO transactions (type, category, amount, description, reference_id, package_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("income", "payment", paid_amount, f"Pembayaran Umroh: {jamaah['name']}", jid, jamaah["package_type"]),
    )
    notify("data_updated", "transaction")

    # Klaim komisi otomatis dibuat (status Pending) saat BARU menjadi Lunas & ada agen --
    # TIDAK langsung cair. Menunggu Manajemen setuju -> Finance cairkan (lihat endpoint
    # /api/commission-claims/{id}/review & /disburse). Fee berbasis PAKET (setiap paket
    # punya default_commission_fee sendiri) -- cek dulu pengecualian khusus untuk
    # kombinasi (agen, paket) ini -> kalau tidak ada, jatuh balik ke fee default paket itu.
    if new_payment == "Lunas" and old_payment != "Lunas" and jamaah["agent_id"]:
        package_fee = db.query_one(
            "SELECT commission_fee FROM agent_package_fees WHERE agent_id = ? AND package_name = ?",
            (jamaah["agent_id"], jamaah["package_type"]),
        )
        fee = package_fee["commission_fee"] if package_fee else (jamaah["default_commission_fee"] or 0)
        db.execute(
            "INSERT INTO commission_claims (agent_id, jamaah_id, amount, requested_by) VALUES (?, ?, ?, ?)",
            (jamaah["agent_id"], jid, fee, "Sistem (Otomatis saat Lunas)"),
        )
        notify("data_updated", "commission_claim")

    sisa = jamaah["total_price"] - new_paid
    status_bayar = "LUNAS SEPENUHNYA" if new_payment == "Lunas" else f"BELUM LUNAS (Sisa: Rp {fmt_id(sisa)})"
    wa_msg = (
        f"\U0001f9fe *INVOICE PEMBAYARAN RESMI*\n\U0001f3e2 *UMAR TRAVEL*\n\n"
        f"Assalamu'alaikum Bpk/Ibu {jamaah['name']},\n\nKami telah menerima pembayaran Anda:\n\n"
        f"*Program/Paket:* {jamaah['package_type']}\n"
        f"*Nominal Masuk:* Rp {fmt_id(paid_amount)}\n"
        f"*Total Terbayar:* Rp {fmt_id(new_paid)}\n"
        f"*Status:* {status_bayar}\n\nJazakumullah Khairan atas kepercayaannya."
    )
    fire_and_forget(wa.send_message(jamaah["phone"], wa_msg))
    log_action(user, "PAYMENT", f"Menerima dana Rp {paid_amount} dari {jamaah['name']}")
    notify("data_updated", "jamaah")
    return {"message": "Pembayaran berhasil diupdate."}


# ===========================================================================
# AUTOMASI CEK VISA (OPS) -- external Playwright via visa_checker
# ===========================================================================
@router.post("/api/jamaah/{jid}/check-visa")
async def jamaah_check_visa(jid: int, user=Depends(authenticate_token)):
    if user["role"] not in ("admin", "ops"):
        raise HTTPException(status_code=403, detail="Hanya Divisi Operasional yang dapat memproses Visa.")

    jamaah = db.query_one("SELECT * FROM jamaah WHERE id = ?", (jid,))
    if not jamaah:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")
    # Gerbang berbasis dimensi pembayaran (bukan label gabungan) -> lebih akurat.
    if jamaah["payment_status"] != "Lunas" and jamaah["visa_status"] != "Proses Kedutaan":
        raise HTTPException(status_code=400, detail="Jamaah belum Lunas, visa belum bisa diproses.")

    visa_status = await visa_checker.check_visa_status(jamaah["nik"], jamaah["name"])
    # Hanya perbarui dimensi visa; pipeline & pembayaran TIDAK ikut tertimpa (bug lama).
    db.execute("UPDATE jamaah SET visa_status = ? WHERE id = ?", (visa_status, jid))
    sync_status_mirror(jid)
    log_action(user, "CHECK_VISA", f"Memproses cek visa Jamaah ID {jid}: {visa_status}")

    if visa_status == "Visa Approved":
        wa_msg = (
            f"Alhamdulillah Bpk/Ibu {jamaah['name']},\n\n"
            f"Visa Umroh Anda telah *DISETUJUI (APPROVED)*.\n"
            f"Silakan menunggu instruksi selanjutnya dari tim operasional Umar Travel "
            f"terkait jadwal keberangkatan."
        )
        fire_and_forget(wa.send_message(jamaah["phone"], wa_msg))

    notify("data_updated", "jamaah")
    return {"message": f"Status visa diperbarui menjadi: {visa_status}"}


# ===========================================================================
# LOGISTIK & OPERASIONAL (per-jamaah ops verb)
# ===========================================================================
@router.put("/api/jamaah/{jid}/ops")
async def jamaah_ops(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    g = body.get
    row = db.query_one("SELECT * FROM jamaah WHERE id = ?", (jid,))
    if not row:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")
    db.execute(
        "UPDATE jamaah SET visa_status = ?, room_type = ?, room_number = ?, doc_status = ?, "
        "passport_expiry = ?, passport_location = ?, bus_group = ? WHERE id = ?",
        (g("visa_status"), g("room_type"), g("room_number"), g("doc_status"),
         g("passport_expiry"), g("passport_location"), g("bus_group"), jid),
    )
    sync_status_mirror(jid)  # visa_status berubah -> cermin status ikut konsisten
    changes = list(filter(None, [
        _field_change("Visa Status", row["visa_status"], g("visa_status")),
        _field_change("Tipe Kamar", row["room_type"], g("room_type")),
        _field_change("No. Kamar", row["room_number"], g("room_number")),
        _field_change("Status Dokumen", row["doc_status"], g("doc_status")),
        _field_change("Masa Berlaku Paspor", row["passport_expiry"], g("passport_expiry")),
        _field_change("Lokasi Paspor", row["passport_location"], g("passport_location")),
        _field_change("Rombongan Bus", row["bus_group"], g("bus_group")),
    ]))
    detail = f"Update Ops {row['name']}: " + "; ".join(changes) if changes else f"Update Ops {row['name']} (tidak ada perubahan)"
    log_action(user, "UPDATE_OPS", detail)
    notify("data_updated", "jamaah")
    return {"message": "Data Operasional & Dokumen berhasil disimpan."}
