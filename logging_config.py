"""
Logging + Request ID plumbing.

Kenapa: sebelumnya semua diagnostik pakai `print()` -- tidak ada level, tidak
ada timestamp, tidak ada korelasi request. Kalau prod incident, mustahil trace
request A vs request B yang bercampur di log yang sama.

Yang di-setup:
- Root logger dgn format: `TS LEVEL [req=<id>] logger: message`
- RequestIdContext ContextVar: middleware set per request, formatter baca.
- RequestIdMiddleware: baca X-Request-Id kalau upstream (LB/reverse proxy)
  kirim, atau generate UUID pendek. Set balik ke response header supaya user
  bisa report "request id X error" saat lapor bug.

Tidak invasif: `print()` tetap jalan (masuk stdout, ke-capture juga). Migrasi
bertahap: ganti print()->logger di modul yang paling sering di-debug dulu.
"""
import contextvars
import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# Header standar yang biasa dipakai reverse proxy (nginx, cloudflare).
REQUEST_ID_HEADER = "X-Request-Id"

# ContextVar dibaca formatter untuk sisipkan ke setiap log line.
# Default "-" kalau di luar konteks request (startup, background task).
_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


class _RequestIdFilter(logging.Filter):
    """Suntik atribut `request_id` ke setiap LogRecord dari ContextVar."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = _request_id_var.get()
        return True


_FMT = "%(asctime)s %(levelname)-5s [req=%(request_id)s] %(name)s: %(message)s"
_DATEFMT = "%Y-%m-%d %H:%M:%S"


def setup_logging(level: int = logging.INFO) -> None:
    """Dipanggil sekali dari lifespan startup. Idempotent -- boleh dipanggil
    ulang tanpa dobel handler (cek existing handler dulu)."""
    root = logging.getLogger()
    root.setLevel(level)
    # Kalau sudah pernah setup, jangan tambah handler kedua (biasa terjadi
    # di test yg re-init lifespan berkali-kali).
    for h in root.handlers:
        if getattr(h, "_umar_installed", False):
            return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FMT, datefmt=_DATEFMT))
    handler.addFilter(_RequestIdFilter())
    handler._umar_installed = True  # type: ignore[attr-defined]
    root.addHandler(handler)


def get_request_id() -> str:
    """Ambil request_id aktif (dipakai handler yang mau echo ke response)."""
    return _request_id_var.get()


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Set request_id per request + echo di response header."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request, call_next):
        # Hormati X-Request-Id dari upstream kalau ada -- supaya bisa trace
        # cross-service. Kalau tidak, generate UUID pendek (12 chars cukup utk
        # human readability + tabrakan practically nol dalam 1 request/detik).
        incoming = request.headers.get(REQUEST_ID_HEADER)
        rid = incoming if incoming else uuid.uuid4().hex[:12]
        token = _request_id_var.set(rid)
        try:
            response = await call_next(request)
        finally:
            _request_id_var.reset(token)
        response.headers[REQUEST_ID_HEADER] = rid
        return response
