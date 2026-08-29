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
from contextlib import asynccontextmanager

# Konsol Windows (cp1252) bisa gagal mencetak emoji (🚀, ✅). Paksa UTF-8.
try:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except Exception:  # noqa: BLE001
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
import socketio
import uvicorn

import db
import realtime
import whatsapp as wa
from realtime import sio
from security_headers import SecurityHeadersMiddleware

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
    # SECURITY: warn kalau JWT_SECRET default terpakai. Kalau prod deploy tanpa
    # set env var, attacker yang tau string ini bisa forge token siapapun.
    from auth import JWT_SECRET as _js
    if _js == "umar_crm_super_secret_key_2026":
        print("[WARN] JWT_SECRET pakai default -- set env var JWT_SECRET untuk produksi!")
    print(f"🚀 Server Backend CRM Umar berjalan di port {PORT} (Python/FastAPI - Local & VPS)")
    yield


app = FastAPI(title="Umar CRM API", lifespan=lifespan)

# SECURITY: pasang header defensif (CSP, X-Frame-Options, X-Content-Type-Options,
# Referrer-Policy, Permissions-Policy, opsional HSTS). Registered SEBELUM router
# supaya semua response -- termasuk exception handler -- kena middleware.
app.add_middleware(SecurityHeadersMiddleware)


# --- Konversi error FastAPI -> {"error": ...} agar kompatibel frontend ---
@app.exception_handler(HTTPException)
async def http_exc_handler(request: Request, exc: HTTPException):
    return JSONResponse(status_code=exc.status_code, content={"error": exc.detail})


@app.exception_handler(RequestValidationError)
async def validation_exc_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(status_code=400, content={"error": "Permintaan tidak valid"})




























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

# -- Modular routers (dipecah dari app.py agar file ini tidak membengkak) --
# Setiap router mendaftar endpoint-nya sendiri lewat APIRouter + include_router.
from routes.reconcile import router as reconcile_router  # noqa: E402
from routes.finance import router as finance_router  # noqa: E402
from routes.mgmt_home import router as mgmt_home_router  # noqa: E402
from routes.mgmt_reports import router as mgmt_reports_router  # noqa: E402
from routes.mgmt_targets import router as mgmt_targets_router  # noqa: E402
from routes.mgmt_risk import router as mgmt_risk_router  # noqa: E402
from routes.ops_incidents import router as ops_incidents_router  # noqa: E402
from routes.ops_home import router as ops_home_router  # noqa: E402
from routes.ops_feedback import router as ops_feedback_router  # noqa: E402
from routes.ops_vendors import router as ops_vendors_router  # noqa: E402
from routes.ops_checklist import router as ops_checklist_router  # noqa: E402
from routes.sales import router as sales_router  # noqa: E402
from routes.jamaah_actions import router as jamaah_actions_router  # noqa: E402
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
from routes.static_pages import router as static_pages_router  # noqa: E402
app.include_router(reconcile_router)
app.include_router(finance_router)
app.include_router(mgmt_home_router)
app.include_router(mgmt_reports_router)
app.include_router(mgmt_targets_router)
app.include_router(mgmt_risk_router)
app.include_router(ops_incidents_router)
app.include_router(ops_home_router)
app.include_router(ops_feedback_router)
app.include_router(ops_vendors_router)
app.include_router(ops_checklist_router)
app.include_router(sales_router)
app.include_router(jamaah_actions_router)
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
app.include_router(static_pages_router)


# Static files (public/) dipasang TERAKHIR agar route /api, /panduan, /uploads menang.
# html=True -> "/" otomatis melayani index.html.
os.makedirs(UPLOAD_DIR, exist_ok=True)
app.mount("/", StaticFiles(directory=PUBLIC_DIR, html=True), name="static")


# Gabungkan FastAPI + Socket.IO menjadi satu aplikasi ASGI.
asgi = socketio.ASGIApp(sio, other_asgi_app=app, socketio_path="socket.io")


if __name__ == "__main__":
    uvicorn.run("app:asgi", host="0.0.0.0", port=PORT, log_level="info")
