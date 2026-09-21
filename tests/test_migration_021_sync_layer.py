"""
Phase SS-1.1 (2026-09-21): Test migration 021 Supabase sync layer schema.

Verify:
1. 9 kolom baru di jamaah applied
2. sync_state + sync_log tables created dgn schema benar
3. Semua 4 indexes present
4. UNIQUE constraint external_id + partial index (NULL allowed)
5. PK constraint sync_state.key
6. Auto-increment + default timestamp sync_log
"""
import db


def test_migration_021_jamaah_columns_added(client):
    """Semua 9 kolom baru harus ada di jamaah table setelah migration."""
    cols = {r["name"] for r in db.query_all("SELECT name FROM pragma_table_info('jamaah')")}
    expected = {
        "external_id", "supabase_missing_since", "admin_marketing",
        "channel", "sub_channel", "dp_amount",
        "package_ext_id", "agent_ext_id", "agent_name_raw",
    }
    missing = expected - cols
    assert not missing, f"Kolom hilang di jamaah: {missing}"


def test_migration_021_sync_state_table_exists(client):
    """Tabel sync_state harus ada dgn kolom key (PK) + value + updated_at."""
    row = db.query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_state'"
    )
    assert row is not None, "Table sync_state tidak dibuat"
    cols = {r["name"] for r in db.query_all("SELECT name FROM pragma_table_info('sync_state')")}
    assert cols == {"key", "value", "updated_at"}, f"Kolom sync_state salah: {cols}"


def test_migration_021_sync_log_table_exists(client):
    """Tabel sync_log harus ada dgn 7 kolom audit trail."""
    row = db.query_one(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='sync_log'"
    )
    assert row is not None, "Table sync_log tidak dibuat"
    cols = {r["name"] for r in db.query_all("SELECT name FROM pragma_table_info('sync_log')")}
    expected = {"id", "run_ts", "action", "external_id", "old_json", "new_json", "error_msg"}
    assert cols == expected, f"Kolom sync_log salah: cols={cols}, expected={expected}"


def test_migration_021_indexes_created(client):
    """3 indexes baru (di luar idx_jamaah_external_id yg bisa dari migration 002)."""
    idxs = {r["name"] for r in db.query_all(
        "SELECT name FROM sqlite_master WHERE type='index' "
        "AND (name LIKE 'idx_jamaah_supabase%' OR name LIKE 'idx_sync_log%')"
    )}
    expected = {"idx_jamaah_supabase_missing", "idx_sync_log_ext_id", "idx_sync_log_action"}
    missing = expected - idxs
    assert not missing, f"Index hilang: {missing}"


def test_migration_021_external_id_unique_constraint(client):
    """UNIQUE INDEX partial di external_id -- 2 row dgn external_id sama harus reject."""
    db.execute("DELETE FROM jamaah WHERE external_id LIKE 'TEST-MIG021-%'")
    db.execute(
        "INSERT INTO jamaah (name, external_id, status) VALUES (?, ?, ?)",
        ("Test Mig021 A", "TEST-MIG021-DUPE", "Test"),
    )
    try:
        db.execute(
            "INSERT INTO jamaah (name, external_id, status) VALUES (?, ?, ?)",
            ("Test Mig021 B", "TEST-MIG021-DUPE", "Test"),
        )
        raised = False
    except Exception as e:
        raised = "unique" in str(e).lower() or "UNIQUE" in str(e)
    finally:
        db.execute("DELETE FROM jamaah WHERE external_id LIKE 'TEST-MIG021-%'")
    assert raised, "Duplicate external_id harus fail dgn UNIQUE constraint"


def test_migration_021_external_id_null_allowed(client):
    """Partial UNIQUE INDEX -- multiple rows dgn external_id NULL harus OK."""
    db.execute("DELETE FROM jamaah WHERE name LIKE 'Test Mig021 NULL%'")
    db.execute(
        "INSERT INTO jamaah (name, external_id, status) VALUES (?, NULL, ?)",
        ("Test Mig021 NULL A", "Test"),
    )
    db.execute(
        "INSERT INTO jamaah (name, external_id, status) VALUES (?, NULL, ?)",
        ("Test Mig021 NULL B", "Test"),
    )
    rows = db.query_all(
        "SELECT id FROM jamaah WHERE name LIKE 'Test Mig021 NULL%'"
    )
    assert len(rows) == 2, "Multiple NULL external_id harus allowed"
    db.execute("DELETE FROM jamaah WHERE name LIKE 'Test Mig021 NULL%'")


def test_migration_021_sync_state_key_pk(client):
    """sync_state.key adalah PK -- INSERT ulang harus fail (kecuali INSERT OR REPLACE)."""
    db.execute("DELETE FROM sync_state WHERE key = 'test_mig021_key'")
    db.execute(
        "INSERT INTO sync_state (key, value) VALUES (?, ?)",
        ("test_mig021_key", "value_1"),
    )
    try:
        db.execute(
            "INSERT INTO sync_state (key, value) VALUES (?, ?)",
            ("test_mig021_key", "value_2"),
        )
        raised = False
    except Exception:
        raised = True
    finally:
        db.execute("DELETE FROM sync_state WHERE key = 'test_mig021_key'")
    assert raised, "Duplicate key di sync_state harus fail"


def test_migration_021_sync_log_defaults(client):
    """sync_log.run_ts harus default CURRENT_TIMESTAMP + id auto-increment."""
    db.execute("DELETE FROM sync_log WHERE action = 'test_mig021'")
    rowid, _ = db.execute(
        "INSERT INTO sync_log (action, external_id) VALUES (?, ?)",
        ("test_mig021", "TEST-EXT-1"),
    )
    row = db.query_one("SELECT id, run_ts, action, external_id FROM sync_log WHERE id = ?", (rowid,))
    assert row["id"] > 0, "id auto-increment tidak jalan"
    assert row["run_ts"] is not None, "run_ts tidak default"
    assert row["action"] == "test_mig021"
    db.execute("DELETE FROM sync_log WHERE action = 'test_mig021'")
