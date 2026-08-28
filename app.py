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
def fmt_id(n) -> str:
    """Format angka ala toLocaleString('id-ID'): 1000000 -> '1.000.000'."""
    try:
        return f"{int(n):,}".replace(",", ".")
    except (TypeError, ValueError):
        return str(n)


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
# PENGATURAN APLIKASI (branding, ambang DP, dll.)
# ===========================================================================
@app.get("/api/settings")
async def settings_get():
    # Publik (tanpa auth) karena dibutuhkan layar login untuk menampilkan branding.
    # Hanya kunci tampilan non-sensitif yang dikembalikan (whitelist), sehingga
    # setting sensitif apa pun yang ditambahkan di masa depan tidak ikut bocor.
    public_keys = (
        "company_name", "company_tagline", "logo_url", "equipment_min_dp_percent",
        "company_legal_name", "company_address", "company_email", "company_website",
    )
    placeholders = ", ".join("?" * len(public_keys))
    rows = db.query_all(
        f"SELECT key, value FROM settings WHERE key IN ({placeholders})", public_keys
    )
    return {r["key"]: r["value"] for r in rows}


@app.put("/api/settings")
async def settings_update(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    allowed = {
        "company_name", "company_tagline", "logo_url", "equipment_min_dp_percent",
        "company_legal_name", "company_address", "company_email", "company_website",
    }
    saved = []
    for key, value in body.items():
        if key not in allowed:
            continue
        if key == "equipment_min_dp_percent":
            # Validasi angka 0-100
            try:
                pct = float(value)
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="Minimum DP harus berupa angka.")
            if pct < 0 or pct > 100:
                raise HTTPException(status_code=400, detail="Minimum DP harus antara 0 dan 100 persen.")
            value = str(pct.is_integer() and int(pct) or pct)
        db.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, str(value)),
        )
        saved.append(key)
    log_action(user,"UPDATE_SETTINGS", f"Mengubah pengaturan: {', '.join(saved)}")
    notify("data_updated", "settings")
    return {"message": "Pengaturan berhasil disimpan.", "saved": saved}


@app.post("/api/settings/logo")
async def settings_logo(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    file_base64 = body.get("fileBase64", "")
    ext = (body.get("ext") or "png").lower().lstrip(".")
    if ext not in ("png", "jpg", "jpeg", "webp", "gif", "svg"):
        raise HTTPException(status_code=400, detail="Format logo harus gambar (png/jpg/webp/gif/svg).")
    if not file_base64:
        raise HTTPException(status_code=400, detail="Tidak ada file logo.")

    os.makedirs(BRANDING_DIR, exist_ok=True)
    file_name = f"logo_{int(asyncio.get_event_loop().time() * 1000)}.{ext}"
    b64 = file_base64.split(",")[1] if "," in file_base64 else file_base64
    try:
        with open(os.path.join(BRANDING_DIR, file_name), "wb") as f:
            f.write(base64.b64decode(b64))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Data logo tidak valid.")

    logo_url = f"/branding/{file_name}"
    db.execute(
        "INSERT INTO settings (key, value) VALUES ('logo_url', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (logo_url,),
    )
    log_action(user,"UPDATE_LOGO", "Mengunggah logo baru perusahaan")
    notify("data_updated", "settings")
    return {"message": "Logo berhasil diperbarui.", "logo_url": logo_url}


# ===========================================================================
# MANAJEMEN WHATSAPP
# ===========================================================================
@app.get("/api/wa/status")
async def wa_status(user=Depends(authenticate_token)):
    return wa.get_status()


@app.post("/api/wa/connect")
async def wa_connect(user=Depends(authenticate_token)):
    wa.connect_to_whatsapp()
    return {"message": "Membuka koneksi WhatsApp..."}


@app.post("/api/wa/logout")
async def wa_logout(user=Depends(authenticate_token)):
    await wa.logout_whatsapp()
    return {"message": "WhatsApp berhasil diputus. Silakan scan ulang."}


@app.post("/api/wa/send")
async def wa_send(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    target = body.get("target")
    message = body.get("message")
    if wa.get_status()["status"] != "connected":
        raise HTTPException(status_code=400, detail="WhatsApp belum terhubung!")

    if target == "all":
        rows = db.query_all("SELECT phone FROM jamaah", ())
        count = 0
        for row in rows:
            if row["phone"]:
                await wa.send_message(row["phone"], message)
                count += 1
                await asyncio.sleep(1.5)  # jeda anti-banned
        return {"message": f"Broadcast berhasil dikirim ke {count} jamaah."}
    else:
        success = await wa.send_message(target, message)
        if success:
            return {"message": "Pesan berhasil terkirim."}
        raise HTTPException(status_code=500, detail="Gagal mengirim pesan.")


@app.post("/api/wa/remind-payment")
async def wa_remind_payment(user=Depends(authenticate_token)):
    # FIX (integritas): jangkau SEMUA jamaah dengan sisa tagihan & booking aktif,
    # bukan hanya status 'Terdaftar'. Sebelumnya jamaah ber-status 'Lead'/'Waitlisted'
    # yang sudah berutang tidak pernah dapat pengingat.
    rows = db.query_all(
        "SELECT name, phone, total_price, paid_amount FROM jamaah "
        "WHERE total_price > paid_amount "
        "AND status NOT IN ('Lead - Follow Up', 'Cancelled', 'Lunas')",
        (),
    )
    count = 0
    for j in rows:
        if j["phone"]:
            sisa = (j["total_price"] or 0) - (j["paid_amount"] or 0)
            msg = (
                f"Assalamu'alaikum Bpk/Ibu {j['name']},\n\n"
                f"Kami dari *Umar Travel* ingin menginformasikan bahwa paket Umroh Anda "
                f"masih memiliki sisa tagihan sebesar *Rp {fmt_id(sisa)}*.\n\n"
                f"Mohon segera melengkapi pembayaran agar proses Visa dan manifestasi "
                f"penerbangan dapat segera kami proses. Jazakumullah khairan."
            )
            if wa.get_status()["status"] == "connected":
                await wa.send_message(j["phone"], msg)
                count += 1
                await asyncio.sleep(1.5)
    return {"message": f"Pengingat otomatis berhasil dikirim ke {count} jamaah yang belum lunas."}


# ===========================================================================
# LIVE CHAT WA (MULTI-AGENT)
# ===========================================================================
@app.get("/api/wa/templates")
async def wa_templates(user=Depends(authenticate_token)):
    return db.query_all("SELECT * FROM wa_templates", ()) or []


@app.post("/api/wa/templates")
async def wa_template_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    last_id, _ = db.execute(
        "INSERT INTO wa_templates (title, content) VALUES (?, ?)",
        (body.get("title"), body.get("content")),
    )
    return {"id": last_id}


@app.delete("/api/wa/templates/{tid}")
async def wa_template_delete(tid: int, user=Depends(authenticate_token)):
    db.execute("DELETE FROM wa_templates WHERE id = ?", (tid,))
    return {"success": True}


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


# ===========================================================================
# PENDAFTARAN PUBLIK (tanpa login) -> antrian validasi CS -> jamaah
# Calon jamaah isi form publik (/daftar) -> tersimpan di pendaftaran_publik
# (bukan tabel jamaah) -> CS review di Kotak Masuk -> Terima (lewat POST
# /api/jamaah yang sama seperti pendaftaran manual) atau Tolak.
# ===========================================================================
_public_submit_log = {}  # ip -> [timestamp, ...], in-memory (cukup untuk skala 1 server)
_public_agent_submit_log = {}  # namespace terpisah dari pendaftaran jamaah di atas
RATE_LIMIT_MAX = 3
RATE_LIMIT_WINDOW_SEC = 3600


def _check_rate_limit(ip, log_store=None):
    log_store = _public_submit_log if log_store is None else log_store
    now = time.time()
    recent = [t for t in log_store.get(ip, []) if now - t < RATE_LIMIT_WINDOW_SEC]
    if len(recent) >= RATE_LIMIT_MAX:
        log_store[ip] = recent
        return False
    recent.append(now)
    log_store[ip] = recent
    return True


@app.post("/api/public/pendaftaran")
async def public_pendaftaran_create(request: Request, body: dict = Depends(json_body)):
    g = body.get

    # Honeypot: field tersembunyi yang cuma bot biasanya isi. Pura-pura sukses
    # (tanpa menyimpan) supaya bot tidak tahu submission-nya ditolak & tidak mencoba lagi.
    if g("website"):
        return {"message": "Data Anda sudah kami terima dan akan diverifikasi tim kami."}

    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip):
        raise HTTPException(
            status_code=429, detail="Terlalu banyak percobaan dari perangkat ini. Silakan coba lagi nanti."
        )

    name = (g("name") or "").strip()
    phone = (g("phone") or "").strip()
    nik = (g("nik") or "").strip()
    if not name or not phone or not nik:
        raise HTTPException(status_code=400, detail="Nama, No. WhatsApp, dan Nomor Identitas wajib diisi.")

    preferred_cs_id = g("preferred_cs_id")
    db.execute(
        "INSERT INTO pendaftaran_publik (name, orderer_name, citizenship, identity_type, nik, "
        "passport_number, passport_issued, passport_issuer_city, gender, birth_place, birth_date, phone, "
        "family_phone, email, address, province, city, subdistrict, village, health_history, mahram, "
        "interest_note, source_ip, preferred_cs_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name, g("orderer_name"), g("citizenship") or "WNI", g("identity_type") or "NIK", nik,
            g("passport_number"), g("passport_issued"), g("passport_issuer_city"),
            g("gender"), g("birth_place"), g("birth_date"), phone, g("family_phone"), g("email"),
            g("address"), g("province"), g("city"), g("subdistrict"), g("village"),
            g("health_history"), g("mahram"), g("interest_note"), ip,
            int(preferred_cs_id) if preferred_cs_id else None,
        ),
    )
    notify("data_updated", "pendaftaran_publik")
    return {
        "message": "Terima kasih! Data Anda sudah kami terima dan akan diverifikasi tim kami. "
        "Kami akan menghubungi Anda melalui WhatsApp dalam 1x24 jam."
    }


# Daftar CS (role sales) untuk dropdown "Pilih CS" di form publik -- sengaja endpoint
# terbuka (tanpa login) tapi cuma mengembalikan id+nama, tidak ada data sensitif.
@app.get("/api/public/cs-list")
async def public_cs_list():
    return db.query_all("SELECT id, name FROM users WHERE role = 'sales' ORDER BY name ASC", ())


@app.get("/api/pendaftaran-publik")
async def pendaftaran_publik_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    rows = db.query_all(
        "SELECT p.*, u.name as preferred_cs_name FROM pendaftaran_publik p "
        "LEFT JOIN users u ON p.preferred_cs_id = u.id ORDER BY p.created_at DESC", ()
    )
    result = []
    for r in rows:
        r = dict(r)
        existing = db.query_one("SELECT id FROM jamaah WHERE nik = ?", (r["nik"],)) if r["nik"] else None
        r["nik_exists"] = bool(existing)
        result.append(r)
    return result


@app.put("/api/pendaftaran-publik/{pid}/reject")
async def pendaftaran_publik_reject(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")

    row = db.query_one("SELECT * FROM pendaftaran_publik WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Data pendaftaran tidak ditemukan")
    if row["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Data ini sudah berstatus '{row['status']}'.")

    db.execute(
        "UPDATE pendaftaran_publik SET status = 'Ditolak', reviewed_by = ?, "
        "reviewed_at = CURRENT_TIMESTAMP, review_note = ? WHERE id = ?",
        (user["name"], reason, pid),
    )
    log_action(user, "REJECT_PUBLIC_REGISTRATION", f"Menolak pendaftaran publik dari {row['name']}: {reason}")
    notify("data_updated", "pendaftaran_publik")
    return {"message": "Pendaftaran publik berhasil ditolak."}


# ===========================================================================
# PENDAFTARAN AGEN/KONSULTAN PUBLIK (tanpa login) -> antrian validasi CS/Admin
# -> agents. Calon agen isi form publik (/daftar-agen) -> tersimpan di
# pendaftaran_agen_publik (bukan tabel agents) -> Admin/CS review di Kotak Masuk
# Keagenan -> Terima (lewat POST /api/agents yang sama seperti tambah agen manual,
# dengan source_submission_id) atau Tolak. Pola identik dengan pendaftaran jamaah.
# ===========================================================================
@app.post("/api/public/agent-registration")
async def public_agent_registration_create(request: Request, body: dict = Depends(json_body)):
    g = body.get

    # Honeypot: sama seperti pendaftaran jamaah -> pura-pura sukses ke bot.
    if g("website"):
        return {"message": "Data Anda sudah kami terima dan akan diverifikasi tim kami."}

    ip = request.client.host if request.client else "unknown"
    if not _check_rate_limit(ip, _public_agent_submit_log):
        raise HTTPException(
            status_code=429, detail="Terlalu banyak percobaan dari perangkat ini. Silakan coba lagi nanti."
        )

    name = (g("name") or "").strip()
    phone = (g("phone") or "").strip()
    if not name or not phone:
        raise HTTPException(status_code=400, detail="Nama dan No. WhatsApp wajib diisi.")

    preferred_cs_id = g("preferred_cs_id")
    db.execute(
        "INSERT INTO pendaftaran_agen_publik (name, phone, email, instagram, address, "
        "province, city, subdistrict, village, source_ip, preferred_cs_id) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            name, phone, g("email"), g("instagram"), g("address"),
            g("province"), g("city"), g("subdistrict"), g("village"), ip,
            int(preferred_cs_id) if preferred_cs_id else None,
        ),
    )
    notify("data_updated", "pendaftaran_agen_publik")
    return {
        "message": "Terima kasih! Pendaftaran Anda sebagai mitra/agen sudah kami terima dan akan "
        "diverifikasi tim kami. Kami akan menghubungi Anda melalui WhatsApp dalam 1x24 jam."
    }


@app.get("/api/pendaftaran-agen")
async def pendaftaran_agen_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    return db.query_all(
        "SELECT p.*, u.name as preferred_cs_name FROM pendaftaran_agen_publik p "
        "LEFT JOIN users u ON p.preferred_cs_id = u.id ORDER BY p.created_at DESC", ()
    )


@app.put("/api/pendaftaran-agen/{pid}/reject")
async def pendaftaran_agen_reject(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")

    row = db.query_one("SELECT * FROM pendaftaran_agen_publik WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Data pendaftaran tidak ditemukan")
    if row["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Data ini sudah berstatus '{row['status']}'.")

    db.execute(
        "UPDATE pendaftaran_agen_publik SET status = 'Ditolak', reviewed_by = ?, "
        "reviewed_at = CURRENT_TIMESTAMP, review_note = ? WHERE id = ?",
        (user["name"], reason, pid),
    )
    log_action(user, "REJECT_AGENT_REGISTRATION", f"Menolak pendaftaran agen dari {row['name']}: {reason}")
    notify("data_updated", "pendaftaran_agen_publik")
    return {"message": "Pendaftaran agen berhasil ditolak."}


# ===========================================================================
# SURAT IZIN KARYAWAN: semua role bisa mengajukan (hanya lihat riwayat sendiri),
# Admin/Management yang menyetujui/menolak. Terpisah dari Manajemen Karyawan (HR)
# yang datanya (termasuk gaji pokok) tetap dibatasi admin/management saja.
# ===========================================================================
LEAVE_ANNUAL_QUOTA_DAYS = 12  # jatah Cuti Tahunan per tahun -- HANYA jenis ini yang
# dibatasi kuotanya; Sakit/Izin Keperluan Pribadi/Lainnya tidak memotong kuota apapun.


def _leave_duration_days(start_date, end_date):
    """Durasi inklusif kedua ujung tanggal (mis. 01-03 Agustus = 3 hari) -- cermin
    persis dari leaveDurationDays() di frontend (public/index.html) agar konsisten."""
    start = datetime.date.fromisoformat(start_date)
    end = datetime.date.fromisoformat(end_date)
    return (end - start).days + 1


@app.post("/api/leave-requests")
async def leave_request_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    g = body.get
    leave_type = (g("leave_type") or "").strip()
    start_date = (g("start_date") or "").strip()
    end_date = (g("end_date") or "").strip()
    reason = (g("reason") or "").strip()
    if not leave_type or not start_date or not end_date or not reason:
        raise HTTPException(
            status_code=400, detail="Jenis izin, tanggal mulai/selesai, dan alasan wajib diisi."
        )
    db.execute(
        "INSERT INTO leave_requests (user_id, user_name, leave_type, start_date, end_date, reason) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user["id"], user["name"], leave_type, start_date, end_date, reason),
    )
    log_action(user, "CREATE_LEAVE_REQUEST", f"Mengajukan {leave_type}: {start_date} s/d {end_date}")
    notify("data_updated", "leave_request")
    return {"message": "Pengajuan izin berhasil dikirim, menunggu persetujuan."}


@app.get("/api/leave-requests")
async def leave_requests_list(user=Depends(authenticate_token)):
    if user["role"] in ("admin", "management"):
        return db.query_all("SELECT * FROM leave_requests ORDER BY requested_at DESC", ())
    return db.query_all(
        "SELECT * FROM leave_requests WHERE user_id = ? ORDER BY requested_at DESC", (user["id"],)
    )


@app.put("/api/leave-requests/{lid}/review")
async def leave_request_review(lid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    action = body.get("action")
    row = db.query_one("SELECT * FROM leave_requests WHERE id = ?", (lid,))
    if not row:
        raise HTTPException(status_code=404, detail="Pengajuan izin tidak ditemukan")
    if row["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Pengajuan ini sudah berstatus '{row['status']}'.")

    if action == "approve":
        # Gatekeeper kuota: hanya Cuti Tahunan yang dibatasi 12 hari/tahun, dan baru
        # dicek saat APPROVAL (bukan submission) -- karena kuota baru benar-benar
        # terpakai begitu disetujui, bukan saat masih Pending.
        if row["leave_type"] == "Cuti Tahunan":
            year = row["start_date"][:4]
            approved_rows = db.query_all(
                "SELECT start_date, end_date FROM leave_requests WHERE user_id = ? AND "
                "leave_type = 'Cuti Tahunan' AND status = 'Disetujui' AND substr(start_date, 1, 4) = ?",
                (row["user_id"], year),
            )
            used_days = sum(_leave_duration_days(r["start_date"], r["end_date"]) for r in approved_rows)
            this_duration = _leave_duration_days(row["start_date"], row["end_date"])
            if used_days + this_duration > LEAVE_ANNUAL_QUOTA_DAYS:
                remaining = max(0, LEAVE_ANNUAL_QUOTA_DAYS - used_days)
                raise HTTPException(
                    status_code=400,
                    detail=(
                        f"Kuota Cuti Tahunan {year} tidak cukup. Sudah terpakai {used_days} dari "
                        f"{LEAVE_ANNUAL_QUOTA_DAYS} hari (sisa {remaining} hari), sementara pengajuan "
                        f"ini butuh {this_duration} hari."
                    ),
                )
        db.execute(
            "UPDATE leave_requests SET status = 'Disetujui', approved_by = ?, "
            "approved_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user["name"], lid),
        )
        msg = "Pengajuan izin disetujui."
    elif action == "reject":
        reject_reason = (body.get("reason") or "").strip()
        if not reject_reason:
            raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")
        db.execute(
            "UPDATE leave_requests SET status = 'Ditolak', approved_by = ?, "
            "approved_at = CURRENT_TIMESTAMP, reject_reason = ? WHERE id = ?",
            (user["name"], reject_reason, lid),
        )
        msg = "Pengajuan izin ditolak."
    else:
        raise HTTPException(status_code=400, detail="Aksi tidak valid.")

    log_action(user, "REVIEW_LEAVE_REQUEST", f"{msg} ({row['user_name']}: {row['leave_type']})")
    notify("data_updated", "leave_request")
    return {"message": msg}





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


# Static files (public/) dipasang TERAKHIR agar route /api, /panduan, /uploads menang.
# html=True -> "/" otomatis melayani index.html.
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="static")


# Gabungkan FastAPI + Socket.IO menjadi satu aplikasi ASGI.
asgi = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")


if __name__ == "__main__":
    uvicorn.run("app:asgi", host="0.0.0.0", port=PORT, log_level="info")
