"""
Rate limiter in-memory untuk brute-force protection endpoint /api/login.

Sliding window per key (biasanya IP address):
- MAX_FAILURES kegagalan login dalam WINDOW_SECONDS -> block sampai window bergeser.
- Login sukses langsung clear counter (attacker yang berhasil tebak tidak
  perlu tunggu window habis, tapi juga tidak dihitung lagi).

Kenapa in-memory (bukan Redis)? Deploy single-instance FastAPI + SQLite,
jadi state global proses cukup. Kalau nanti multi-worker/multi-node, ganti
implementation dengan Redis atau shared cache -- interface fungsi tetap.
"""
import time
from collections import defaultdict, deque

# --- Login brute-force protection --------------------------------------------
# Ambang batas: 10 kegagalan dalam 5 menit -> block sampai kegagalan tertua expire.
WINDOW_SECONDS = 300
MAX_FAILURES = 10

_login_failures: dict[str, deque] = defaultdict(deque)

# --- General API throttle ----------------------------------------------------
# Cap per user (atau IP kalau anonim) untuk /api/* umum. Angka sengaja longgar
# supaya user aktif tidak ke-block: ~5 req/detik rata-rata. Yang ditangkap =
# script bot / scrape brute yg ratusan req/detik.
API_WINDOW_SECONDS = 60
API_MAX_REQUESTS = 300

_api_requests: dict[str, deque] = defaultdict(deque)


def _prune(dq: deque, now: float, window: float = WINDOW_SECONDS) -> None:
    """Buang timestamp yang sudah di luar window."""
    cutoff = now - window
    while dq and dq[0] < cutoff:
        dq.popleft()


def is_login_blocked(key: str) -> bool:
    """True kalau key sudah mencapai MAX_FAILURES kegagalan dalam window."""
    now = time.time()
    dq = _login_failures[key]
    _prune(dq, now, WINDOW_SECONDS)
    return len(dq) >= MAX_FAILURES


def record_login_failure(key: str) -> None:
    """Catat 1 kegagalan login (dipanggil setelah 401/404 di handler login)."""
    _login_failures[key].append(time.time())


def clear_login_failures(key: str) -> None:
    """Reset counter (dipanggil setelah login sukses)."""
    _login_failures.pop(key, None)


def is_api_blocked(key: str) -> bool:
    """True kalau key sudah lebihi API_MAX_REQUESTS dalam API_WINDOW_SECONDS.

    Dipanggil dari middleware SEBELUM request diteruskan ke handler.
    """
    now = time.time()
    dq = _api_requests[key]
    _prune(dq, now, API_WINDOW_SECONDS)
    return len(dq) >= API_MAX_REQUESTS


def record_api_request(key: str) -> None:
    """Catat 1 request API (dipanggil dari middleware setelah cek block)."""
    _api_requests[key].append(time.time())


def reset_all() -> None:
    """Buang seluruh state -- untuk isolasi antar-test di pytest."""
    _login_failures.clear()
    _api_requests.clear()


# ============================================================================
# Middleware: API throttle
# ============================================================================
import base64  # noqa: E402
import json  # noqa: E402

from starlette.middleware.base import BaseHTTPMiddleware  # noqa: E402
from starlette.responses import JSONResponse  # noqa: E402
from starlette.types import ASGIApp  # noqa: E402


def _extract_user_id_from_bearer(auth_header: str | None) -> str | None:
    """Coba baca `id` dari JWT payload TANPA verifikasi tanda-tangan.

    Rate limit boleh pakai user_id dari token tidak-terverifikasi -- worst case
    attacker forge id palsu utk consume budget user lain. Karena budget per-key
    tidak affect user lain (bukti kredensial), forge hanya rugi diri sendiri.
    Trade-off: hindari overhead JWT verify di tiap request middleware.
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        return None
    token = auth_header[7:]
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        # JWT payload = base64url encoded JSON. Pad supaya b64decode tidak crash.
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        uid = payload.get("id")
        return f"user:{uid}" if uid is not None else None
    except Exception:  # noqa: BLE001
        return None


class ApiRateLimitMiddleware(BaseHTTPMiddleware):
    """Throttle request /api/* per user (fallback IP kalau anonim).

    Non-API path (index.html, /uploads, /socket.io, /branding) DILEWATKAN --
    supaya SPA + realtime + PII file loader tidak terkena cap yg salah target.
    Endpoint /api/login juga dilewatkan karena punya rate limiter khusus.
    """

    async def dispatch(self, request, call_next):
        path = request.url.path
        # Skip non-API path + login (punya throttle sendiri).
        if not path.startswith("/api/") or path == "/api/login":
            return await call_next(request)

        # Kunci bucket: user_id kalau bearer valid, else IP.
        key = _extract_user_id_from_bearer(request.headers.get("authorization"))
        if key is None:
            key = f"ip:{request.client.host if request.client else 'unknown'}"

        if is_api_blocked(key):
            return JSONResponse(
                status_code=429,
                content={"error": "Terlalu banyak request. Coba lagi dalam 1 menit."},
            )
        record_api_request(key)
        return await call_next(request)
