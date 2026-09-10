"""
Phase F1b: helper resolve nama subcategory -> id, cached.
Dipakai routes yang INSERT ke transactions supaya propagate category_id
tanpa lookup manual tiap kali.
"""
import db

_CACHE: dict[str, int] = {}


def resolve_cat_id(name: str) -> int | None:
    """Return id subcategory expense_categories dgn `name` exact match, atau
    None kalau tidak ketemu (mis. taxonomy diubah admin, atau typo).

    Cache in-memory. Kalau kategori di-rename/nonaktifkan setelah server hidup,
    dev harus restart -- taxonomy jarang berubah utk MVP."""
    if name in _CACHE:
        return _CACHE[name]
    row = db.query_one(
        "SELECT id FROM expense_categories WHERE name = ? AND is_active = 1 LIMIT 1",
        (name,))
    cid = row["id"] if row else None
    if cid is not None:
        _CACHE[name] = cid
    return cid


CAT_PAYMENT_JAMAAH = "Payment Jamaah"
CAT_REFUND_JAMAAH = "Refund Jamaah"
CAT_GAJI_TUNJANGAN = "Gaji Pokok + Tunjangan"
CAT_KOMISI_AGEN = "Komisi Agen"
CAT_EXTRAS_JAMAAH = "Extras Jamaah (koper/seragam)"
