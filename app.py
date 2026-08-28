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
import os
import sys
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
from auth import authenticate_file_token, authenticate_token
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


























# ===========================================================================
# HOME FINANCE (F-A) & HOME MANAGEMENT (M-A)
# ===========================================================================
# /api/finance/home: pindah ke routes/finance.py


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
from routes.dashboard import router as dashboard_router  # noqa: E402
from routes.jamaah_read import router as jamaah_read_router  # noqa: E402
from routes.jamaah_write import router as jamaah_write_router  # noqa: E402
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
app.include_router(dashboard_router)
app.include_router(jamaah_read_router)
app.include_router(jamaah_write_router)


# Static files (public/) dipasang TERAKHIR agar route /api, /panduan, /uploads menang.
# html=True -> "/" otomatis melayani index.html.
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="static")


# Gabungkan FastAPI + Socket.IO menjadi satu aplikasi ASGI.
asgi = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")


if __name__ == "__main__":
    uvicorn.run("app:asgi", host="0.0.0.0", port=PORT, log_level="info")
