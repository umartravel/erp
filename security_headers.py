"""
Security response headers middleware.

Menambah header defensif pada SETIAP HTTP response (JSON API + HTML statis).
Non-invasif -- tidak mengubah body, tidak mengubah routing, tidak menyentuh WS.

Header yang di-set:
- Content-Security-Policy: allowlist eksplisit sesuai CDN yang dipakai
  public/index.html. Kalau nanti nambah CDN baru, update di sini juga (kalau
  tidak -- browser akan block script/style-nya).
- X-Frame-Options: DENY -> lawas tapi banyak browser lama masih hormati.
  Modern browsers cukup baca frame-ancestors dari CSP.
- X-Content-Type-Options: nosniff -> cegah MIME-sniffing (misal file .txt
  dijalankan sebagai .html).
- Referrer-Policy: strict-origin-when-cross-origin -> jangan bocorkan path
  ke external site saat user klik link keluar.
- Permissions-Policy: matikan API sensitif yang tidak dipakai (camera, mic,
  geolocation, dsb). Kurangi permukaan drive-by exploit.
- Strict-Transport-Security: HANYA kalau ENABLE_HSTS=1 (default off supaya
  dev/localhost HTTP tidak ke-lock ke HTTPS oleh browser cache).

CATATAN 'unsafe-inline' + 'unsafe-eval':
SPA umar_crm punya ~4000 baris inline <script> + inline style + Tailwind CDN
runtime yang butuh eval. Menghapusnya butuh migrasi besar (build step, nonce,
refactor semua onclick=). Untuk sekarang kompromi: tetap 'unsafe-inline' +
'unsafe-eval', TAPI batasi allowlist domain external -- attacker yang berhasil
inject script tidak bisa exfil ke domain sembarang.
"""
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# ----------------------------------------------------------------------------
# CSP allowlist -- update kalau public/index.html tambah CDN baru
# ----------------------------------------------------------------------------
_SCRIPT_SRC = " ".join([
    "'self'",
    "'unsafe-inline'",  # inline <script> blocks di index.html (butuh migrasi besar untuk hapus)
    "'unsafe-eval'",    # Tailwind CDN runtime, Chart.js worker
    "https://cdn.tailwindcss.com",
    "https://unpkg.com",
    "https://cdn.jsdelivr.net",
    "https://cdn.socket.io",
    "https://code.jquery.com",
])

_STYLE_SRC = " ".join([
    "'self'",
    "'unsafe-inline'",  # style="..." attrs + <style> blocks
    "https://fonts.googleapis.com",
    "https://unpkg.com",
    "https://cdn.jsdelivr.net",
])

_FONT_SRC = " ".join([
    "'self'",
    "data:",
    "https://fonts.gstatic.com",
])

_IMG_SRC = " ".join([
    "'self'",
    "data:",
    "blob:",
    "https://placehold.co",
    "https://*.tile.openstreetmap.org",  # Leaflet tiles
])

_CONNECT_SRC = " ".join([
    "'self'",
    "ws:", "wss:",                              # socket.io realtime
    "https://www.emsifa.com",                   # API wilayah Indonesia
])

_CSP = "; ".join([
    "default-src 'self'",
    f"script-src {_SCRIPT_SRC}",
    f"style-src {_STYLE_SRC}",
    f"font-src {_FONT_SRC}",
    f"img-src {_IMG_SRC}",
    f"connect-src {_CONNECT_SRC}",
    "frame-ancestors 'none'",   # cegah clickjacking (modern setara X-Frame-Options: DENY)
    "base-uri 'self'",          # cegah <base> injection redirect resource loading
    "form-action 'self'",       # cegah form dibajak submit ke external
    "object-src 'none'",        # blokir <object>/<embed>/<applet> (Flash/plugin abuse)
])

_PERMISSIONS_POLICY = ", ".join([
    "camera=()",
    "microphone=()",
    "geolocation=()",
    "payment=()",
    "usb=()",
    "magnetometer=()",
    "gyroscope=()",
    "accelerometer=()",
])


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Set defensive HTTP headers on every response."""

    def __init__(self, app: ASGIApp, enable_hsts: bool | None = None) -> None:
        super().__init__(app)
        # HSTS opt-in via env: hanya safe kalau prod deploy sudah full HTTPS.
        # Nyalain di localhost -> browser cache akan force https://localhost yg tidak jalan.
        if enable_hsts is None:
            enable_hsts = os.environ.get("ENABLE_HSTS", "").lower() in ("1", "true", "yes")
        self._enable_hsts = enable_hsts

    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers["Content-Security-Policy"] = _CSP
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        response.headers["Permissions-Policy"] = _PERMISSIONS_POLICY
        if self._enable_hsts:
            # 6 bulan; includeSubDomains dilepas supaya sub-domain lain tidak ikut ke-lock.
            response.headers["Strict-Transport-Security"] = "max-age=15552000"
        return response
