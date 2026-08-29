"""
Test suite fixtures. Berjalan pada DB terisolir (tests/.test_db.sqlite) yang
di-init dari nol + seeded via db.init_db() -- ZERO sentuhan ke umar_crm.db
produksi. Env var UMAR_DB_FILE harus di-set SEBELUM import project apapun.
"""
import os
import pathlib

# CRITICAL: set env var SEBELUM impor project apapun. db.DB_FILE dievaluasi
# saat module db di-import (line 14), jadi harus keburu di-override di sini.
_TESTS_DIR = pathlib.Path(__file__).parent
_TMP_DB = _TESTS_DIR / ".test_db.sqlite"
# Bersihkan DB test dari run sebelumnya supaya idempoten.
for suffix in ("", "-shm", "-wal"):
    p = _TESTS_DIR / f".test_db.sqlite{suffix}"
    if p.exists():
        p.unlink()
os.environ["UMAR_DB_FILE"] = str(_TMP_DB)
# WA jangan sentuh backend nyata; stub mode simulate = 'sukses' tapi tidak send.
os.environ["WA_SIMULATE"] = "1"

# Sekarang aman import project.
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app as _app_module  # noqa: E402
import rate_limit as _rate_limit  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_rate_limit():
    """Reset rate limiter sebelum setiap test -- kegagalan login test A
    tidak boleh menumpuk ke test B, sekaligus supaya test_auth punya
    'meja bersih' saat brute-force test lain menaikkan counter."""
    _rate_limit.reset_all()
    yield


@pytest.fixture(scope="session")
def client():
    """TestClient dengan lifespan aktif -- init_db() jalan sekali (create schema
    + seed users/packages/agents/settings/checklist), lalu semua test share
    koneksi yg sama."""
    with TestClient(_app_module.app) as c:
        yield c


def _login(client, username, password="password123"):
    r = client.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, f"Login {username} gagal: {r.status_code} {r.text}"
    return r.json()["token"]


@pytest.fixture(scope="session")
def admin_token(client):
    return _login(client, "admin")


@pytest.fixture(scope="session")
def sales_token(client):
    return _login(client, "sales1")


@pytest.fixture(scope="session")
def finance_token(client):
    return _login(client, "finance1")


@pytest.fixture(scope="session")
def ops_token(client):
    return _login(client, "ops1")


@pytest.fixture(scope="session")
def management_token(client):
    return _login(client, "manager1")


def bearer(token):
    """Helper: return dict header Authorization bearer."""
    return {"Authorization": f"Bearer {token}"}
