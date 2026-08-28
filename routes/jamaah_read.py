"""
Router Jamaah Read/Create:
- GET  /api/jamaah  : list role-scoped (sales cuma miliknya) + received_items agregat.
- POST /api/jamaah  : create baru; satu-satunya jalur pembuatan jamaah dari
  form manual & Terima pendaftaran-publik (gatekeeper: harga sesuai room_type,
  kuota tidak melebihi, NIK unique). Kalau dari public: link balik + WA welcome.

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
"""
from fastapi import APIRouter

import db
import whatsapp as wa
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    fire_and_forget,
    fmt_id,
    json_body,
    log_action,
    notify,
    require_role,
    status_to_dims,
    sync_status_mirror,
)

router = APIRouter(tags=["jamaah-read"])


@router.get("/api/jamaah")
async def jamaah_list(user=Depends(authenticate_token)):
    query = (
        "SELECT j.*, (SELECT GROUP_CONCAT(item_name, ', ') FROM jamaah_inventory "
        "WHERE jamaah_id = j.id) as received_items, u.name as sales_name "
        "FROM jamaah j LEFT JOIN users u ON j.sales_id = u.id "
    )
    params = []
    if user["role"] == "sales":  # RBAC: sales hanya lihat miliknya
        query += " WHERE j.sales_id = ? "
        params.append(user["id"])
    query += " ORDER BY j.created_at DESC"
    return db.query_all(query, tuple(params))


@router.post("/api/jamaah")
async def jamaah_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")

    package_type = body.get("package_type")
    total_price = body.get("total_price")
    room_type = body.get("room_type")

    row = db.query_one(
        "SELECT quota, price, price_quad, price_triple, price_double, "
        "(SELECT COUNT(*) FROM jamaah WHERE package_type = p.name AND status NOT IN ('Cancelled')) as filled "
        "FROM packages p WHERE p.name = ?",
        (package_type,),
    )
    if not row:
        raise HTTPException(status_code=404, detail="Paket yang dipilih tidak ditemukan.")

    # Gatekeeper harga berdasarkan tipe kamar (validasi sisi server)
    server_price = row["price"] or 0
    if room_type == "QUAD" and row["price_quad"]:
        server_price = row["price_quad"]
    elif room_type == "TRIPLE" and row["price_triple"]:
        server_price = row["price_triple"]
    elif room_type == "DOUBLE" and row["price_double"]:
        server_price = row["price_double"]

    if server_price != int(total_price):
        raise HTTPException(
            status_code=400,
            detail=f"Harga tidak cocok! Harga {room_type or 'paket'} untuk paket ini "
            f"seharusnya Rp {fmt_id(server_price or 0)}",
        )

    if row["filled"] >= row["quota"] and user["role"] != "admin":
        raise HTTPException(
            status_code=400,
            detail="GERBANG 2: Pendaftaran Ditolak! Stok kuota kursi untuk paket ini sudah penuh.",
        )

    g = body.get

    # CS penanggung jawab: default ke CS yang mengirim form ini, KECUALI kalau jamaah
    # ini berasal dari pendaftaran publik dan calon jamaah sudah memilih CS tujuan sendiri
    # saat daftar -- preferensi itu yang menang (lihat /api/public/pendaftaran).
    sales_id = user["id"]
    source_submission_id_for_cs = g("source_submission_id")
    if source_submission_id_for_cs:
        sub_cs = db.query_one(
            "SELECT preferred_cs_id FROM pendaftaran_publik WHERE id = ?", (source_submission_id_for_cs,)
        )
        if sub_cs and sub_cs["preferred_cs_id"]:
            sales_id = sub_cs["preferred_cs_id"]

    try:
        last_id, _ = db.execute(
            "INSERT INTO jamaah ("
            "nik, name, phone, package_type, status, total_price, notes, health_history, mahram, "
            "agent_id, city, sales_id, orderer_name, gender, birth_place, birth_date, citizenship, "
            "identity_type, family_phone, email, father_name, education, job, marital_status, "
            "relation, address, province, subdistrict, village, room_type) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                g("nik"), g("name"), g("phone"), package_type, g("status") or "Waitlisted",
                server_price, g("notes"), g("health_history"), g("mahram"), g("agent_id") or None,
                g("city"), sales_id, g("orderer_name"), g("gender"), g("birth_place"), g("birth_date"),
                g("citizenship"), g("identity_type"), g("family_phone"), g("email"),
                g("father_name"), g("education"), g("job"), g("marital_status"), g("relation"),
                g("address"), g("province"), g("subdistrict"), g("village"), room_type,
            ),
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(
                status_code=400, detail="Pendaftaran Gagal: NIK Jamaah ini sudah pernah terdaftar."
            )
        raise HTTPException(status_code=500, detail=str(e))

    if g("lead_source"):
        db.execute("UPDATE jamaah SET lead_source = ? WHERE id = ?", (g("lead_source"), last_id))

    # Inisialisasi dimensi status dari status awal yang dipilih, lalu sinkronkan cermin.
    pipeline, payment, trip = status_to_dims(g("status") or "Waitlisted", 0, server_price)
    db.execute(
        "UPDATE jamaah SET pipeline_stage = ?, payment_status = ?, trip_status = ? WHERE id = ?",
        (pipeline, payment, trip, last_id),
    )
    sync_status_mirror(last_id)

    log_action(user, "CREATE_JAMAAH", f"Mendaftar jamaah baru: {g('name')} (NIK: {g('nik')})")

    # Bila jamaah ini berasal dari form pendaftaran publik (lihat /api/public/pendaftaran),
    # tautkan balik & tandai submission itu sebagai diterima -- reuse endpoint ini sebagai
    # satu-satunya jalur pembuatan jamaah, gatekeeper harga/kuota otomatis ikut berlaku.
    source_submission_id = g("source_submission_id")
    if source_submission_id:
        sub = db.query_one(
            "SELECT status FROM pendaftaran_publik WHERE id = ?", (source_submission_id,)
        )
        if sub and sub["status"] == "Pending":
            db.execute(
                "UPDATE pendaftaran_publik SET status = 'Diterima', jamaah_id = ?, "
                "reviewed_by = ?, reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
                (last_id, user["name"], source_submission_id),
            )
            notify("data_updated", "pendaftaran_publik")

    wa_msg = (
        f"Assalamu'alaikum Bpk/Ibu {g('name')}.\n\n"
        f"Terima kasih telah mendaftar program umroh paket {package_type} di *Umar Travel*. "
        f"Data Anda telah kami terima.\n\nMohon segera melakukan proses pembayaran untuk "
        f"mengamankan kursi Anda. Jazakumullah khairan."
    )
    fire_and_forget(wa.send_message(g("phone"), wa_msg))
    notify("data_updated", "jamaah")
    return {"id": last_id, "message": "Data Jamaah berhasil disimpan & WA dikirim."}
