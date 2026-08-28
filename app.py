"""
Main Server Umar CRM (versi Python / FastAPI).
Hasil migrasi menyeluruh dari server.js (Express + Socket.io).

Menjalankan:
    pip install -r requirements.txt
    python -m playwright install chromium      # untuk cek visa (opsional)
    python app.py                              # atau: uvicorn app:asgi --host 0.0.0.0 --port 3000

Frontend (public/index.html) TIDAK berubah, kecuali sumber socket.io client
diarahkan ke CDN (python-socketio tidak melayani /socket.io/socket.io.js).
"""
import asyncio
import base64
import datetime
import json
import os
import sys
import time
import uuid
from contextlib import asynccontextmanager

# Konsol Windows (cp1252) bisa gagal mencetak emoji (🚀, ✅). Paksa UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from fastapi import Body, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
import socketio
import uvicorn

import db
import realtime
import visa_checker
import whatsapp as wa
from auth import authenticate_file_token, authenticate_token, create_token, verify_password
from deps import (
    _derive_status,
    _field_change,
    assert_jamaah_access,
    fmt_id,
    parse_int,
    status_to_dims,
    sync_status_mirror,
)
from expense_pdf import build_expense_pdf
from jamaah_docs_pdf import auto_group_rooms, build_absensi_pdf, build_manifest_pdf, build_roomlist_pdf
from mgmt_pdf import build_monthly_report_pdf
from realtime import notify, sio
from reconcile_importer import parse_csv as reconcile_parse_csv, row_hash as reconcile_row_hash

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PUBLIC_DIR = os.path.join(BASE_DIR, "public")
# Dokumen PII (KTP/KK/Paspor/vaksin) & media WA disimpan di folder PRIVAT —
# di luar direktori statis publik — dan hanya dilayani lewat route ber-token.
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads_private")
# Logo BUKAN PII & harus tampil di layar login (pra-autentikasi), jadi disimpan
# di direktori publik.
BRANDING_DIR = os.path.join(PUBLIC_DIR, "branding")
PORT = int(os.environ.get("PORT", 3000))

@asynccontextmanager
async def lifespan(_app: FastAPI):
    # Startup: init DB, simpan loop event (untuk emit dari thread), auto-connect WA
    db.init_db()
    realtime.set_loop(asyncio.get_running_loop())
    wa.connect_to_whatsapp()
    print(f"🚀 Server Backend CRM Umar berjalan di port {PORT} (Python/FastAPI - Local & VPS)")
    yield


app = FastAPI(title="Umar CRM API", lifespan=lifespan)


# --- Body parser longgar (meniru req.body Express) ---
async def json_body(request: Request) -> dict:
    try:
        data = await request.json()
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


# --- Konversi error FastAPI -> {"error": ...} agar kompatibel frontend ---
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": "Permintaan tidak valid"})


# --- Helper umum ---
def get_setting(key, default=None):
    row = db.query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return row["value"] if row and row["value"] is not None else default




def log_action(user, action, details):
    db.execute(
        "INSERT INTO audit_logs (user_id, user_name, role, action, details) VALUES (?, ?, ?, ?, ?)",
        (user["id"], user["name"], user["role"], action, details),
    )
    notify("data_updated", "audit")


def require_role(user, *roles):
    if user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Akses Ditolak")




def fire_and_forget(coro):
    """Kirim WA tanpa menunggu (mirip pemanggilan wa.sendMessage tanpa await)."""
    asyncio.create_task(coro)


# ===========================================================================
# 1. LOGIN
# ===========================================================================
@app.post("/api/login")
async def login(body: dict = Depends(json_body)):
    username = body.get("username")
    password = body.get("password")
    user = db.query_one("SELECT * FROM users WHERE username = ?", (username,))
    if not user:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    if not verify_password(password or "", user["password"]):
        raise HTTPException(status_code=401, detail="Password salah")
    # Catat login sukses -- dipakai admin untuk audit user aktif.
    db.execute("UPDATE users SET last_login_at = CURRENT_TIMESTAMP WHERE id = ?", (user["id"],))
    token = create_token(user["id"], user["role"], user["name"])
    return {"token": token, "user": {"id": user["id"], "name": user["name"], "role": user["role"], "photo_url": user["photo_url"]}}





# ===========================================================================
# DASHBOARD & LAPORAN
# ===========================================================================
def _total_piutang():
    """Total tagihan jamaah yang belum lunas (kecuali Cancelled/Lead). Satu source
    of truth -- dipakai dashboard.finance.piutang + tactical.piutang, jangan copas
    query yang sama di banyak tempat (sering out-of-sync antara halaman)."""
    row = db.query_one(
        "SELECT SUM(total_price - paid_amount) as s FROM jamaah "
        "WHERE status NOT IN ('Lead - Follow Up', 'Cancelled') "
        "AND total_price > paid_amount"
    )
    return (row["s"] or 0) if row else 0


@app.get("/api/dashboard/super")
async def dashboard_super(user=Depends(authenticate_token)):
    funnel = db.query_all("SELECT status, COUNT(*) as count FROM jamaah GROUP BY status")
    visa = db.query_all(
        "SELECT visa_status, COUNT(*) as count FROM jamaah "
        "WHERE status NOT IN ('Lead - Follow Up', 'Cancelled') GROUP BY visa_status"
    )
    low_stock = db.query_all(
        "SELECT item_name, stock FROM inventory WHERE stock <= 20 ORDER BY stock ASC"
    )
    unassigned = db.query_all(
        "SELECT name, departure_date FROM packages "
        "WHERE tour_leader IS NULL OR trim(tour_leader) = '' OR mutawwif IS NULL "
        "OR trim(mutawwif) = '' ORDER BY departure_date DESC"
    )
    inc = db.query_one("SELECT SUM(amount) as s FROM transactions WHERE type = 'income'")
    exp = db.query_one("SELECT SUM(amount) as s FROM transactions WHERE type = 'expense'")
    piutang = _total_piutang()
    recent_exp = db.query_all(
        "SELECT category, amount, description, created_at FROM transactions "
        "WHERE type = 'expense' ORDER BY created_at DESC LIMIT 8"
    )
    procurement = db.query_all(
        "SELECT vendor_name, service_type, total_price, deposit_paid FROM procurement "
        "ORDER BY created_at DESC LIMIT 5"
    )
    top_agents = db.query_all(
        "SELECT a.name, COUNT(j.id) as total FROM agents a JOIN jamaah j ON a.id = j.agent_id "
        "WHERE j.status NOT IN ('Cancelled', 'Lead - Follow Up') GROUP BY a.id "
        "ORDER BY total DESC LIMIT 5"
    )
    bvisa = db.query_one(
        "SELECT COUNT(*) as c FROM jamaah WHERE status IN ('Lunas') AND visa_status = 'Belum Proses' "
        "AND created_at < datetime('now', '-7 days')"
    )
    bpay = db.query_one(
        "SELECT COUNT(*) as c FROM jamaah WHERE status = 'Terdaftar' AND paid_amount = 0 "
        "AND created_at < datetime('now', '-14 days')"
    )
    inc_s = (inc["s"] or 0) if inc else 0
    exp_s = (exp["s"] or 0) if exp else 0
    return {
        "funnel": funnel,
        "visa": visa,
        "lowStock": low_stock,
        "finance": {
            "balance": inc_s - exp_s,
            "piutang": piutang,
            "income": inc_s,
            "expense": exp_s,
        },
        "unassignedPkgs": unassigned,
        "topAgents": top_agents,
        "recentExpenses": recent_exp,
        "procurement": procurement,
        "bottlenecks": {
            "visa": (bvisa["c"] or 0) if bvisa else 0,
            "payment": (bpay["c"] or 0) if bpay else 0,
        },
    }


@app.get("/api/tactical-stats")
async def tactical_stats(user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    piutang = _total_piutang()
    payroll = db.query_one("SELECT SUM(base_salary) as payroll FROM users")
    readiness = db.query_all(
        "SELECT p.name, p.departure_date, COUNT(j.id) as total_jamaah, "
        "SUM(CASE WHEN j.status = 'Lunas' THEN 1 ELSE 0 END) as lunas_count, "
        "SUM(CASE WHEN j.visa_status = 'Visa Approved' THEN 1 ELSE 0 END) as visa_count "
        "FROM packages p LEFT JOIN jamaah j ON j.package_type = p.name "
        "AND j.status NOT IN ('Lead - Follow Up', 'Cancelled') "
        "GROUP BY p.id ORDER BY p.departure_date DESC LIMIT 4"
    )
    bottleneck = db.query_one(
        "SELECT COUNT(*) as count FROM jamaah WHERE status IN ('Lunas', 'DP Masuk') "
        "AND visa_status = 'Belum Proses' AND created_at < datetime('now', '-7 days')"
    )
    return {
        "piutang": piutang,
        "payroll": (payroll["payroll"] or 0) if payroll else 0,
        "readiness": readiness or [],
        "bottlenecks": bottleneck["count"] if bottleneck else 0,
    }




@app.get("/api/audit-logs")
async def audit_logs(user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    return db.query_all("SELECT * FROM audit_logs ORDER BY created_at DESC LIMIT 100", ()) or []


# ===========================================================================
# CRUD JAMAAH
# ===========================================================================
@app.get("/api/jamaah")
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


@app.post("/api/jamaah")
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

    log_action(user,"CREATE_JAMAAH", f"Mendaftar jamaah baru: {g('name')} (NIK: {g('nik')})")

    # Bila jamaah ini berasal dari form pendaftaran publik (lihat /api/public/pendaftaran),
    # tautkan balik & tandai submission itu sebagai diterima — reuse endpoint ini sebagai
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






@app.put("/api/jamaah/bulk-ops")
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


# NB: route literal /api/jamaah/bulk-ops HARUS didaftarkan sebelum /api/jamaah/{jid}
# di bawah ini -- FastAPI/Starlette mencocokkan route berurutan sesuai urutan
# didaftarkan, jadi kalau {jid} didaftarkan duluan, request ke .../bulk-ops akan
# ketangkep pola {jid} lebih dulu dan gagal validasi (int("bulk-ops") error 400).
@app.put("/api/jamaah/{jid}")
async def jamaah_update(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    g = body.get
    row = db.query_one("SELECT * FROM jamaah WHERE id = ?", (jid,))
    if not row:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")

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
            "relation = ?, address = ?, province = ?, city = ?, subdistrict = ?, village = ? WHERE id = ?",
            (
                g("nik"), g("name"), g("phone"), g("health_history"), g("mahram"),
                g("orderer_name"), g("gender"), g("birth_place"), g("birth_date"), g("citizenship"),
                g("identity_type"), g("family_phone"), g("email"), g("father_name"),
                g("education"), g("job"), g("marital_status"), g("relation"), g("address"),
                g("province"), g("city"), g("subdistrict"), g("village"), jid,
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

    db.execute(
        "UPDATE jamaah SET nik = ?, name = ?, phone = ?, status = ?, health_history = ?, mahram = ?, "
        "package_type = ?, total_price = ?, orderer_name = ?, gender = ?, birth_place = ?, birth_date = ?, "
        "citizenship = ?, identity_type = ?, family_phone = ?, email = ?, father_name = ?, education = ?, "
        "job = ?, marital_status = ?, relation = ?, address = ?, province = ?, city = ?, subdistrict = ?, "
        "village = ?, room_type = ? WHERE id = ?",
        (
            g("nik"), g("name"), g("phone"), g("status"), g("health_history"), g("mahram"),
            final_package, final_price, g("orderer_name"), g("gender"), g("birth_place"), g("birth_date"),
            g("citizenship"), g("identity_type"), g("family_phone"), g("email"), g("father_name"),
            g("education"), g("job"), g("marital_status"), g("relation"), g("address"),
            g("province"), g("city"), g("subdistrict"), g("village"), g("room_type"), jid,
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
        changes.append(f"Total Harga (Rp {fmt_id(old_price)} → Rp {fmt_id(new_price)}) [Keterangan: {price_change_note}]")

    log_action(
        user, "UPDATE_JAMAAH",
        f"Update data jamaah {row['name']}: " + ("; ".join(changes) if changes else "tidak ada perubahan field utama"),
    )
    notify("data_updated", "jamaah")
    return {"message": "Data Jamaah berhasil diupdate."}


@app.put("/api/jamaah/{jid}/doc-completeness")
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


@app.post("/api/jamaah/{jid}/documents")
async def jamaah_documents(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    doc_type = body.get("docType")
    file_base64 = body.get("fileBase64", "")
    ext = body.get("ext")
    if doc_type not in ("ktp", "kk", "passport", "vaccine"):
        raise HTTPException(status_code=400, detail="Tipe dokumen tidak valid")

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
# PEMBAYARAN (FINANCE)
# ===========================================================================
@app.put("/api/jamaah/{jid}/payment")
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
        f"🧾 *INVOICE PEMBAYARAN RESMI*\n🏢 *UMAR TRAVEL*\n\n"
        f"Assalamu'alaikum Bpk/Ibu {jamaah['name']},\n\nKami telah menerima pembayaran Anda:\n\n"
        f"*Program/Paket:* {jamaah['package_type']}\n"
        f"*Nominal Masuk:* Rp {fmt_id(paid_amount)}\n"
        f"*Total Terbayar:* Rp {fmt_id(new_paid)}\n"
        f"*Status:* {status_bayar}\n\nJazakumullah Khairan atas kepercayaannya."
    )
    fire_and_forget(wa.send_message(jamaah["phone"], wa_msg))
    log_action(user,"PAYMENT", f"Menerima dana Rp {paid_amount} dari {jamaah['name']}")
    notify("data_updated", "jamaah")
    return {"message": "Pembayaran berhasil diupdate."}


# ===========================================================================
# AUTOMASI CEK VISA (OPS)
# ===========================================================================
@app.post("/api/jamaah/{jid}/check-visa")
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
# LOGISTIK & OPERASIONAL
# ===========================================================================
@app.put("/api/jamaah/{jid}/ops")
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










# ===========================================================================
# HOME FINANCE (F-A) & HOME MANAGEMENT (M-A)
# ===========================================================================
# /api/finance/home: pindah ke routes/finance.py

@app.get("/api/search")
async def global_search(q: str = "", user=Depends(authenticate_token)):
    """Global search: jamaah + paket + agen + user. Batasi 5 per kategori supaya
    palette tidak kelebihan hasil. Role-gated: sales cuma cari jamaah/paket/agen
    yang dia handle."""
    q = (q or "").strip()
    if len(q) < 2:
        return {"results": []}

    like = f"%{q}%"
    role = user.get("role")
    results = []

    # Jamaah
    jamaah_filter = ""
    jamaah_params = [like, like, like]
    if role == "sales":
        jamaah_filter = " AND sales_id = ?"
        jamaah_params.append(user["id"])
    for j in db.query_all(
        "SELECT id, name, phone, package_type, payment_status, status FROM jamaah "
        f"WHERE (name LIKE ? OR phone LIKE ? OR nik LIKE ?){jamaah_filter} "
        "ORDER BY name ASC LIMIT 5",
        tuple(jamaah_params),
    ):
        results.append({
            "kind": "jamaah",
            "id": j["id"],
            "title": j["name"],
            "subtitle": f"{j['phone'] or '-'} - {j['package_type'] or '-'}",
            "meta": f"{j['payment_status'] or '-'} - {j['status'] or '-'}",
            "goto": "jamaah",
        })

    # Paket
    for p in db.query_all(
        "SELECT id, name, departure_date, quota FROM packages "
        "WHERE name LIKE ? ORDER BY departure_date DESC LIMIT 5",
        (like,),
    ):
        results.append({
            "kind": "paket",
            "id": p["id"],
            "title": p["name"],
            "subtitle": f"Berangkat {p['departure_date'] or '-'}",
            "meta": f"Kuota {p['quota'] or 0}",
            "goto": "packages",
        })

    # Agen
    agent_filter = ""
    agent_params = [like, like]
    if role == "sales":
        agent_filter = " AND handler_cs_id = ?"
        agent_params.append(user["id"])
    for a in db.query_all(
        "SELECT id, name, phone, city FROM agents "
        f"WHERE (name LIKE ? OR phone LIKE ?){agent_filter} "
        "ORDER BY name ASC LIMIT 5",
        tuple(agent_params),
    ):
        results.append({
            "kind": "agen",
            "id": a["id"],
            "title": a["name"],
            "subtitle": f"{a['phone'] or '-'} - {a['city'] or '-'}",
            "meta": "",
            "goto": "agents",
        })

    # User (admin/mgmt only)
    if role in ("admin", "management"):
        for u in db.query_all(
            "SELECT id, name, username, role FROM users "
            "WHERE name LIKE ? OR username LIKE ? "
            "ORDER BY name ASC LIMIT 5",
            (like, like),
        ):
            results.append({
                "kind": "user",
                "id": u["id"],
                "title": u["name"],
                "subtitle": f"@{u['username']}",
                "meta": u["role"],
                "goto": "users",
            })

    return {"results": results, "query": q, "count": len(results)}


# /api/finance/aged-receivable: pindah ke routes/finance.py

# get /api/mgmt/monthly-pdf: pindah ke routes/mgmt.py

# get /api/mgmt/company-targets: pindah ke routes/mgmt.py

# post /api/mgmt/company-targets: pindah ke routes/mgmt.py
# get /api/mgmt/home: pindah ke routes/mgmt.py
# get /api/mgmt/risk-register: pindah ke routes/mgmt.py

# ===========================================================================
# HALAMAN STATIS & PANDUAN
# ===========================================================================
@app.get("/panduan")
async def panduan():
    return FileResponse(os.path.join(BASE_DIR, "Panduan.html"))


@app.get("/daftar")
async def daftar_publik():
    return FileResponse(os.path.join(BASE_DIR, "Pendaftaran-Publik.html"))


@app.get("/daftar-agen")
async def daftar_agen_publik():
    return FileResponse(os.path.join(BASE_DIR, "Pendaftaran-Agen-Publik.html"))


@app.get("/dokumentasi-teknis")
async def dokumentasi_teknis():
    return FileResponse(os.path.join(BASE_DIR, "Dokumentasi-Teknis.html"))


# Penyajian file PII/media WA dengan autentikasi (token via header atau ?token=).
# Route ini didefinisikan SEBELUM mount statis sehingga /uploads/* TIDAK pernah
# dilayani publik. File fisik tersimpan di folder privat di luar public/.
@app.get("/uploads/{filename}")
async def serve_upload(filename: str, _user=Depends(authenticate_file_token)):
    safe = os.path.basename(filename)  # cegah path traversal (../)
    path = os.path.join(UPLOAD_DIR, safe)
    if not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="File tidak ditemukan")
    return FileResponse(path)


# -- Modular routers (dipecah dari app.py agar file ini tidak membengkak) --
# Setiap router mendaftar endpoint-nya sendiri lewat APIRouter + include_router.
from routes.reconcile import router as reconcile_router  # noqa: E402
from routes.finance import router as finance_router  # noqa: E402
from routes.mgmt import router as mgmt_router  # noqa: E402
from routes.ops import router as ops_router  # noqa: E402
from routes.sales import router as sales_router  # noqa: E402
from routes.jamaah import router as jamaah_router  # noqa: E402
from routes.packages import router as packages_router  # noqa: E402
from routes.inventory import router as inventory_router  # noqa: E402
from routes.marketing import router as marketing_router  # noqa: E402
from routes.users import router as users_router  # noqa: E402
from routes.agents import router as agents_router  # noqa: E402
from routes.expense import router as expense_router  # noqa: E402
from routes.procurement import router as procurement_router  # noqa: E402
from routes.finance_tx import router as finance_tx_router  # noqa: E402
from routes.hr import router as hr_router  # noqa: E402
from routes.settings import router as settings_router  # noqa: E402
from routes.wa import router as wa_router  # noqa: E402
app.include_router(reconcile_router)
app.include_router(finance_router)
app.include_router(mgmt_router)
app.include_router(ops_router)
app.include_router(sales_router)
app.include_router(jamaah_router)
app.include_router(packages_router)
app.include_router(inventory_router)
app.include_router(marketing_router)
app.include_router(users_router)
app.include_router(agents_router)
app.include_router(expense_router)
app.include_router(procurement_router)
app.include_router(finance_tx_router)
app.include_router(hr_router)
app.include_router(settings_router)
app.include_router(wa_router)


# Static files (public/) dipasang TERAKHIR agar route /api, /panduan, /uploads menang.
# html=True -> "/" otomatis melayani index.html.
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="static")


# Gabungkan FastAPI + Socket.IO menjadi satu aplikasi ASGI.
asgi = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")


if __name__ == "__main__":
    uvicorn.run("app:asgi", host="0.0.0.0", port=PORT, log_level="info")
