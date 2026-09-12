"""
Phase F1: migration 011 expense_categories -- seed + backfill test.
"""
import db


def test_categories_seeded(client):
    total = db.query_one("SELECT COUNT(*) c FROM expense_categories")["c"]
    # 2 income parents + 6 expense parents + 5 income subs + 44 expense subs = 57
    assert total >= 50, f"Expected >= 50 categories seeded, got {total}"


def test_income_parents_present(client):
    parents = db.query_all(
        "SELECT name FROM expense_categories "
        "WHERE parent_id IS NULL AND group_type = 'income' ORDER BY sort_order"
    )
    names = [p["name"] for p in parents]
    assert "Pendapatan Umroh" in names
    assert "Pendapatan Lain-lain" in names


def test_expense_parents_present(client):
    parents = db.query_all(
        "SELECT name FROM expense_categories "
        "WHERE parent_id IS NULL AND group_type = 'expense' ORDER BY sort_order"
    )
    names = [p["name"] for p in parents]
    for e in [
        "HPP / Biaya Operasional Umroh",
        "Marketing & Sales",
        "HR & Gaji",
        "Operasional Kantor",
        "Keuangan & Legal",
        "Aset & Lain-lain",
    ]:
        assert e in names, f"Missing expense parent: {e}"


def test_marketing_iklan_digital_subcategory_present(client):
    row = db.query_one(
        "SELECT c.name, p.name AS parent "
        "FROM expense_categories c JOIN expense_categories p ON p.id = c.parent_id "
        "WHERE c.name LIKE '%Iklan Digital%'"
    )
    assert row is not None
    assert row["parent"] == "Marketing & Sales"


def test_payment_jamaah_subcategory_present(client):
    """Backfill target subcategory harus ada di bawah 'Pendapatan Umroh'."""
    row = db.query_one(
        "SELECT c.id, c.name, p.name AS parent FROM expense_categories c "
        "JOIN expense_categories p ON p.id = c.parent_id "
        "WHERE c.name = 'Payment Jamaah'"
    )
    assert row is not None
    assert row["parent"] == "Pendapatan Umroh"


def test_transactions_category_id_column_exists(client):
    cols = [r["name"] for r in db.query_all("PRAGMA table_info(transactions)")]
    assert "category_id" in cols, f"transactions.category_id missing. Cols: {cols}"


# ============================================================================
# Phase EX-1: unblock GET /finance/categories untuk semua role + wajibkan
# category_id di POST /expense-reports.
# ============================================================================

from tests.conftest import bearer  # noqa: E402


def _first_expense_category_id():
    row = db.query_one(
        "SELECT id FROM expense_categories "
        "WHERE group_type = 'expense' AND parent_id IS NOT NULL AND is_active = 1 "
        "ORDER BY id ASC LIMIT 1"
    )
    return row["id"] if row else None


def _ensure_project(client, admin_token):
    projs = client.get("/api/expense-projects", headers=bearer(admin_token)).json()
    if projs:
        return projs[0]["id"]
    client.post("/api/expense-projects", json={"name": "PROJ-EX1"},
                headers=bearer(admin_token))
    projs = client.get("/api/expense-projects", headers=bearer(admin_token)).json()
    return projs[0]["id"] if projs else None


def test_ex1_sales_can_read_categories(client, sales_token):
    """Sales HARUS bisa GET /api/finance/categories (Phase EX-1 unblock)."""
    r = client.get("/api/finance/categories", headers=bearer(sales_token))
    assert r.status_code == 200, f"Sales dapat {r.status_code}: {r.text}"
    body = r.json()
    assert "expense" in body and "income" in body
    assert len(body["expense"]) > 0, "expense tree tidak boleh kosong"


def test_ex1_ops_can_read_categories(client, ops_token):
    """Ops juga bisa GET kategori."""
    r = client.get("/api/finance/categories", headers=bearer(ops_token))
    assert r.status_code == 200


def test_ex1_categories_crud_still_admin_only(client, sales_token):
    """POST kategori tetap admin-only -- sales dapat 403."""
    r = client.post("/api/finance/categories",
                    json={"name": "Test Cat", "group_type": "expense"},
                    headers=bearer(sales_token))
    assert r.status_code == 403, f"Expected 403, got {r.status_code}: {r.text}"


def test_ex1_expense_report_category_required(client, sales_token, admin_token):
    """POST /api/expense-reports tanpa category_id -> 400."""
    pid = _ensure_project(client, admin_token)
    r = client.post(
        "/api/expense-reports",
        json={"project_id": pid, "period_from": "2026-09-01",
              "period_to": "2026-09-30", "note": "no category"},
        headers=bearer(sales_token),
    )
    assert r.status_code == 400, f"Expected 400, got {r.status_code}: {r.text}"
    body = r.json()
    msg = (body.get("detail") or body.get("error") or "").lower()
    assert "kategori" in msg, f"Message tidak mengandung 'kategori': {body}"


def test_ex1_expense_report_category_valid_ok(client, sales_token, admin_token):
    """POST /api/expense-reports dengan category_id valid -> 200."""
    pid = _ensure_project(client, admin_token)
    cid = _first_expense_category_id()
    assert cid, "Perlu minimal 1 subkategori expense untuk test"
    r = client.post(
        "/api/expense-reports",
        json={"project_id": pid, "category_id": cid,
              "period_from": "2026-09-01", "period_to": "2026-09-30",
              "note": "with category"},
        headers=bearer(sales_token),
    )
    assert r.status_code == 200, f"Expected 200, got {r.status_code}: {r.text}"
    assert "id" in r.json()


def test_ex1_expense_report_invalid_category_id(client, sales_token, admin_token):
    """category_id yang nonaktif/tidak ada -> 400."""
    pid = _ensure_project(client, admin_token)
    r = client.post(
        "/api/expense-reports",
        json={"project_id": pid, "category_id": 999999,
              "period_from": "2026-09-01", "period_to": "2026-09-30"},
        headers=bearer(sales_token),
    )
    assert r.status_code == 400


# ============================================================================
# Phase EX-2: migration 014 expense_lines.category_id FK + backfill.
# ============================================================================


def test_ex2_expense_lines_category_id_column_exists(client):
    """Migration 014 wajib menambah kolom category_id di expense_lines."""
    cols = [r["name"] for r in db.query_all("PRAGMA table_info(expense_lines)")]
    assert "category_id" in cols, f"expense_lines.category_id missing. Cols: {cols}"


def test_ex2_expense_line_backfill_map_present(client):
    """6 subkategori target backfill (Transport Operasional, Hotel Transit, dst)
    harus ada di seed expense_categories."""
    targets = [
        "Transport Operasional", "Hotel Transit", "Konsumsi Karyawan",
        "Internet & Telpon", "ATK", "Insidentil",
    ]
    for name in targets:
        row = db.query_one(
            "SELECT id FROM expense_categories WHERE name = ? AND is_active = 1",
            (name,))
        assert row is not None, f"Subkategori target backfill '{name}' tidak ada di seed"


def test_ex2_line_create_with_category_id(client, sales_token, admin_token):
    """POST /api/expense-reports/{rid}/lines terima category_id (FK) + tulis
    ke kolom baru."""
    pid = _ensure_project(client, admin_token)
    cid_header = _first_expense_category_id()
    # Create report
    r = client.post(
        "/api/expense-reports",
        json={"project_id": pid, "category_id": cid_header,
              "period_from": "2026-09-01", "period_to": "2026-09-30"},
        headers=bearer(sales_token),
    )
    assert r.status_code == 200
    rid = r.json()["id"]

    # Cari kategori berbeda utk line
    cid_line_row = db.query_one(
        "SELECT id FROM expense_categories WHERE group_type = 'expense' "
        "AND parent_id IS NOT NULL AND is_active = 1 AND id != ? LIMIT 1",
        (cid_header,))
    cid_line = cid_line_row["id"] if cid_line_row else cid_header

    r = client.post(
        f"/api/expense-reports/{rid}/lines",
        json={"category_id": cid_line, "description": "test line",
              "unit_price_net": 50000, "qty": 1, "tax_percent": 11.0,
              "date": "2026-09-15"},
        headers=bearer(sales_token),
    )
    assert r.status_code == 200, f"Line create failed: {r.text}"

    line = db.query_one(
        "SELECT category_id FROM expense_lines WHERE report_id = ? ORDER BY id DESC LIMIT 1",
        (rid,))
    assert line["category_id"] == cid_line


def test_ex2_line_create_with_text_only_still_works(client, sales_token, admin_token):
    """Backward-compat: line dengan text `category` (bukan category_id) tetap
    accepted."""
    pid = _ensure_project(client, admin_token)
    cid_header = _first_expense_category_id()
    r = client.post(
        "/api/expense-reports",
        json={"project_id": pid, "category_id": cid_header,
              "period_from": "2026-09-01", "period_to": "2026-09-30"},
        headers=bearer(sales_token),
    )
    rid = r.json()["id"]

    r = client.post(
        f"/api/expense-reports/{rid}/lines",
        json={"category": "Transportasi", "description": "legacy line",
              "unit_price_net": 10000, "qty": 1, "tax_percent": 0,
              "date": "2026-09-15"},
        headers=bearer(sales_token),
    )
    assert r.status_code == 200, f"Legacy line create should work: {r.text}"


def test_ex2_line_create_no_category_at_all_rejected(client, sales_token, admin_token):
    """Line tanpa category_id maupun text category -> 400."""
    pid = _ensure_project(client, admin_token)
    cid_header = _first_expense_category_id()
    r = client.post(
        "/api/expense-reports",
        json={"project_id": pid, "category_id": cid_header,
              "period_from": "2026-09-01", "period_to": "2026-09-30"},
        headers=bearer(sales_token),
    )
    rid = r.json()["id"]

    r = client.post(
        f"/api/expense-reports/{rid}/lines",
        json={"description": "empty cat", "unit_price_net": 10000,
              "qty": 1, "tax_percent": 0, "date": "2026-09-15"},
        headers=bearer(sales_token),
    )
    assert r.status_code == 400
