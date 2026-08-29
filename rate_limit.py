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

# Ambang batas: 10 kegagalan dalam 5 menit -> block sampai kegagalan tertua expire.
WINDOW_SECONDS = 300
MAX_FAILURES = 10

_login_failures: dict[str, deque] = defaultdict(deque)


def _prune(dq: deque, now: float) -> None:
    """Buang timestamp yang sudah di luar window."""
    cutoff = now - WINDOW_SECONDS
    while dq and dq[0] < cutoff:
        dq.popleft()


def is_login_blocked(key: str) -> bool:
    """True kalau key sudah mencapai MAX_FAILURES kegagalan dalam window."""
    now = time.time()
    dq = _login_failures[key]
    _prune(dq, now)
    return len(dq) >= MAX_FAILURES


def record_login_failure(key: str) -> None:
    """Catat 1 kegagalan login (dipanggil setelah 401/404 di handler login)."""
    _login_failures[key].append(time.time())


def clear_login_failures(key: str) -> None:
    """Reset counter (dipanggil setelah login sukses)."""
    _login_failures.pop(key, None)


def reset_all() -> None:
    """Buang seluruh state -- untuk isolasi antar-test di pytest."""
    _login_failures.clear()
