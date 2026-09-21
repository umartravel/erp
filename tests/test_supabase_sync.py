"""
Phase SS-1.5 (2026-09-21): Test endpoints Supabase sync layer.

Cover:
1. Status endpoint (no Supabase call)
2. Log endpoint (no Supabase call)
3. Soft-deleted list endpoint
4. RBAC: sales/finance/ops denied semua endpoint
5. Date parser + mapping unit tests (supabase_sync internal)

Tidak test actual sync_closings run (butuh mock Supabase HTTP). Testing
integration Supabase dilakukan manual di SS-1.6 backfill day.
"""
import db
import supabase_sync
from tests.conftest import bearer


# ============================================================================
# Unit tests supabase_sync (tanpa DB, tanpa network)
# ============================================================================

def test_date_parser_iso_format():
    """YYYY-MM-DD ISO format harus pass-through, tidak salah di-parse sebagai DD/MM/YYYY."""
    assert supabase_sync._parse_date_ddmmyyyy("2026-09-18") == "2026-09-18"


def test_date_parser_ddmmyyyy():
    """Format Supabase 'TANGGAL ORDER' = 'DD/MM/YYYY'."""
    assert supabase_sync._parse_date_ddmmyyyy("18/09/2026") == "2026-09-18"


def test_date_parser_short_year():
    """DD/MM/YY 2-digit year -> assumes 20XX."""
    assert supabase_sync._parse_date_ddmmyyyy("01/12/26") == "2026-12-01"


def test_date_parser_edge_cases():
    """Null/empty/invalid returns None."""
    assert supabase_sync._parse_date_ddmmyyyy("") is None
    assert supabase_sync._parse_date_ddmmyyyy(None) is None
    assert supabase_sync._parse_date_ddmmyyyy("junk") is None
    assert supabase_sync._parse_date_ddmmyyyy("35/13/2026") is None  # invalid day/month


def test_map_closing_full_row():
    """Full Supabase closing row -> UMAR jamaah mapping."""
    sample = {
        "ID JAMAAH": "TEST-MAP-1", "NAMA JAMAAH": "TEST USER",
        "TANGGAL ORDER": "18/09/2026", "PAKET": "Umroh Test",
        "ID PAKET": "PKG-99", "ADMIN MARKETING": "FARAH",
        "CHANNEL": "OFFLINE", "SUB CHANNEL": "AGEN",
        "HARGA": 27900000, "TOTAL BAYAR": 5000000, "DP": 5000000,
        "STATPAY": "DP", "NAMA_AGEN": "DENDI",
        "KECAMATAN": "KEC-TEST", "KANTOR IMIGRASI": "JAKARTA",
    }
    m = supabase_sync._map_closing_to_jamaah(sample)
    assert m["external_id"] == "TEST-MAP-1"
    assert m["name"] == "TEST USER"
    assert m["order_date"] == "2026-09-18"
    assert m["package_ext_id"] == "PKG-99"
    assert m["admin_marketing"] == "FARAH"
    assert m["dp_amount"] == 5000000
    assert m["payment_status"] == "Cicilan"
    assert m["subdistrict"] == "KEC-TEST"
    assert m["passport_location"] == "JAKARTA"
    assert m["agent_name_raw"] == "DENDI"


def test_map_statpay_enum():
    """STATPAY enum mapping ke UMAR payment_status."""
    for supabase_val, umar_val in [
        ("DP", "Cicilan"), ("LUNAS", "Lunas"), ("BELUM LUNAS", "Terdaftar"),
        ("dp", "Cicilan"),  # case insensitive via .upper()
        ("unknown", "Terdaftar"),  # fallback
    ]:
        m = supabase_sync._map_closing_to_jamaah({"ID JAMAAH": "X", "STATPAY": supabase_val})
        assert m["payment_status"] == umar_val, (
            f"{supabase_val} -> {m['payment_status']} != {umar_val}"
        )


# ============================================================================
# Endpoint tests (butuh client + auth token)
# ============================================================================

def test_sync_status_admin_ok(client, admin_token):
    """GET /api/supabase/sync/status returns state + config check."""
    r = client.get("/api/supabase/sync/status", headers=bearer(admin_token))
    assert r.status_code == 200
    data = r.json()
    assert "config_available" in data
    assert "state" in data
    assert "soft_deleted_current" in data


def test_sync_status_mgmt_ok(client, management_token):
    """Management juga boleh access status."""
    r = client.get("/api/supabase/sync/status", headers=bearer(management_token))
    assert r.status_code == 200


def test_sync_status_sales_denied(client, sales_token):
    r = client.get("/api/supabase/sync/status", headers=bearer(sales_token))
    assert r.status_code == 403


def test_sync_log_admin_ok(client, admin_token):
    """GET /api/supabase/sync/log returns audit entries."""
    db.execute("DELETE FROM sync_log WHERE action = 'test_ep_log'")
    db.execute(
        "INSERT INTO sync_log (action, external_id) VALUES ('test_ep_log', 'TEST-LOG')"
    )
    r = client.get("/api/supabase/sync/log?limit=10", headers=bearer(admin_token))
    assert r.status_code == 200
    entries = r.json()["entries"]
    assert any(e["action"] == "test_ep_log" for e in entries)
    db.execute("DELETE FROM sync_log WHERE action = 'test_ep_log'")


def test_sync_log_filter_action(client, admin_token):
    """Filter action returns hanya matching rows."""
    db.execute("DELETE FROM sync_log WHERE action IN ('test_filter_a', 'test_filter_b')")
    db.execute("INSERT INTO sync_log (action, external_id) VALUES ('test_filter_a', 'A')")
    db.execute("INSERT INTO sync_log (action, external_id) VALUES ('test_filter_b', 'B')")
    r = client.get("/api/supabase/sync/log?action=test_filter_a",
                   headers=bearer(admin_token))
    entries = r.json()["entries"]
    assert all(e["action"] == "test_filter_a" for e in entries)
    db.execute("DELETE FROM sync_log WHERE action IN ('test_filter_a', 'test_filter_b')")


def test_sync_soft_deleted_admin_ok(client, admin_token):
    """GET /api/supabase/sync/soft-deleted returns list."""
    db.execute("DELETE FROM jamaah WHERE external_id = 'TEST-SOFT-DEL'")
    db.execute(
        "INSERT INTO jamaah (external_id, name, package_type, supabase_missing_since, status) "
        "VALUES ('TEST-SOFT-DEL', 'Test Soft', 'Test', '2026-09-21 00:00:00', 'Test')"
    )
    r = client.get("/api/supabase/sync/soft-deleted", headers=bearer(admin_token))
    assert r.status_code == 200
    rows = r.json()["soft_deleted"]
    assert any(x["external_id"] == "TEST-SOFT-DEL" for x in rows)
    db.execute("DELETE FROM jamaah WHERE external_id = 'TEST-SOFT-DEL'")


def test_sync_run_sales_denied(client, sales_token):
    """POST /api/supabase/sync/run: sales tidak boleh."""
    r = client.post("/api/supabase/sync/run", headers=bearer(sales_token))
    assert r.status_code == 403


def test_sync_full_backfill_mgmt_denied(client, management_token):
    """POST /full-backfill: management tidak boleh (admin only)."""
    r = client.post("/api/supabase/sync/full-backfill", headers=bearer(management_token))
    assert r.status_code == 403


def test_restore_sales_denied(client, sales_token):
    """POST /restore/{ext_id}: sales tidak boleh."""
    r = client.post("/api/supabase/restore/TEST-1", headers=bearer(sales_token))
    assert r.status_code == 403


def test_sync_run_no_config(client, admin_token, monkeypatch):
    """POST /sync/run tanpa config Supabase -> 503."""
    monkeypatch.setattr("supabase_client.check_config_available", lambda: False)
    r = client.post("/api/supabase/sync/run", headers=bearer(admin_token))
    assert r.status_code == 503
    assert "config tidak tersedia" in r.json()["error"].lower()


def test_state_persistence(client, admin_token):
    """sync_state key/value store persist antar call."""
    db.execute("DELETE FROM sync_state WHERE key = 'test_persist_key'")
    supabase_sync._set_state("test_persist_key", "value_1")
    assert supabase_sync._get_state("test_persist_key") == "value_1"
    supabase_sync._set_state("test_persist_key", "value_2")  # upsert
    assert supabase_sync._get_state("test_persist_key") == "value_2"
    db.execute("DELETE FROM sync_state WHERE key = 'test_persist_key'")


def test_log_sync_write(client):
    """_log_sync write ke sync_log table dgn action + JSON payload."""
    db.execute("DELETE FROM sync_log WHERE action = 'test_write_log'")
    supabase_sync._log_sync(
        "test_write_log", "EXT-1",
        old={"name": "Old"}, new={"name": "New"},
    )
    row = db.query_one(
        "SELECT action, external_id, old_json, new_json FROM sync_log "
        "WHERE action = 'test_write_log'"
    )
    assert row is not None
    assert row["external_id"] == "EXT-1"
    assert '"Old"' in row["old_json"]
    assert '"New"' in row["new_json"]
    db.execute("DELETE FROM sync_log WHERE action = 'test_write_log'")
