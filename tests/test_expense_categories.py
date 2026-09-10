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
