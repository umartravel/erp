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
from expense_pdf import build_expense_pdf
from jamaah_docs_pdf import auto_group_rooms, build_absensi_pdf, build_manifest_pdf, build_roomlist_pdf
from realtime import notify, sio

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


def parse_int(value, field="nilai"):
    """Parse angka dari input. Balikan HTTP 400 yang rapi bila tidak valid
    (alih-alih error 500 yang tidak informatif)."""
    try:
        return int(float(value))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"Input {field} harus berupa angka yang valid.")


# --- Dimensi status: cermin `status` lama diturunkan dari 4 dimensi bersih ---
def _derive_status(pipeline, payment, visa, trip):
    """Hasilkan label `status` legacy (kompatibel frontend) dari dimensi terpisah.
    Urutan prioritas mempertahankan perilaku tampilan lama."""
    if trip == "OnTrip":
        return "On Trip"
    if pipeline == "Cancelled":
        return "Cancelled"
    if visa == "Visa Approved":
        return "Visa Approved"
    if payment == "Lunas":
        return "Lunas"
    if payment == "DP":
        return "DP Masuk"
    if pipeline == "Registered":
        return "Terdaftar"
    if pipeline == "Waitlisted":
        return "Waitlisted"
    return "Lead - Follow Up"


def sync_status_mirror(jid):
    """Tulis ulang kolom `status` legacy agar konsisten dengan dimensi terkini."""
    j = db.query_one(
        "SELECT pipeline_stage, payment_status, visa_status, trip_status FROM jamaah WHERE id = ?",
        (jid,),
    )
    if not j:
        return None
    label = _derive_status(
        j["pipeline_stage"], j["payment_status"], j["visa_status"], j["trip_status"]
    )
    db.execute("UPDATE jamaah SET status = ? WHERE id = ?", (label, jid))
    return label


def status_to_dims(status, paid, total):
    """Petakan label legacy + nominal bayar -> (pipeline, payment, trip).
    Dipakai saat membuat/mengubah jamaah lewat input status klasik."""
    paid = paid or 0
    total = total or 0
    if paid > 0 and total > 0 and paid >= total:
        payment = "Lunas"
    elif paid > 0:
        payment = "DP"
    else:
        payment = "Unpaid"
    trip = "OnTrip" if status == "On Trip" else "NotStarted"
    if status == "Cancelled":
        pipeline = "Cancelled"
    elif status == "Lead - Follow Up":
        pipeline = "Lead"
    elif status == "Waitlisted":
        pipeline = "Waitlisted"
    elif status == "Terdaftar":
        pipeline = "Registered"
    else:
        pipeline = "Booked"
    return pipeline, payment, trip


def log_action(user, action, details):
    db.execute(
        "INSERT INTO audit_logs (user_id, user_name, role, action, details) VALUES (?, ?, ?, ?, ?)",
        (user["id"], user["name"], user["role"], action, details),
    )
    notify("data_updated", "audit")


def require_role(user, *roles):
    if user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Akses Ditolak")


def _field_change(label, old_val, new_val):
    """Deskripsi perubahan satu field untuk audit trail -- dipakai di endpoint mana
    saja yang mengedit data lalu ingin log yang bilang APA yang berubah, bukan generik."""
    old_val, new_val = old_val or "-", new_val or "-"
    return f"{label} ({old_val} → {new_val})" if old_val != new_val else None


def assert_jamaah_access(jid, user):
    """Sales hanya boleh mengakses jamaah miliknya sendiri."""
    if user["role"] == "sales":
        row = db.query_one("SELECT sales_id FROM jamaah WHERE id = ?", (jid,))
        if not row:
            raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")
        if row["sales_id"] != user["id"]:
            raise HTTPException(status_code=403, detail="Akses Ditolak: bukan jamaah Anda.")


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
    token = create_token(user["id"], user["role"], user["name"])
    return {"token": token, "user": {"name": user["name"], "role": user["role"], "photo_url": user["photo_url"]}}


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
@app.get("/api/wa/conversations")
async def wa_conversations(user=Depends(authenticate_token)):
    return db.query_all(
        "SELECT * FROM wa_conversations ORDER BY last_updated DESC", ()
    )


@app.get("/api/wa/conversations/{phone}")
async def wa_conversation_messages(phone: str, user=Depends(authenticate_token)):
    return db.query_all(
        "SELECT * FROM wa_messages WHERE phone = ? ORDER BY created_at ASC", (phone,)
    )


@app.put("/api/wa/conversations/{phone}/read")
async def wa_conversation_read(phone: str, user=Depends(authenticate_token)):
    db.execute("UPDATE wa_conversations SET unread_count = 0 WHERE phone = ?", (phone,))
    return {"success": True}


@app.put("/api/wa/conversations/{phone}/assign")
async def wa_conversation_assign(
    phone: str, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    assigned_to = body.get("assigned_to")
    db.execute(
        "UPDATE wa_conversations SET assigned_to = ? WHERE phone = ?", (assigned_to, phone)
    )
    return {"success": True, "message": f"Obrolan WA telah diteruskan ke {assigned_to}"}


@app.post("/api/wa/conversations/{phone}/send")
async def wa_conversation_send(
    phone: str, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    message = body.get("message")
    success = await wa.send_message(phone, message)
    if not success:
        raise HTTPException(status_code=500, detail="Gagal mengirim pesan dari sistem.")

    db.execute(
        "INSERT INTO wa_messages (phone, sender, message) VALUES (?, 'system', ?)",
        (phone, message),
    )
    notify(
        "wa_new_message",
        {"phone": phone, "sender": "system", "message": message, "created_at": wa._now_iso()},
    )
    conv = db.query_one("SELECT id, name, unread_count FROM wa_conversations WHERE phone = ?", (phone,))
    if conv:
        db.execute(
            "UPDATE wa_conversations SET last_message = ?, last_updated = CURRENT_TIMESTAMP WHERE phone = ?",
            (message, phone),
        )
        notify("wa_conversation_updated", {
            "phone": phone, "name": conv["name"], "last_message": message,
            "unread_count": conv["unread_count"], "last_updated": wa._now_iso(),
        })
    else:
        db.execute(
            "INSERT INTO wa_conversations (phone, name, last_message, unread_count, last_updated) "
            "VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)",
            (phone, phone, message),
        )
        notify("wa_conversation_updated", {
            "phone": phone, "name": phone, "last_message": message,
            "unread_count": 0, "last_updated": wa._now_iso(),
        })
    return {"success": True}


@app.post("/api/wa/conversations/{phone}/send-media")
async def wa_conversation_send_media(
    phone: str, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    message = body.get("message")
    file_base64 = body.get("fileBase64", "")
    file_name = body.get("fileName", "file")
    mime_type = body.get("mimeType")
    media_type = body.get("mediaType")
    try:
        ext = file_name.split(".")[-1]
        safe_name = f"send_{int(asyncio.get_event_loop().time()*1000)}.{ext}"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        b64 = file_base64.split(",")[1] if "," in file_base64 else file_base64
        with open(os.path.join(UPLOAD_DIR, safe_name), "wb") as f:
            f.write(base64.b64decode(b64))
        media_url = f"/uploads/{safe_name}"

        success = await wa.send_message(
            phone,
            message or "",
            {"url": media_url, "type": media_type, "mimetype": mime_type, "fileName": file_name},
        )
        if not success:
            raise HTTPException(status_code=500, detail="Gagal mengirim media.")

        db.execute(
            "INSERT INTO wa_messages (phone, sender, message, media_url, media_type) "
            "VALUES (?, 'system', ?, ?, ?)",
            (phone, message or "", media_url, media_type),
        )
        notify("wa_new_message", {
            "phone": phone, "sender": "system", "message": message or "",
            "media_url": media_url, "media_type": media_type, "created_at": wa._now_iso(),
        })
        preview = f"[Lampiran {media_type}] {message or ''}"
        conv = db.query_one("SELECT id, name, unread_count FROM wa_conversations WHERE phone = ?", (phone,))
        if conv:
            db.execute(
                "UPDATE wa_conversations SET last_message = ?, last_updated = CURRENT_TIMESTAMP WHERE phone = ?",
                (preview, phone),
            )
            notify("wa_conversation_updated", {
                "phone": phone, "name": conv["name"], "last_message": preview,
                "unread_count": conv["unread_count"], "last_updated": wa._now_iso(),
            })
        else:
            db.execute(
                "INSERT INTO wa_conversations (phone, name, last_message, unread_count, last_updated) "
                "VALUES (?, ?, ?, 0, CURRENT_TIMESTAMP)",
                (phone, phone, preview),
            )
            notify("wa_conversation_updated", {
                "phone": phone, "name": phone, "last_message": preview,
                "unread_count": 0, "last_updated": wa._now_iso(),
            })
        return {"success": True, "media_url": media_url}
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        raise HTTPException(status_code=500, detail=str(e))


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


@app.put("/api/wa/conversations/{phone}/name")
async def wa_conversation_name(
    phone: str, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    name = body.get("name")
    db.execute("UPDATE wa_conversations SET name = ? WHERE phone = ?", (name, phone))
    return {"success": True, "message": f"Kontak berhasil disimpan sebagai {name}"}


# ===========================================================================
# DASHBOARD & LAPORAN
# ===========================================================================
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
    piutang = db.query_one(
        "SELECT SUM(total_price - paid_amount) as s FROM jamaah "
        "WHERE status NOT IN ('Lead - Follow Up', 'Cancelled') AND total_price > paid_amount"
    )
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
            "piutang": (piutang["s"] or 0) if piutang else 0,
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


@app.get("/api/stats")
async def stats(user=Depends(authenticate_token)):
    t = db.query_one("SELECT COUNT(*) as c FROM jamaah")
    l = db.query_one("SELECT COUNT(*) as c FROM jamaah WHERE status = 'Lunas'")
    v = db.query_one("SELECT COUNT(*) as c FROM jamaah WHERE status LIKE 'Lead%'")
    inc = db.query_one("SELECT SUM(amount) as s FROM transactions WHERE type = 'income'")
    exp = db.query_one("SELECT SUM(amount) as s FROM transactions WHERE type = 'expense'")
    pkgs = db.query_all(
        "SELECT name, departure_date, price FROM packages ORDER BY departure_date DESC LIMIT 5"
    )
    inc_s = inc["s"] or 0 if inc else 0
    exp_s = exp["s"] or 0 if exp else 0
    return {
        "jamaah": {"total": t["c"] or 0, "lunas": l["c"] or 0, "lead": v["c"] or 0},
        "finance": {"income": inc_s, "expense": exp_s, "balance": inc_s - exp_s},
        "upcomingPackages": pkgs,
    }


@app.get("/api/reports/executive")
async def reports_executive(user=Depends(authenticate_token)):
    cities = db.query_all(
        "SELECT city, COUNT(*) as total FROM jamaah WHERE city IS NOT NULL AND city != '' "
        "GROUP BY city ORDER BY total DESC LIMIT 5"
    )
    annual = db.query_all(
        "SELECT strftime('%Y', created_at) as year, COUNT(*) as total FROM jamaah "
        "GROUP BY year ORDER BY year DESC LIMIT 5"
    )
    return {"topCities": cities, "annualDepartures": annual}


@app.get("/api/tactical-stats")
async def tactical_stats(user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    piutang = db.query_one(
        "SELECT SUM(total_price - paid_amount) as piutang FROM jamaah "
        "WHERE status NOT IN ('Lead - Follow Up', 'Cancelled') AND total_price > paid_amount"
    )
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
        "piutang": (piutang["piutang"] or 0) if piutang else 0,
        "payroll": (payroll["payroll"] or 0) if payroll else 0,
        "readiness": readiness or [],
        "bottlenecks": bottleneck["count"] if bottleneck else 0,
    }


# ===========================================================================
# INSIDEN & AUDIT
# ===========================================================================
@app.get("/api/incidents")
async def incidents_list(user=Depends(authenticate_token)):
    return db.query_all("SELECT * FROM incidents ORDER BY created_at DESC", ()) or []


@app.post("/api/incidents")
async def incidents_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    db.execute(
        "INSERT INTO incidents (package_name, reported_by, incident_text) VALUES (?, ?, ?)",
        (body.get("package_name") or "Umum", user["name"], body.get("incident_text")),
    )
    notify("data_updated", "incident")
    return {"message": "Laporan insiden berhasil dikirim ke Pusat."}


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


# ===========================================================================
# MASTER DATA: PACKAGES, USERS, HAPUS JAMAAH
# ===========================================================================
@app.get("/api/packages")
async def packages_list(user=Depends(authenticate_token)):
    return db.query_all(
        "SELECT p.*, (SELECT COUNT(*) FROM jamaah WHERE package_type = p.name "
        "AND status NOT IN ('Cancelled')) as filled FROM packages p ORDER BY p.departure_date ASC",
        (),
    )


@app.post("/api/packages")
async def packages_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    g = body.get
    last_id, _ = db.execute(
        "INSERT INTO packages (name, price, departure_date, duration, quota, "
        "price_quad, price_triple, price_double, default_commission_fee) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (g("name"), g("price"), g("departure_date"), g("duration"),
         int(g("quota")) if g("quota") else 45, g("price_quad"), g("price_triple"), g("price_double"),
         int(g("default_commission_fee")) if g("default_commission_fee") else 0),
    )
    log_action(user, "CREATE_PACKAGE", f"Menambah paket baru: {g('name')}")
    notify("data_updated", "package")
    return {"id": last_id, "message": "Paket berhasil ditambahkan."}


@app.put("/api/packages/{pid}")
async def packages_update(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    g = body.get
    pkg = db.query_one("SELECT * FROM packages WHERE id = ?", (pid,))
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan")
    # `price` (harga umum lama, sebelum ada rincian per tipe kamar) sengaja TIDAK disentuh --
    # tidak ada field untuk itu di form Edit Paket, jadi kalau ikut ditulis ulang di sini akan
    # selalu jadi NULL (bug yang pernah terjadi & sudah diperbaiki: lihat commit ini).
    db.execute(
        "UPDATE packages SET name = ?, departure_date = ?, duration = ?, quota = ?, "
        "price_quad = ?, price_triple = ?, price_double = ?, default_commission_fee = ? WHERE id = ?",
        (
            g("name"), g("departure_date"), g("duration"),
            int(g("quota")) if g("quota") else 45, g("price_quad"), g("price_triple"), g("price_double"),
            int(g("default_commission_fee")) if g("default_commission_fee") else 0, pid,
        ),
    )
    log_action(user, "UPDATE_PACKAGE", f"Mengubah data paket: {pkg['name']}")
    notify("data_updated", "package")
    return {"message": "Paket berhasil diperbarui."}


@app.delete("/api/packages/{pid}")
async def packages_delete(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin")
    pkg = db.query_one("SELECT name FROM packages WHERE id = ?", (pid,))
    db.execute("DELETE FROM packages WHERE id = ?", (pid,))
    if pkg:
        log_action(user, "DELETE_PACKAGE", f"Menghapus paket: {pkg['name']}")
    notify("data_updated", "package")
    return {"message": "Paket dihapus."}


@app.put("/api/packages/{pid}/staff")
async def packages_staff(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    db.execute(
        "UPDATE packages SET tour_leader = ?, mutawwif = ? WHERE id = ?",
        (body.get("tour_leader"), body.get("mutawwif"), pid),
    )
    log_action(user, "UPDATE_PACKAGE_STAFF", f"Menugaskan petugas lapangan paket ID {pid}")
    notify("data_updated", "package")
    return {"message": "Petugas lapangan berhasil ditugaskan."}


# ===========================================================================
# DOKUMEN OPERASIONAL PER PAKET: Manifest, Roomlist (+ pengelompokan kamar), Absensi
# ===========================================================================
def _package_jamaah_rows(pid):
    pkg = db.query_one("SELECT * FROM packages WHERE id = ?", (pid,))
    if not pkg:
        raise HTTPException(status_code=404, detail="Paket tidak ditemukan")
    rows = db.query_all(
        "SELECT * FROM jamaah WHERE package_type = ? AND status NOT IN ('Cancelled') ORDER BY name ASC",
        (pkg["name"],),
    )
    return pkg, rows


def _company_and_logo():
    company = {
        "legal_name": get_setting("company_legal_name", "Umar Travel"),
        "address": get_setting("company_address", ""),
        "email": get_setting("company_email", ""),
        "website": get_setting("company_website", ""),
    }
    logo_path = None
    logo_url = get_setting("logo_url")
    if logo_url:
        candidate = os.path.join(PUBLIC_DIR, logo_url.lstrip("/"))
        if os.path.isfile(candidate):
            logo_path = candidate
    return company, logo_path


@app.get("/api/packages/{pid}/room-groups")
async def package_room_groups(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    _pkg, rows = _package_jamaah_rows(pid)
    suggested = auto_group_rooms(rows)
    result = []
    for r in rows:
        r = dict(r)
        r["suggested_room_number"] = suggested.get(r["id"])
        result.append(r)
    return result


@app.put("/api/packages/{pid}/room-groups")
async def package_room_groups_save(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    for a in body.get("assignments") or []:
        db.execute(
            "UPDATE jamaah SET room_number = ? WHERE id = ?",
            (a.get("room_number"), a.get("jamaah_id")),
        )
    log_action(user, "UPDATE_ROOM_GROUPS", f"Memperbarui pengelompokan kamar paket ID {pid}")
    notify("data_updated", "jamaah")
    return {"message": "Pengelompokan kamar berhasil disimpan."}


@app.get("/api/packages/{pid}/manifest-pdf")
async def package_manifest_pdf(pid: int, user=Depends(authenticate_file_token)):
    require_role(user, "admin", "ops")
    pkg, rows = _package_jamaah_rows(pid)
    company, logo_path = _company_and_logo()
    pdf_bytes = build_manifest_pdf(pkg, rows, company=company, logo_path=logo_path)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Manifest-{pkg['name'].replace(' ', '-')}.pdf"},
    )


@app.get("/api/packages/{pid}/roomlist-pdf")
async def package_roomlist_pdf(pid: int, user=Depends(authenticate_file_token)):
    require_role(user, "admin", "ops")
    pkg, rows = _package_jamaah_rows(pid)
    company, logo_path = _company_and_logo()
    pdf_bytes = build_roomlist_pdf(pkg, rows, company=company, logo_path=logo_path)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Roomlist-{pkg['name'].replace(' ', '-')}.pdf"},
    )


@app.get("/api/packages/{pid}/absensi-pdf")
async def package_absensi_pdf(pid: int, user=Depends(authenticate_file_token)):
    require_role(user, "admin", "ops")
    pkg, rows = _package_jamaah_rows(pid)
    company, logo_path = _company_and_logo()
    pdf_bytes = build_absensi_pdf(pkg, rows, company=company, logo_path=logo_path)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename=Absensi-{pkg['name'].replace(' ', '-')}.pdf"},
    )


@app.get("/api/users")
async def users_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    return db.query_all(
        "SELECT id, username, name, role, base_salary, phone, personal_email, address, nik, "
        "birth_date, photo_url, last_education, education_major, education_institution FROM users", ()
    )


@app.get("/api/users/directory")
async def users_directory(user=Depends(authenticate_token)):
    # Versi ringan dari /api/users (tanpa data HR sensitif) -- dipakai untuk menampilkan
    # foto profil di tempat yang menyebut nama karyawan (mis. PIC Live Chat), yang bisa
    # diakses semua role, bukan cuma admin/management.
    return db.query_all("SELECT name, photo_url FROM users", ())


# ===========================================================================
# PROFIL SAYA: setiap karyawan mendata dirinya sendiri (kontak, foto, pendidikan)
# -- terpisah dari data akun (username/role/gaji) yang tetap hanya admin yang atur.
# Ganti password: karyawan bisa ganti sendiri (wajib password lama), Admin bisa
# reset password siapa saja tanpa password lama (lihat /api/users/{uid}/reset-password).
# ===========================================================================
@app.get("/api/users/me")
async def users_me(user=Depends(authenticate_token)):
    row = db.query_one(
        "SELECT id, username, name, role, phone, personal_email, address, nik, birth_date, "
        "photo_url, last_education, education_major, education_institution FROM users WHERE id = ?",
        (user["id"],),
    )
    if not row:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    return row


@app.put("/api/users/me/profile")
async def users_me_profile_update(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    g = body.get
    db.execute(
        "UPDATE users SET phone = ?, personal_email = ?, address = ?, nik = ?, birth_date = ?, "
        "last_education = ?, education_major = ?, education_institution = ? WHERE id = ?",
        (
            g("phone"), g("personal_email"), g("address"), g("nik"), g("birth_date"),
            g("last_education"), g("education_major"), g("education_institution"), user["id"],
        ),
    )
    log_action(user, "UPDATE_OWN_PROFILE", "Memperbarui data profil diri")
    return {"message": "Profil berhasil disimpan."}


@app.post("/api/users/me/photo")
async def users_me_photo_upload(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    file_base64 = body.get("fileBase64", "")
    ext = (body.get("ext") or "jpg").lower().lstrip(".")
    if ext not in ("png", "jpg", "jpeg", "webp"):
        raise HTTPException(status_code=400, detail="Format foto harus gambar (png/jpg/webp).")
    if not file_base64:
        raise HTTPException(status_code=400, detail="Tidak ada file foto.")

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    file_name = f"profile_{user['id']}_{int(asyncio.get_event_loop().time() * 1000)}.{ext}"
    b64 = file_base64.split(",")[1] if "," in file_base64 else file_base64
    try:
        with open(os.path.join(UPLOAD_DIR, file_name), "wb") as f:
            f.write(base64.b64decode(b64))
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="Data foto tidak valid.")

    photo_url = f"/uploads/{file_name}"
    db.execute("UPDATE users SET photo_url = ? WHERE id = ?", (photo_url, user["id"]))
    log_action(user, "UPDATE_OWN_PROFILE", "Mengunggah foto profil baru")
    return {"message": "Foto profil berhasil diperbarui.", "photo_url": photo_url}


@app.put("/api/users/me/password")
async def users_me_password_change(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    old_password = body.get("old_password") or ""
    new_password = body.get("new_password") or ""
    if not new_password or len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password baru minimal 6 karakter.")

    row = db.query_one("SELECT password FROM users WHERE id = ?", (user["id"],))
    if not row or not verify_password(old_password, row["password"]):
        raise HTTPException(status_code=400, detail="Password lama salah.")

    from auth import hash_password

    db.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(new_password), user["id"]))
    log_action(user, "CHANGE_OWN_PASSWORD", "Mengganti password akun sendiri")
    return {"message": "Password berhasil diganti."}


@app.put("/api/users/{uid}/reset-password")
async def users_reset_password(uid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    new_password = body.get("new_password") or ""
    if not new_password or len(new_password) < 6:
        raise HTTPException(status_code=400, detail="Password baru minimal 6 karakter.")

    target = db.query_one("SELECT username FROM users WHERE id = ?", (uid,))
    if not target:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")

    from auth import hash_password

    db.execute("UPDATE users SET password = ? WHERE id = ?", (hash_password(new_password), uid))
    log_action(user, "RESET_USER_PASSWORD", f"Reset password untuk akun: {target['username']}")
    return {"message": "Password berhasil direset."}


@app.post("/api/users")
async def users_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    from auth import hash_password

    g = body.get
    try:
        hashed = hash_password(g("password") or "")
        db.execute(
            "INSERT INTO users (username, password, name, role, base_salary) VALUES (?, ?, ?, ?, ?)",
            (g("username"), hashed, g("name"), g("role"), g("base_salary") or 0),
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="Username ini sudah digunakan oleh karyawan lain.")
        raise HTTPException(status_code=500, detail=str(e))
    log_action(user, "CREATE_USER", f"Membuat akun karyawan baru: {g('username')} (role: {g('role')})")
    notify("data_updated", "user")
    return {"message": "User berhasil ditambahkan."}


@app.put("/api/users/{uid}")
async def users_update(uid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin")
    g = body.get
    target = db.query_one("SELECT username FROM users WHERE id = ?", (uid,))
    if not target:
        raise HTTPException(status_code=404, detail="User tidak ditemukan")
    try:
        db.execute(
            "UPDATE users SET username = ?, name = ?, role = ?, base_salary = ?, phone = ?, "
            "personal_email = ?, address = ?, nik = ?, birth_date = ?, last_education = ?, "
            "education_major = ?, education_institution = ? WHERE id = ?",
            (
                g("username"), g("name"), g("role"), g("base_salary") or 0, g("phone"),
                g("personal_email"), g("address"), g("nik"), g("birth_date"), g("last_education"),
                g("education_major"), g("education_institution"), uid,
            ),
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="Username ini sudah digunakan oleh karyawan lain.")
        raise HTTPException(status_code=500, detail=str(e))
    log_action(user, "UPDATE_USER", f"Mengubah data karyawan: {target['username']} -> {g('username')}")
    notify("data_updated", "user")
    return {"message": "Data karyawan berhasil diperbarui."}


@app.delete("/api/users/{uid}")
async def users_delete(uid: int, user=Depends(authenticate_token)):
    require_role(user, "admin")
    if uid == user["id"]:
        raise HTTPException(
            status_code=400, detail="Akses Ditolak: Anda tidak dapat menghapus akun Anda sendiri."
        )
    target = db.query_one("SELECT username, role FROM users WHERE id = ?", (uid,))
    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    if target:
        log_action(user, "DELETE_USER", f"Menghapus akun karyawan: {target['username']} (role: {target['role']})")
    notify("data_updated", "user")
    return {"message": "User dihapus."}


@app.delete("/api/jamaah/{jid}")
async def jamaah_delete(jid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    row = db.query_one("SELECT status FROM jamaah WHERE id = ?", (jid,))
    if not row:
        raise HTTPException(status_code=404, detail="Data tidak ditemukan")
    if row["status"] != "Lead - Follow Up" and user["role"] != "admin":
        raise HTTPException(
            status_code=400,
            detail="Hanya status Lead yang boleh dihapus permanen. Gunakan fitur Batalkan (Cancel) untuk data Booking.",
        )
    db.execute("DELETE FROM jamaah WHERE id = ?", (jid,))
    log_action(user,"DELETE_JAMAAH", f"Menghapus data Lead ID: {jid}")
    notify("data_updated", "jamaah")
    return {"message": "Data Leads dihapus permanen."}


@app.put("/api/jamaah/{jid}/cancel")
async def jamaah_cancel(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    reason = body.get("reason")
    if not reason:
        raise HTTPException(
            status_code=400, detail="Alasan pembatalan wajib diisi untuk keperluan Audit."
        )
    db.execute(
        "UPDATE jamaah SET pipeline_stage = 'Cancelled', cancel_reason = ? WHERE id = ?", (reason, jid)
    )
    sync_status_mirror(jid)
    log_action(user,"CANCEL_JAMAAH", f"Membatalkan booking Jamaah ID: {jid}. Alasan: {reason}")
    notify("data_updated", "jamaah")
    return {"message": "Booking Jamaah berhasil dibatalkan."}


# --- Catatan internal antar divisi ---
@app.get("/api/jamaah/{jid}/comments")
async def jamaah_comments(jid: int, user=Depends(authenticate_token)):
    return db.query_all(
        "SELECT * FROM jamaah_comments WHERE jamaah_id = ? ORDER BY created_at ASC", (jid,)
    )


@app.post("/api/jamaah/{jid}/comments")
async def jamaah_comment_add(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    db.execute(
        "INSERT INTO jamaah_comments (jamaah_id, user_name, comment) VALUES (?, ?, ?)",
        (jid, user["name"], body.get("comment")),
    )
    return {"message": "Catatan internal berhasil ditambahkan."}


# ===========================================================================
# CRM SALES: AKTIVITAS LEAD, FOLLOW-UP, KINERJA
# ===========================================================================
@app.get("/api/jamaah/{jid}/activities")
async def lead_activities_list(jid: int, user=Depends(authenticate_token)):
    assert_jamaah_access(jid, user)
    return db.query_all(
        "SELECT * FROM lead_activities WHERE jamaah_id = ? ORDER BY created_at DESC", (jid,)
    )


@app.get("/api/jamaah/{jid}/payments")
async def jamaah_payments_history(jid: int, user=Depends(authenticate_token)):
    assert_jamaah_access(jid, user)
    return db.query_all(
        "SELECT amount, created_at FROM transactions "
        "WHERE reference_id = ? AND category = 'payment' AND type = 'income' "
        "ORDER BY created_at ASC",
        (jid,),
    )


# ===========================================================================
# REFUND: CS ajukan -> Manajemen setuju/tolak -> Finance cairkan dana.
# Sengaja terpisah dari Batalkan Booking (pipeline_stage tidak diubah di sini)
# agar dua aksi tsb tidak saling tercampur.
# ===========================================================================
@app.get("/api/jamaah/{jid}/refund-requests")
async def jamaah_refund_requests_list(jid: int, user=Depends(authenticate_token)):
    assert_jamaah_access(jid, user)
    return db.query_all(
        "SELECT * FROM refund_requests WHERE jamaah_id = ? ORDER BY requested_at ASC", (jid,)
    )


@app.post("/api/jamaah/{jid}/refund-requests")
async def jamaah_refund_request_create(
    jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    require_role(user, "admin", "sales")
    assert_jamaah_access(jid, user)

    amount = parse_int(body.get("amount"), "nominal refund")
    reason = (body.get("reason") or "").strip()
    if not reason:
        raise HTTPException(status_code=400, detail="Alasan refund wajib diisi.")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nominal refund harus lebih dari 0.")

    jamaah = db.query_one("SELECT name, paid_amount FROM jamaah WHERE id = ?", (jid,))
    if not jamaah:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")

    already_requested = db.query_one(
        "SELECT COALESCE(SUM(amount), 0) as total FROM refund_requests "
        "WHERE jamaah_id = ? AND status IN ('Pending', 'Disetujui')",
        (jid,),
    )["total"]
    if amount > (jamaah["paid_amount"] or 0) - already_requested:
        raise HTTPException(
            status_code=400,
            detail="Nominal refund melebihi sisa dana yang bisa direfund (sudah dikurangi pengajuan lain yang masih berjalan).",
        )

    cancel_booking = 1 if body.get("cancel_booking") else 0
    db.execute(
        "INSERT INTO refund_requests (jamaah_id, amount, reason, requested_by, cancel_booking) VALUES (?, ?, ?, ?, ?)",
        (jid, amount, reason, user["name"], cancel_booking),
    )
    log_action(user, "REQUEST_REFUND", f"Mengajukan refund Rp {amount} untuk {jamaah['name']}: {reason}")
    notify("data_updated", "refund_request")
    return {"message": "Pengajuan refund berhasil dikirim, menunggu persetujuan manajemen."}


@app.get("/api/refund-requests")
async def refund_requests_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "management", "finance")
    return db.query_all(
        "SELECT r.*, j.name as jamaah_name, j.nik as jamaah_nik FROM refund_requests r "
        "LEFT JOIN jamaah j ON r.jamaah_id = j.id ORDER BY r.requested_at DESC"
    )


@app.put("/api/refund-requests/{rid}/review")
async def refund_request_review(
    rid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)
):
    require_role(user, "admin", "management")
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Aksi harus 'approve' atau 'reject'.")

    r = db.query_one("SELECT * FROM refund_requests WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Pengajuan refund tidak ditemukan")
    if r["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Pengajuan ini berstatus '{r['status']}', tidak bisa direview ulang.")

    note = (body.get("note") or "").strip()
    if action == "reject" and not note:
        raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")

    new_status = "Disetujui" if action == "approve" else "Ditolak"
    db.execute(
        "UPDATE refund_requests SET status = ?, approved_by = ?, approved_at = CURRENT_TIMESTAMP, "
        "reject_reason = ? WHERE id = ?",
        (new_status, user["name"], note if action == "reject" else None, rid),
    )
    log_action(
        user, "APPROVE_REFUND" if action == "approve" else "REJECT_REFUND",
        f"{new_status} pengajuan refund #{rid} (Rp {r['amount']})",
    )
    notify("data_updated", "refund_request")
    return {"message": f"Pengajuan refund berhasil di-{'setujui' if action == 'approve' else 'tolak'}."}


@app.put("/api/refund-requests/{rid}/disburse")
async def refund_request_disburse(rid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")

    r = db.query_one("SELECT * FROM refund_requests WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Pengajuan refund tidak ditemukan")
    if r["status"] != "Disetujui":
        raise HTTPException(
            status_code=400,
            detail="Hanya pengajuan berstatus 'Disetujui' (oleh manajemen) yang bisa dicairkan.",
        )

    jamaah = db.query_one("SELECT * FROM jamaah WHERE id = ?", (r["jamaah_id"],))
    if not jamaah:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")

    new_paid = (jamaah["paid_amount"] or 0) - r["amount"]
    total = jamaah["total_price"] or 0
    if total > 0 and new_paid >= total:
        new_payment = "Lunas"
    elif new_paid > 0:
        new_payment = "DP"
    else:
        new_payment = "Unpaid"

    last_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, reference_id, package_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("expense", "refund", r["amount"], f"Refund: {jamaah['name']}", jamaah["id"], jamaah["package_type"]),
    )
    db.execute(
        "UPDATE jamaah SET paid_amount = ?, payment_status = ? WHERE id = ?",
        (new_paid, new_payment, jamaah["id"]),
    )
    # Pembatalan booking (bila dicentang saat pengajuan) baru dijalankan di titik ini --
    # saat uang benar-benar keluar dari Buku Kas, bukan sekadar disetujui manajemen --
    # supaya status booking selalu mencerminkan kondisi uang yang sebenarnya.
    if r["cancel_booking"] and jamaah["pipeline_stage"] != "Cancelled":
        db.execute(
            "UPDATE jamaah SET pipeline_stage = 'Cancelled', cancel_reason = ? WHERE id = ?",
            (r["reason"], jamaah["id"]),
        )
    sync_status_mirror(jamaah["id"])
    db.execute(
        "UPDATE refund_requests SET status = 'Dicairkan', disbursed_by = ?, disbursed_at = CURRENT_TIMESTAMP, "
        "transaction_id = ? WHERE id = ?",
        (user["name"], last_id, rid),
    )
    log_action(
        user, "DISBURSE_REFUND",
        f"Mencairkan refund Rp {r['amount']} untuk {jamaah['name']}"
        + (" (sekaligus membatalkan booking)" if r["cancel_booking"] else ""),
    )
    notify("data_updated", "refund_request")
    notify("data_updated", "transaction")
    notify("data_updated", "jamaah")
    return {"message": "Refund berhasil dicairkan dan tercatat di Buku Kas."}


@app.post("/api/jamaah/{jid}/activities")
async def lead_activity_add(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    assert_jamaah_access(jid, user)
    activity_type = body.get("activity_type") or "Catatan"
    note = body.get("note")
    next_follow_up = body.get("next_follow_up") or None
    db.execute(
        "INSERT INTO lead_activities (jamaah_id, user_name, activity_type, note, next_follow_up) "
        "VALUES (?, ?, ?, ?, ?)",
        (jid, user["name"], activity_type, note, next_follow_up),
    )
    # Denormalisasi ke jamaah agar query follow-up & 'kontak terakhir' cepat.
    db.execute(
        "UPDATE jamaah SET next_follow_up = ?, last_contact = CURRENT_TIMESTAMP WHERE id = ?",
        (next_follow_up, jid),
    )
    log_action(user,"LEAD_ACTIVITY", f"{activity_type} ke Jamaah ID {jid}")
    notify("data_updated", "jamaah")
    return {"message": "Aktivitas follow-up berhasil dicatat."}


@app.get("/api/followups")
async def followups(user=Depends(authenticate_token)):
    """Daftar lead yang dijadwalkan follow-up (sales: miliknya; admin: semua)."""
    q = (
        "SELECT j.id, j.name, j.phone, j.status, j.package_type, j.next_follow_up, "
        "j.last_contact, j.total_price, j.paid_amount "
        "FROM jamaah j WHERE j.next_follow_up IS NOT NULL AND j.next_follow_up != '' "
        "AND j.status NOT IN ('Cancelled', 'On Trip')"
    )
    params = []
    if user["role"] == "sales":
        q += " AND j.sales_id = ?"
        params.append(user["id"])
    q += " ORDER BY j.next_follow_up ASC"
    return db.query_all(q, tuple(params))


def _sales_stats(sales_id):
    base = " FROM jamaah WHERE sales_id = ?"
    p = (sales_id,)
    total = db.query_one("SELECT COUNT(*) c" + base, p)["c"]
    converted = db.query_one(
        "SELECT COUNT(*) c" + base + " AND status IN ('Lunas', 'Visa Approved', 'On Trip')", p
    )["c"]
    active = db.query_one(
        "SELECT COUNT(*) c" + base + " AND status NOT IN ('Cancelled', 'Lunas', 'Visa Approved', 'On Trip')", p
    )["c"]
    paid = db.query_one("SELECT COALESCE(SUM(paid_amount), 0) s" + base, p)["s"]
    this_month = db.query_one(
        "SELECT COUNT(*) c" + base + " AND strftime('%Y-%m', created_at) = strftime('%Y-%m', 'now')", p
    )["c"]
    due = db.query_one(
        "SELECT COUNT(*) c" + base + " AND next_follow_up IS NOT NULL AND next_follow_up != '' "
        "AND date(next_follow_up) <= date('now') AND status NOT IN ('Cancelled', 'On Trip')", p
    )["c"]
    agents_recruited = db.query_one(
        "SELECT COUNT(*) c FROM agents WHERE handler_cs_id = ?", (sales_id,)
    )["c"]
    return {
        "total_leads": total,
        "converted": converted,
        "active": active,
        "total_paid": paid or 0,
        "this_month": this_month,
        "due_followups": due,
        "conversion_rate": round(converted * 100.0 / total, 1) if total else 0,
        "agents_recruited": agents_recruited,
    }


@app.get("/api/sales/performance")
async def sales_performance(user=Depends(authenticate_token)):
    if user["role"] == "sales":
        return {"self": {**_sales_stats(user["id"]), "name": user["name"]}}
    if user["role"] == "admin":
        # Leaderboard semua sales
        sales_users = db.query_all("SELECT id, name FROM users WHERE role = 'sales'", ())
        board = [{**_sales_stats(s["id"]), "name": s["name"], "id": s["id"]} for s in sales_users]
        board.sort(key=lambda x: x["converted"], reverse=True)
        return {"leaderboard": board}
    raise HTTPException(status_code=403, detail="Akses Ditolak")


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


@app.get("/api/inventory")
async def inventory_list(user=Depends(authenticate_token)):
    return db.query_all("SELECT * FROM inventory ORDER BY item_name ASC", ())


@app.post("/api/inventory")
async def inventory_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    g = body.get
    item_name = (g("item_name") or "").strip()
    if not item_name:
        raise HTTPException(status_code=400, detail="Nama barang wajib diisi.")
    # Inventory kini khusus Barang Jamaah -- Aset Perusahaan punya tabel & alur
    # sendiri (lihat /api/company-assets) karena butuh pelacakan per-unit/pemegang.
    try:
        db.execute(
            "INSERT INTO inventory (item_name, stock, category, min_stock_threshold) VALUES (?, ?, ?, ?)",
            (item_name, int(g("stock") or 0), "Barang Jamaah", int(g("min_stock_threshold") or 20)),
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="Nama barang ini sudah ada di daftar inventory.")
        raise HTTPException(status_code=500, detail=str(e))
    log_action(user, "CREATE_INVENTORY_ITEM", f"Menambah item inventory baru: {item_name}")
    notify("data_updated", "inventory")
    return {"message": "Item inventory baru berhasil ditambahkan."}


@app.put("/api/inventory/{iid}")
async def inventory_update(iid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    g = body.get
    item = db.query_one("SELECT * FROM inventory WHERE id = ?", (iid,))
    if not item:
        raise HTTPException(status_code=404, detail="Item inventory tidak ditemukan")
    db.execute(
        "UPDATE inventory SET item_name = ?, min_stock_threshold = ? WHERE id = ?",
        (g("item_name") or item["item_name"], int(g("min_stock_threshold") or 20), iid),
    )
    log_action(user, "UPDATE_INVENTORY_ITEM", f"Mengubah item inventory: {item['item_name']}")
    notify("data_updated", "inventory")
    return {"message": "Item inventory berhasil diperbarui."}


@app.delete("/api/inventory/{iid}")
async def inventory_delete(iid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    item = db.query_one("SELECT * FROM inventory WHERE id = ?", (iid,))
    if not item:
        raise HTTPException(status_code=404, detail="Item inventory tidak ditemukan")
    db.execute("DELETE FROM inventory WHERE id = ?", (iid,))
    log_action(user, "DELETE_INVENTORY_ITEM", f"Menghapus item inventory: {item['item_name']}")
    notify("data_updated", "inventory")
    return {"message": "Item inventory berhasil dihapus."}


# ===========================================================================
# ASET PERUSAHAAN (laptop, HP, dll) -- per-unit, terpisah dari stok gudang
# barang jamaah. Tiap unit melacak siapa pemegangnya sekarang.
# ===========================================================================
ASSET_CONDITIONS = ("Baik", "Rusak Ringan", "Rusak Berat", "Hilang")


@app.get("/api/company-assets")
async def company_assets_list(user=Depends(authenticate_token)):
    return db.query_all("SELECT * FROM company_assets ORDER BY item_name ASC", ())


@app.get("/api/company-assets/{aid}/history")
async def company_assets_history(aid: int, user=Depends(authenticate_token)):
    return db.query_all(
        "SELECT * FROM asset_transfer_history WHERE asset_id = ? ORDER BY transferred_at DESC", (aid,)
    )


def _record_asset_transfer(asset_id, from_holder, to_holder, transferred_by):
    """Catat satu baris linimasa perpindahan tangan -- dipanggil dari create (kalau
    langsung ada pemegang awal), transfer, dan edit (kalau assigned_to ikut berubah)."""
    db.execute(
        "INSERT INTO asset_transfer_history (asset_id, from_holder, to_holder, transferred_by) "
        "VALUES (?, ?, ?, ?)",
        (asset_id, from_holder, to_holder, transferred_by),
    )


@app.post("/api/company-assets")
async def company_assets_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    g = body.get
    item_name = (g("item_name") or "").strip()
    if not item_name:
        raise HTTPException(status_code=400, detail="Nama aset wajib diisi.")
    condition = g("condition") or "Baik"
    if condition not in ASSET_CONDITIONS:
        raise HTTPException(status_code=400, detail="Kondisi tidak valid.")
    assigned_to = (g("assigned_to") or "").strip() or None
    asset_code = db.next_asset_code()
    new_id, _ = db.execute(
        "INSERT INTO company_assets (item_name, serial_number, condition, assigned_to, assigned_at, notes, asset_code) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            item_name, g("serial_number"), condition, assigned_to,
            datetime.datetime.now().isoformat() if assigned_to else None, g("notes"), asset_code,
        ),
    )
    if assigned_to:
        _record_asset_transfer(new_id, None, assigned_to, user["name"])
    log_action(user, "CREATE_ASSET", f"Menambah aset perusahaan baru: {item_name}")
    notify("data_updated", "company_asset")
    return {"message": "Aset perusahaan baru berhasil ditambahkan."}


@app.put("/api/company-assets/{aid}")
async def company_assets_update(aid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    g = body.get
    asset = db.query_one("SELECT * FROM company_assets WHERE id = ?", (aid,))
    if not asset:
        raise HTTPException(status_code=404, detail="Aset tidak ditemukan")
    condition = g("condition") or "Baik"
    if condition not in ASSET_CONDITIONS:
        raise HTTPException(status_code=400, detail="Kondisi tidak valid.")
    new_assigned = (g("assigned_to") or "").strip() or None
    # assigned_at cuma diperbarui kalau pemegangnya benar-benar berubah lewat form
    # Edit ini -- perpindahan normal sebaiknya lewat aksi "Pindah Tangan" tersendiri.
    assigned_at = asset["assigned_at"]
    if new_assigned != asset["assigned_to"]:
        assigned_at = datetime.datetime.now().isoformat() if new_assigned else None
        _record_asset_transfer(aid, asset["assigned_to"], new_assigned, user["name"])
    db.execute(
        "UPDATE company_assets SET item_name = ?, serial_number = ?, condition = ?, "
        "assigned_to = ?, assigned_at = ?, notes = ? WHERE id = ?",
        (
            g("item_name") or asset["item_name"], g("serial_number"), condition,
            new_assigned, assigned_at, g("notes"), aid,
        ),
    )
    log_action(user, "UPDATE_ASSET", f"Mengubah aset perusahaan: {asset['item_name']}")
    notify("data_updated", "company_asset")
    return {"message": "Aset perusahaan berhasil diperbarui."}


@app.put("/api/company-assets/{aid}/transfer")
async def company_assets_transfer(aid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    asset = db.query_one("SELECT * FROM company_assets WHERE id = ?", (aid,))
    if not asset:
        raise HTTPException(status_code=404, detail="Aset tidak ditemukan")
    # Nilai kosong = dikembalikan ke gudang/belum ditugaskan -- dropdown pemilihan di
    # frontend sudah punya opsi eksplisit untuk itu, jadi tidak perlu lagi dipaksa lewat
    # Edit Aset seperti sebelumnya.
    new_holder = (body.get("assigned_to") or "").strip() or None
    db.execute(
        "UPDATE company_assets SET assigned_to = ?, assigned_at = ? WHERE id = ?",
        (new_holder, datetime.datetime.now().isoformat() if new_holder else None, aid),
    )
    _record_asset_transfer(aid, asset["assigned_to"], new_holder, user["name"])
    log_action(
        user, "TRANSFER_ASSET",
        f"Memindahkan aset {asset['item_name']} dari {asset['assigned_to'] or 'gudang'} ke {new_holder or 'gudang'}",
    )
    notify("data_updated", "company_asset")
    return {"message": f"Aset berhasil dipindahkan ke {new_holder or 'gudang'}."}


@app.delete("/api/company-assets/{aid}")
async def company_assets_delete(aid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    asset = db.query_one("SELECT * FROM company_assets WHERE id = ?", (aid,))
    if not asset:
        raise HTTPException(status_code=404, detail="Aset tidak ditemukan")
    db.execute("DELETE FROM company_assets WHERE id = ?", (aid,))
    log_action(user, "DELETE_ASSET", f"Menghapus aset perusahaan: {asset['item_name']}")
    notify("data_updated", "company_asset")
    return {"message": "Aset perusahaan berhasil dihapus."}


@app.post("/api/jamaah/{jid}/inventory")
async def jamaah_inventory(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    items = body.get("items")
    if not items or not isinstance(items, list) or len(items) == 0:
        raise HTTPException(status_code=400, detail="Tidak ada barang yang dipilih")

    row = db.query_one("SELECT paid_amount, total_price FROM jamaah WHERE id = ?", (jid,))
    if not row:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")

    # Ambang minimum DP kini dapat dikonfigurasi via Pengaturan (default 75%).
    try:
        min_pct = float(get_setting("equipment_min_dp_percent", "75"))
    except (TypeError, ValueError):
        min_pct = 75.0
    threshold = (row["total_price"] or 0) * min_pct / 100.0
    if (row["paid_amount"] or 0) < threshold and user["role"] != "admin":
        pct_label = int(min_pct) if float(min_pct).is_integer() else min_pct
        raise HTTPException(
            status_code=400,
            detail=f"Gagal! SOP GERBANG: Jamaah harus melunasi minimal {pct_label}% tagihan sebelum "
            "pengambilan fisik perlengkapan.",
        )

    success = 0
    for item_name in items:
        _, changed = db.execute(
            "UPDATE inventory SET stock = stock - 1 WHERE item_name = ? AND stock > 0", (item_name,)
        )
        if changed > 0:
            db.execute(
                "INSERT INTO jamaah_inventory (jamaah_id, item_name) VALUES (?, ?)", (jid, item_name)
            )
            success += 1
    log_action(user,"HANDOVER", f"Serah terima {success} logistik ke Jamaah ID {jid}")
    notify("data_updated", "inventory")
    notify("data_updated", "jamaah")
    return {"message": f"Berhasil menyerahkan {success} item perlengkapan."}


@app.put("/api/jamaah/{jid}/depart")
async def jamaah_depart(jid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    db.execute("UPDATE jamaah SET trip_status = 'OnTrip' WHERE id = ?", (jid,))
    db.execute("UPDATE jamaah SET status = 'On Trip' WHERE id = ?", (jid,))
    log_action(user,"DEPARTURE", f"Jamaah ID {jid} diberangkatkan (On Trip)")
    notify("data_updated", "jamaah")
    return {"message": "Status jamaah diubah menjadi ON TRIP."}


@app.post("/api/inventory/restock")
async def inventory_restock(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    item_name = body.get("item_name")
    qty = body.get("qty")
    cost = body.get("cost")
    db.execute("UPDATE inventory SET stock = stock + ? WHERE item_name = ?", (int(qty), item_name))

    if cost and int(cost) > 0:
        db.execute(
            "INSERT INTO transactions (type, category, amount, description) VALUES (?, ?, ?, ?)",
            ("expense", "operational", int(cost), f"Pembelian Logistik Gudang: {qty}x {item_name}"),
        )
        log_action(user,"RESTOCK_EXPENSE", f"Beli {qty}x {item_name} seharga Rp {cost}")
        notify("data_updated", "transaction")
    else:
        log_action(user,"RESTOCK", f"Tambah stok {qty}x {item_name}")

    notify("data_updated", "inventory")
    return {"message": f"Stok {item_name} berhasil ditambah."}


# ===========================================================================
# PROCUREMENT & VENDOR
# ===========================================================================
@app.get("/api/procurement")
async def procurement_list(user=Depends(authenticate_token)):
    # Ops ikut bisa lihat (bukan cuma Finance/Management) -- mereka yang di lapangan
    # perlu tahu kontrak vendor apa saja yang aktif untuk koordinasi logistik. Membuat
    # kontrak baru (procurement_create) tetap dibatasi admin/finance karena menyangkut
    # deposit yang (setelah disetujui) memotong Buku Kas.
    require_role(user, "admin", "finance", "management", "ops")
    return db.query_all("SELECT * FROM procurement ORDER BY created_at DESC", ())


@app.post("/api/procurement")
async def procurement_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    # Alur maker-checker (sama pola dengan Refund/Klaim Komisi): kontrak baru masuk
    # sebagai 'Pending' dan BELUM memotong Kas. Kas baru terpotong setelah Admin/Management
    # menyetujui (procurement_review) dan pembayaran dicatat lewat procurement_payment.
    require_role(user, "admin", "finance")
    g = body.get
    vendor_name = (g("vendor_name") or "").strip()
    if not vendor_name:
        raise HTTPException(status_code=400, detail="Nama vendor wajib diisi.")
    total_stock = parse_int(g("total_stock"), "total kuota/blok")
    total_price = parse_int(g("total_price"), "total nilai kontrak")
    last_id, _ = db.execute(
        "INSERT INTO procurement (vendor_name, service_type, total_stock, total_price, "
        "deposit_paid, package_name, status, created_by) VALUES (?, ?, ?, ?, 0, ?, 'Pending', ?)",
        (vendor_name, g("service_type"), total_stock, total_price, g("package_name") or None, user["name"]),
    )
    log_action(user, "CREATE_PROCUREMENT", f"Mengajukan kontrak vendor: {vendor_name} ({g('service_type')})")
    notify("data_updated", "procurement")
    return {"message": "Kontrak vendor berhasil diajukan, menunggu persetujuan.", "id": last_id}


@app.put("/api/procurement/{pid}")
async def procurement_update(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")
    g = body.get
    row = db.query_one("SELECT * FROM procurement WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kontrak vendor tidak ditemukan")
    vendor_name = (g("vendor_name") or "").strip()
    if not vendor_name:
        raise HTTPException(status_code=400, detail="Nama vendor wajib diisi.")
    total_stock = parse_int(g("total_stock"), "total kuota/blok")
    total_price = parse_int(g("total_price"), "total nilai kontrak")
    if total_price < (row["deposit_paid"] or 0):
        raise HTTPException(
            status_code=400,
            detail=f"Total nilai kontrak tidak boleh lebih kecil dari total yang sudah dibayar "
            f"(Rp {row['deposit_paid']:,}).".replace(",", "."),
        )
    db.execute(
        "UPDATE procurement SET vendor_name = ?, service_type = ?, total_stock = ?, "
        "total_price = ?, package_name = ? WHERE id = ?",
        (vendor_name, g("service_type"), total_stock, total_price, g("package_name") or None, pid),
    )
    log_action(user, "UPDATE_PROCUREMENT", f"Mengubah data kontrak vendor: {row['vendor_name']} -> {vendor_name}")
    notify("data_updated", "procurement")
    return {"message": "Data kontrak vendor berhasil diperbarui."}


@app.delete("/api/procurement/{pid}")
async def procurement_delete(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin")
    row = db.query_one("SELECT * FROM procurement WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kontrak vendor tidak ditemukan")
    if (row["deposit_paid"] or 0) > 0:
        raise HTTPException(
            status_code=400,
            detail="Kontrak ini sudah punya riwayat pembayaran yang memotong Kas -- tidak bisa "
            "dihapus. Batalkan kontrak ini sebagai gantinya.",
        )
    db.execute("DELETE FROM procurement WHERE id = ?", (pid,))
    log_action(user, "DELETE_PROCUREMENT", f"Menghapus kontrak vendor: {row['vendor_name']}")
    notify("data_updated", "procurement")
    return {"message": "Kontrak vendor berhasil dihapus."}


@app.put("/api/procurement/{pid}/review")
async def procurement_review(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Aksi tidak valid.")
    row = db.query_one("SELECT * FROM procurement WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kontrak vendor tidak ditemukan")
    if row["status"] != "Pending":
        raise HTTPException(status_code=400, detail=f"Kontrak ini sudah berstatus '{row['status']}'.")

    if action == "reject":
        reason = (body.get("reason") or "").strip()
        if not reason:
            raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")
        db.execute(
            "UPDATE procurement SET status = 'Ditolak', reviewed_by = ?, "
            "reviewed_at = CURRENT_TIMESTAMP, reject_reason = ? WHERE id = ?",
            (user["name"], reason, pid),
        )
        log_action(user, "REJECT_PROCUREMENT", f"Menolak kontrak vendor {row['vendor_name']}: {reason}")
    else:
        db.execute(
            "UPDATE procurement SET status = 'Aktif', reviewed_by = ?, "
            "reviewed_at = CURRENT_TIMESTAMP WHERE id = ?",
            (user["name"], pid),
        )
        log_action(user, "APPROVE_PROCUREMENT", f"Menyetujui kontrak vendor {row['vendor_name']}")
    notify("data_updated", "procurement")
    return {"message": "Kontrak vendor berhasil " + ("ditolak." if action == "reject" else "disetujui.")}


@app.post("/api/procurement/{pid}/payment")
async def procurement_payment(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")
    row = db.query_one("SELECT * FROM procurement WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kontrak vendor tidak ditemukan")
    if row["status"] != "Aktif":
        raise HTTPException(
            status_code=400,
            detail="Pembayaran hanya bisa dicatat untuk kontrak yang sudah Aktif (disetujui).",
        )
    amount = parse_int(body.get("amount"), "nominal pembayaran")
    if amount <= 0:
        raise HTTPException(status_code=400, detail="Nominal pembayaran harus lebih dari 0.")
    sisa = (row["total_price"] or 0) - (row["deposit_paid"] or 0)
    if amount > sisa:
        raise HTTPException(
            status_code=400, detail=f"Nominal melebihi sisa tagihan (Rp {sisa:,}).".replace(",", "."),
        )
    db.execute("UPDATE procurement SET deposit_paid = deposit_paid + ? WHERE id = ?", (amount, pid))
    db.execute(
        "INSERT INTO transactions (type, category, amount, description, reference_id, package_name) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        ("expense", "procurement_payment", amount,
         f"Pembayaran Vendor {row['service_type']}: {row['vendor_name']} (Blok {row['total_stock']} pax)",
         pid, row["package_name"]),
    )
    log_action(
        user, "PAY_PROCUREMENT",
        f"Mencatat pembayaran Rp {amount:,} ke vendor {row['vendor_name']}".replace(",", "."),
    )
    notify("data_updated", "transaction")
    notify("data_updated", "procurement")
    return {"message": "Pembayaran vendor berhasil dicatat."}


@app.put("/api/procurement/{pid}/status")
async def procurement_set_status(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    new_status = body.get("status")
    if new_status not in ("Selesai", "Dibatalkan"):
        raise HTTPException(status_code=400, detail="Status tidak valid.")
    row = db.query_one("SELECT * FROM procurement WHERE id = ?", (pid,))
    if not row:
        raise HTTPException(status_code=404, detail="Kontrak vendor tidak ditemukan")
    if row["status"] not in ("Aktif", "Pending"):
        raise HTTPException(status_code=400, detail=f"Kontrak ini sudah berstatus '{row['status']}'.")
    db.execute("UPDATE procurement SET status = ? WHERE id = ?", (new_status, pid))
    log_action(
        user, "UPDATE_PROCUREMENT_STATUS",
        f"Mengubah status kontrak vendor {row['vendor_name']} -> {new_status}",
    )
    notify("data_updated", "procurement")
    return {"message": f"Kontrak vendor ditandai sebagai {new_status}."}


@app.get("/api/procurement/{pid}/payments")
async def procurement_payments_history(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management", "ops")
    return db.query_all(
        "SELECT * FROM transactions WHERE reference_id = ? AND category = 'procurement_payment' "
        "ORDER BY created_at ASC", (pid,)
    )


# ===========================================================================
# KEAGENAN, LABA RUGI, VIRTUAL ACCOUNT
# ===========================================================================
@app.post("/api/dummy-payment/va")
async def dummy_va(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    phone = body.get("phone") or "000000000"
    amount = body.get("amount")
    va_number = "988" + phone[-9:]
    return {"message": "Virtual Account BNI (Dummy) berhasil dibuat.", "va_number": va_number, "amount": amount}


@app.get("/api/agents")
async def agents_list(user=Depends(authenticate_token)):
    return db.query_all(
        "SELECT a.*, u.name as handler_cs_name FROM agents a "
        "LEFT JOIN users u ON a.handler_cs_id = u.id ORDER BY a.name ASC", ()
    )


@app.get("/api/agents/performance")
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


@app.post("/api/agents")
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


@app.put("/api/agents/{aid}")
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


# Pengecualian fee komisi per (agen, paket) -- opsional. Kalau tidak ada baris yang
# cocok untuk kombinasi agen+paket tertentu, dipakai fee default milik paket itu sendiri
# (packages.default_commission_fee).
@app.get("/api/agents/{aid}/package-fees")
async def agent_package_fees_list(aid: int, user=Depends(authenticate_token)):
    return db.query_all(
        "SELECT * FROM agent_package_fees WHERE agent_id = ? ORDER BY package_name ASC", (aid,)
    )


@app.post("/api/agents/{aid}/package-fees")
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


@app.delete("/api/agents/{aid}/package-fees/{fee_id}")
async def agent_package_fee_delete(aid: int, fee_id: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "sales")
    db.execute("DELETE FROM agent_package_fees WHERE id = ? AND agent_id = ?", (fee_id, aid))
    log_action(user, "DELETE_AGENT_PACKAGE_FEE", f"Menghapus pengecualian fee komisi (agen ID {aid})")
    notify("data_updated", "agent")
    return {"message": "Pengecualian fee komisi berhasil dihapus."}


# ===========================================================================
# KLAIM KOMISI AGEN: dibuat otomatis (Pending) saat jamaah Lunas -> Manajemen
# setuju/tolak -> Finance cairkan. Pola sama persis dengan Refund (§ hari ini).
# ===========================================================================
@app.get("/api/commission-claims")
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


@app.put("/api/commission-claims/{cid}/review")
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


@app.put("/api/commission-claims/{cid}/disburse")
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


@app.get("/api/agents/{aid}/jamaah")
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


@app.get("/api/agents/expand-report")
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


@app.get("/api/reports/pnl")
async def reports_pnl(user=Depends(authenticate_token)):
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


@app.get("/api/reports/expense-matrix")
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
# KEUANGAN & PENGGAJIAN
# ===========================================================================
@app.get("/api/transactions")
async def transactions_list(user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    return db.query_all("SELECT * FROM transactions ORDER BY created_at DESC", ())


@app.post("/api/transactions/expense")
async def transactions_expense(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")
    g = body.get
    db.execute(
        "INSERT INTO transactions (type, category, amount, description, package_name) VALUES (?, ?, ?, ?, ?)",
        ("expense", g("category"), int(g("amount")), g("description"), g("package_name") or None),
    )
    log_action(user,"EXPENSE", f"Catat pengeluaran Rp {g('amount')} ({g('category')})")
    notify("data_updated", "transaction")
    return {"message": "Pengeluaran operasional berhasil dicatat."}


@app.post("/api/payroll")
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
        db.execute(
            "INSERT INTO transactions (type, category, amount, description, reference_id) VALUES (?, ?, ?, ?, ?)",
            ("expense", "payroll", u["base_salary"], f"Gaji Karyawan: {u['name']}", u["id"]),
        )
        processed += 1
    log_action(user,"PAYROLL", f"Memproses penggajian untuk {processed} karyawan")
    notify("data_updated", "transaction")
    return {"message": f"Penggajian untuk {processed} karyawan berhasil diproses ke buku kas."}


@app.delete("/api/transactions/{tid}")
async def transactions_delete(tid: int, user=Depends(authenticate_token)):
    require_role(user, "admin")

    # FIX (integritas): bila yang dihapus adalah transaksi pembayaran jamaah,
    # kembalikan paid_amount jamaah & hitung ulang statusnya. Tanpa ini, koreksi
    # buku kas membuat saldo jamaah tetap 'Lunas' padahal dananya sudah ditarik.
    tx = db.query_one("SELECT type, category, amount, reference_id FROM transactions WHERE id = ?", (tid,))
    if not tx:
        raise HTTPException(status_code=404, detail="Transaksi tidak ditemukan")

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

    log_action(user,"DELETE_TX", f"Koreksi Admin: Hapus transaksi Buku Kas ID {tid}")
    notify("data_updated", "transaction")
    return {"message": "Transaksi berhasil dihapus (Koreksi Admin)."}


# ===========================================================================
# EXPENSE REPORT (Project label + header/lines, maker-checker, ref + PDF)
# Draft -> Submitted -> (Approved oleh admin/management | Rejected) -> Paid oleh admin/finance
# ===========================================================================
def _line_totals(line):
    net = (line["unit_price_net"] or 0) * (line["qty"] or 0)
    tax = round(net * (line["tax_percent"] or 0) / 100.0)
    return net, tax, net + tax


def _report_totals(lines):
    net = tax = 0
    for ln in lines:
        n, t, _ = _line_totals(ln)
        net += n
        tax += t
    return net, tax, net + tax


def _generate_expense_ref():
    year = datetime.datetime.now().year
    row = db.query_one("SELECT COUNT(*) as c FROM expense_reports WHERE ref LIKE ?", (f"EXP/{year}/%",))
    seq = (row["c"] if row else 0) + 1
    return f"EXP/{year}/{seq:04d}"


def _mask_private_note(report, user):
    """Note 'private' hanya boleh dibaca pemilik, approver-nya, atau admin/management/finance."""
    if report.get("note_visibility") != "private":
        return report
    allowed = user["role"] in ("admin", "management", "finance") or user["id"] in (
        report.get("user_id"), report.get("approver_id"),
    )
    if not allowed:
        report = dict(report)
        report["note"] = None
    return report


def _assert_report_access(report, user, owner_only=False):
    is_owner = report["user_id"] == user["id"]
    is_reviewer = user["role"] in ("admin", "management", "finance")
    if owner_only and not (is_owner or user["role"] == "admin"):
        raise HTTPException(status_code=403, detail="Hanya pemilik laporan atau admin yang dapat mengubahnya.")
    if not (is_owner or is_reviewer):
        raise HTTPException(status_code=403, detail="Akses Ditolak")


@app.get("/api/expense-projects")
async def expense_projects_list(user=Depends(authenticate_token)):
    return db.query_all("SELECT * FROM expense_projects WHERE is_active = 1 ORDER BY name ASC", ())


@app.get("/api/expense-approvers")
async def expense_approvers_list(user=Depends(authenticate_token)):
    """Daftar minimal (id, name, role) untuk dropdown 'User responsible for approval' —
    dibuka untuk semua role karena semua karyawan perlu memilih approver saat membuat
    Expense Report, tanpa perlu akses penuh ke /api/users (admin-only, ada data gaji)."""
    return db.query_all(
        "SELECT id, name, role FROM users WHERE role IN ('admin', 'management') ORDER BY name ASC", ()
    )


@app.post("/api/expense-projects")
async def expense_projects_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nama project wajib diisi.")
    try:
        db.execute(
            "INSERT INTO expense_projects (name, created_by) VALUES (?, ?)", (name, user["name"])
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="Project dengan nama ini sudah ada.")
        raise HTTPException(status_code=500, detail=str(e))
    log_action(user, "CREATE_EXPENSE_PROJECT", f"Menambah project expense: {name}")
    notify("data_updated", "expense_project")
    return {"message": "Project berhasil ditambahkan."}


@app.put("/api/expense-projects/{pid}")
async def expense_projects_update(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nama project wajib diisi.")
    db.execute("UPDATE expense_projects SET name = ? WHERE id = ?", (name, pid))
    log_action(user, "UPDATE_EXPENSE_PROJECT", f"Mengubah nama project expense ID {pid} menjadi: {name}")
    notify("data_updated", "expense_project")
    return {"message": "Project berhasil diperbarui."}


@app.delete("/api/expense-projects/{pid}")
async def expense_projects_deactivate(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    # Nonaktifkan (bukan hard delete) agar Expense Report lama yang mereferensikannya tetap utuh.
    db.execute("UPDATE expense_projects SET is_active = 0 WHERE id = ?", (pid,))
    log_action(user, "DEACTIVATE_EXPENSE_PROJECT", f"Menonaktifkan project expense ID {pid}")
    notify("data_updated", "expense_project")
    return {"message": "Project berhasil dinonaktifkan."}


@app.get("/api/expense-reports")
async def expense_reports_list(user=Depends(authenticate_token)):
    if user["role"] in ("admin", "management", "finance"):
        rows = db.query_all(
            "SELECT r.*, p.name as project_name FROM expense_reports r "
            "LEFT JOIN expense_projects p ON r.project_id = p.id ORDER BY r.created_at DESC", ()
        )
    else:
        rows = db.query_all(
            "SELECT r.*, p.name as project_name FROM expense_reports r "
            "LEFT JOIN expense_projects p ON r.project_id = p.id WHERE r.user_id = ? ORDER BY r.created_at DESC",
            (user["id"],),
        )
    result = []
    for r in rows:
        lines = db.query_all("SELECT * FROM expense_lines WHERE report_id = ?", (r["id"],))
        net, tax, gross = _report_totals(lines)
        r = dict(_mask_private_note(r, user))
        r.update({"amount_net": net, "amount_tax": tax, "amount_gross": gross, "line_count": len(lines)})
        result.append(r)
    return result


@app.post("/api/expense-reports")
async def expense_reports_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Semua role bisa membuat Expense Report baru (status awal: Draft)."""
    g = body.get
    project_id = g("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Project wajib dipilih.")
    project = db.query_one("SELECT * FROM expense_projects WHERE id = ?", (project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="Project tidak ditemukan.")
    approver_id = g("approver_id")
    approver = db.query_one("SELECT name FROM users WHERE id = ?", (approver_id,)) if approver_id else None

    ref = _generate_expense_ref()
    last_id, _ = db.execute(
        "INSERT INTO expense_reports (ref, project_id, user_id, user_name, period_from, period_to, "
        "approver_id, approver_name, note, note_visibility) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ref, project_id, user["id"], user["name"], g("period_from"), g("period_to") or g("period_from"),
         approver_id, approver["name"] if approver else None, g("note") or "",
         g("note_visibility") if g("note_visibility") in ("public", "private") else "public"),
    )
    log_action(user, "CREATE_EXPENSE_REPORT", f"Membuat Expense Report {ref} untuk project {project['name']}")
    notify("data_updated", "expense_report")
    return {"message": "Expense Report berhasil dibuat.", "id": last_id, "ref": ref}


@app.get("/api/expense-reports/{rid}")
async def expense_reports_get(rid: int, user=Depends(authenticate_token)):
    r = db.query_one(
        "SELECT r.*, p.name as project_name FROM expense_reports r "
        "LEFT JOIN expense_projects p ON r.project_id = p.id WHERE r.id = ?", (rid,)
    )
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user)
    lines = db.query_all("SELECT * FROM expense_lines WHERE report_id = ? ORDER BY date ASC, id ASC", (rid,))
    net, tax, gross = _report_totals(lines)
    r = dict(_mask_private_note(r, user))
    r["lines"] = lines
    r["amount_net"] = net
    r["amount_tax"] = tax
    r["amount_gross"] = gross
    return r


@app.put("/api/expense-reports/{rid}")
async def expense_reports_update(rid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)
    if r["status"] != "Draft":
        raise HTTPException(status_code=400, detail="Hanya Expense Report berstatus Draft yang bisa diubah.")

    g = body.get
    approver_id = g("approver_id")
    approver = db.query_one("SELECT name FROM users WHERE id = ?", (approver_id,)) if approver_id else None
    db.execute(
        "UPDATE expense_reports SET period_from = ?, period_to = ?, approver_id = ?, approver_name = ?, "
        "note = ?, note_visibility = ? WHERE id = ?",
        (g("period_from") or r["period_from"], g("period_to") or r["period_to"],
         approver_id or r["approver_id"], approver["name"] if approver else r["approver_name"],
         g("note") if g("note") is not None else r["note"],
         g("note_visibility") if g("note_visibility") in ("public", "private") else r["note_visibility"], rid),
    )
    log_action(user, "UPDATE_EXPENSE_REPORT", f"Mengubah header Expense Report {r['ref']}")
    notify("data_updated", "expense_report")
    return {"message": "Expense Report berhasil diperbarui."}


@app.post("/api/expense-reports/{rid}/lines")
async def expense_lines_create(rid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)
    if r["status"] != "Draft":
        raise HTTPException(status_code=400, detail="Baris hanya bisa ditambahkan saat status Draft.")

    g = body.get
    unit_price = parse_int(g("unit_price_net"), "harga satuan")
    qty = parse_int(g("qty") or 1, "qty")
    tax_percent = float(g("tax_percent")) if g("tax_percent") not in (None, "") else 11.0
    if not g("category") or unit_price <= 0 or qty <= 0:
        raise HTTPException(status_code=400, detail="Kategori, harga satuan, dan qty wajib diisi dengan benar.")

    receipt_url = None
    file_base64 = g("receiptBase64")
    if file_base64:
        ext = (g("ext") or "jpg").lower().lstrip(".")
        file_name = f"expline_{rid}_{int(asyncio.get_event_loop().time()*1000)}.{ext}"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        b64 = file_base64.split(",")[1] if "," in file_base64 else file_base64
        with open(os.path.join(UPLOAD_DIR, file_name), "wb") as f:
            f.write(base64.b64decode(b64))
        receipt_url = f"/uploads/{file_name}"

    db.execute(
        "INSERT INTO expense_lines (report_id, date, category, description, unit_price_net, tax_percent, "
        "qty, receipt_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, g("date") or datetime.date.today().isoformat(), g("category"), g("description") or "",
         unit_price, tax_percent, qty, receipt_url),
    )
    notify("data_updated", "expense_report")
    return {"message": "Item pengeluaran berhasil ditambahkan."}


@app.delete("/api/expense-reports/{rid}/lines/{lid}")
async def expense_lines_delete(rid: int, lid: int, user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)
    if r["status"] != "Draft":
        raise HTTPException(status_code=400, detail="Baris hanya bisa dihapus saat status Draft.")
    db.execute("DELETE FROM expense_lines WHERE id = ? AND report_id = ?", (lid, rid))
    notify("data_updated", "expense_report")
    return {"message": "Item pengeluaran berhasil dihapus."}


@app.post("/api/expense-reports/{rid}/submit")
async def expense_reports_submit(rid: int, user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)
    if r["status"] != "Draft":
        raise HTTPException(status_code=400, detail="Hanya Expense Report berstatus Draft yang bisa diajukan.")
    lines = db.query_all("SELECT id FROM expense_lines WHERE report_id = ?", (rid,))
    if not lines:
        raise HTTPException(status_code=400, detail="Tambahkan minimal 1 item pengeluaran sebelum mengajukan.")
    if not r["approver_id"]:
        raise HTTPException(status_code=400, detail="User responsible for approval wajib dipilih.")

    db.execute("UPDATE expense_reports SET status = 'Submitted' WHERE id = ?", (rid,))
    log_action(user, "SUBMIT_EXPENSE_REPORT", f"Mengajukan Expense Report {r['ref']} untuk approval")
    notify("data_updated", "expense_report")
    return {"message": "Expense Report berhasil diajukan, menunggu persetujuan."}


@app.put("/api/expense-reports/{rid}/review")
async def expense_reports_review(rid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Aksi harus 'approve' atau 'reject'.")

    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    if r["status"] != "Submitted":
        raise HTTPException(status_code=400, detail=f"Laporan ini berstatus '{r['status']}', tidak bisa direview ulang.")

    new_status = "Approved" if action == "approve" else "Rejected"
    note = body.get("note") or ""
    if action == "reject" and not note:
        raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")

    db.execute(
        "UPDATE expense_reports SET status = ?, reviewed_by = ?, review_note = ?, validation_date = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (new_status, user["name"], note, rid),
    )
    log_action(
        user, "APPROVE_EXPENSE_REPORT" if action == "approve" else "REJECT_EXPENSE_REPORT",
        f"{new_status} Expense Report {r['ref']} ({r['user_name']})",
    )
    notify("data_updated", "expense_report")
    return {"message": f"Expense Report berhasil di-{'setujui' if action == 'approve' else 'tolak'}."}


@app.put("/api/expense-reports/{rid}/pay")
async def expense_reports_pay(rid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")

    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    if r["status"] != "Approved":
        raise HTTPException(
            status_code=400,
            detail="Hanya laporan berstatus 'Approved' (sudah disetujui) yang bisa dibayar.",
        )
    lines = db.query_all("SELECT * FROM expense_lines WHERE report_id = ?", (rid,))
    _, _, gross = _report_totals(lines)

    last_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, reference_id) VALUES (?, ?, ?, ?, ?)",
        ("expense", "expense_report", gross, f"Expense Report {r['ref']}: {r['user_name']}", rid),
    )
    db.execute(
        "UPDATE expense_reports SET status = 'Paid', paid_by = ?, paid_at = CURRENT_TIMESTAMP, "
        "transaction_id = ? WHERE id = ?",
        (user["name"], last_id, rid),
    )
    log_action(user, "PAY_EXPENSE_REPORT", f"Membayar Expense Report {r['ref']} ({r['user_name']}, Rp {gross})")
    notify("data_updated", "expense_report")
    notify("data_updated", "transaction")
    return {"message": "Expense Report berhasil dibayar dan tercatat di Buku Kas."}


@app.post("/api/expense-reports/{rid}/clone")
async def expense_reports_clone(rid: int, user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)

    ref = _generate_expense_ref()
    new_id, _ = db.execute(
        "INSERT INTO expense_reports (ref, project_id, user_id, user_name, period_from, period_to, "
        "approver_id, approver_name, note, note_visibility) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ref, r["project_id"], r["user_id"], r["user_name"], r["period_from"], r["period_to"],
         r["approver_id"], r["approver_name"], r["note"], r["note_visibility"]),
    )
    lines = db.query_all("SELECT * FROM expense_lines WHERE report_id = ?", (rid,))
    for ln in lines:
        db.execute(
            "INSERT INTO expense_lines (report_id, date, category, description, unit_price_net, tax_percent, qty) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id, ln["date"], ln["category"], ln["description"], ln["unit_price_net"], ln["tax_percent"], ln["qty"]),
        )
    log_action(user, "CLONE_EXPENSE_REPORT", f"Menduplikasi Expense Report {r['ref']} menjadi {ref}")
    notify("data_updated", "expense_report")
    return {"message": "Expense Report berhasil diduplikasi.", "id": new_id, "ref": ref}


@app.delete("/api/expense-reports/{rid}")
async def expense_reports_delete(rid: int, user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)
    if r["status"] != "Draft":
        raise HTTPException(status_code=400, detail="Hanya Expense Report berstatus Draft yang bisa dihapus.")
    db.execute("DELETE FROM expense_lines WHERE report_id = ?", (rid,))
    db.execute("DELETE FROM expense_reports WHERE id = ?", (rid,))
    log_action(user, "DELETE_EXPENSE_REPORT", f"Menghapus Expense Report {r['ref']}")
    notify("data_updated", "expense_report")
    return {"message": "Expense Report berhasil dihapus."}


@app.get("/api/expense-reports/{rid}/pdf")
async def expense_reports_pdf(rid: int, user=Depends(authenticate_file_token)):
    r = db.query_one(
        "SELECT r.*, p.name as project_name FROM expense_reports r "
        "LEFT JOIN expense_projects p ON r.project_id = p.id WHERE r.id = ?", (rid,)
    )
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user)
    lines = db.query_all("SELECT * FROM expense_lines WHERE report_id = ? ORDER BY date ASC, id ASC", (rid,))
    company = {
        "legal_name": get_setting("company_legal_name", "Umar Travel"),
        "address": get_setting("company_address", ""),
        "email": get_setting("company_email", ""),
        "website": get_setting("company_website", ""),
    }
    logo_path = None
    logo_url = get_setting("logo_url")
    if logo_url:
        candidate = os.path.join(PUBLIC_DIR, logo_url.lstrip("/"))
        if os.path.isfile(candidate):
            logo_path = candidate
    pdf_bytes = build_expense_pdf(r, lines, company=company, logo_path=logo_path)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={r['ref'].replace('/', '-')}.pdf"},
    )


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


@app.get("/dokumentasi-fitur-baru")
async def dokumentasi_fitur_baru():
    return FileResponse(os.path.join(BASE_DIR, "Dokumentasi-Fitur-Baru.html"))


@app.get("/dokumentasi-fitur-17-juli-2026")
async def dokumentasi_fitur_17_juli_2026():
    return FileResponse(os.path.join(BASE_DIR, "Dokumentasi-Fitur-17-Juli-2026.html"))


@app.get("/dokumentasi-fitur-20-juli-2026")
async def dokumentasi_fitur_20_juli_2026():
    return FileResponse(os.path.join(BASE_DIR, "Dokumentasi-Fitur-20-Juli-2026.html"))


@app.get("/dokumentasi-fitur-21-juli-2026")
async def dokumentasi_fitur_21_juli_2026():
    return FileResponse(os.path.join(BASE_DIR, "Dokumentasi-Fitur-21-Juli-2026.html"))


@app.get("/dokumentasi-fitur-22-juli-2026")
async def dokumentasi_fitur_22_juli_2026():
    return FileResponse(os.path.join(BASE_DIR, "Dokumentasi-Fitur-22-Juli-2026.html"))


@app.get("/dokumentasi-fitur-23-juli-2026")
async def dokumentasi_fitur_23_juli_2026():
    return FileResponse(os.path.join(BASE_DIR, "Dokumentasi-Fitur-23-Juli-2026.html"))


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


# Static files (public/) dipasang TERAKHIR agar route /api, /panduan, /uploads menang.
# html=True -> "/" otomatis melayani index.html.
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="static")


# Gabungkan FastAPI + Socket.IO menjadi satu aplikasi ASGI.
asgi = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")


if __name__ == "__main__":
    uvicorn.run("app:asgi", host="0.0.0.0", port=PORT, log_level="info")
