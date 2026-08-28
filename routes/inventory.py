"""
Router Inventory & Aset (I-A..I-x):
- Stok gudang barang jamaah (inventory) + restock.
- Aset perusahaan per-unit (company_assets) + linimasa perpindahan tangan.
- Serah terima perlengkapan ke jamaah + penandaan keberangkatan (depart).

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
"""
import datetime

from fastapi import APIRouter

import db
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    get_setting,
    json_body,
    log_action,
    notify,
    require_role,
)

router = APIRouter(tags=["inventory"])


# ===========================================================================
# INVENTORY GUDANG (barang jamaah)
# ===========================================================================
@router.get("/api/inventory")
async def inventory_list(user=Depends(authenticate_token)):
    return db.query_all("SELECT * FROM inventory ORDER BY item_name ASC", ())


@router.post("/api/inventory")
async def inventory_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    g = body.get
    item_name = (g("item_name") or "").strip()
    if not item_name:
        raise HTTPException(status_code=400, detail="Nama barang wajib diisi.")
    # Inventory kini khusus Barang Jamaah -- Aset Perusahaan punya tabel & alur
    # sendiri (lihat /api/company-assets) karena butuh pelacakan per-unit/pemegang.
    try:
        db.execute(
            "INSERT INTO inventory (item_name, stock, category, min_stock_threshold) VALUES (?, ?, ?, ?)",
            (item_name, int(g("stock") or 0), "Barang Jamaah", int(g("min_stock_threshold") or 20)),
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="Nama barang ini sudah ada di daftar inventory.")
        raise HTTPException(status_code=500, detail=str(e))
    log_action(user, "CREATE_INVENTORY_ITEM", f"Menambah item inventory baru: {item_name}")
    notify("data_updated", "inventory")
    return {"message": "Item inventory baru berhasil ditambahkan."}


@router.put("/api/inventory/{iid}")
async def inventory_update(iid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    g = body.get
    item = db.query_one("SELECT * FROM inventory WHERE id = ?", (iid,))
    if not item:
        raise HTTPException(status_code=404, detail="Item inventory tidak ditemukan")
    db.execute(
        "UPDATE inventory SET item_name = ?, min_stock_threshold = ? WHERE id = ?",
        (g("item_name") or item["item_name"], int(g("min_stock_threshold") or 20), iid),
    )
    log_action(user, "UPDATE_INVENTORY_ITEM", f"Mengubah item inventory: {item['item_name']}")
    notify("data_updated", "inventory")
    return {"message": "Item inventory berhasil diperbarui."}


@router.delete("/api/inventory/{iid}")
async def inventory_delete(iid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    item = db.query_one("SELECT * FROM inventory WHERE id = ?", (iid,))
    if not item:
        raise HTTPException(status_code=404, detail="Item inventory tidak ditemukan")
    db.execute("DELETE FROM inventory WHERE id = ?", (iid,))
    log_action(user, "DELETE_INVENTORY_ITEM", f"Menghapus item inventory: {item['item_name']}")
    notify("data_updated", "inventory")
    return {"message": "Item inventory berhasil dihapus."}


# ===========================================================================
# ASET PERUSAHAAN (laptop, HP, dll) -- per-unit, terpisah dari stok gudang
# barang jamaah. Tiap unit melacak siapa pemegangnya sekarang.
# ===========================================================================
ASSET_CONDITIONS = ("Baik", "Rusak Ringan", "Rusak Berat", "Hilang")


@router.get("/api/company-assets")
async def company_assets_list(user=Depends(authenticate_token)):
    return db.query_all("SELECT * FROM company_assets ORDER BY item_name ASC", ())


@router.get("/api/company-assets/{aid}/history")
async def company_assets_history(aid: int, user=Depends(authenticate_token)):
    return db.query_all(
        "SELECT * FROM asset_transfer_history WHERE asset_id = ? ORDER BY transferred_at DESC", (aid,)
    )


def _record_asset_transfer(asset_id, from_holder, to_holder, transferred_by):
    """Catat satu baris linimasa perpindahan tangan -- dipanggil dari create (kalau
    langsung ada pemegang awal), transfer, dan edit (kalau assigned_to ikut berubah)."""
    db.execute(
        "INSERT INTO asset_transfer_history (asset_id, from_holder, to_holder, transferred_by) "
        "VALUES (?, ?, ?, ?)",
        (asset_id, from_holder, to_holder, transferred_by),
    )


@router.post("/api/company-assets")
async def company_assets_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    g = body.get
    item_name = (g("item_name") or "").strip()
    if not item_name:
        raise HTTPException(status_code=400, detail="Nama aset wajib diisi.")
    condition = g("condition") or "Baik"
    if condition not in ASSET_CONDITIONS:
        raise HTTPException(status_code=400, detail="Kondisi tidak valid.")
    assigned_to = (g("assigned_to") or "").strip() or None
    asset_code = db.next_asset_code()
    new_id, _ = db.execute(
        "INSERT INTO company_assets (item_name, serial_number, condition, assigned_to, assigned_at, notes, asset_code) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            item_name, g("serial_number"), condition, assigned_to,
            datetime.datetime.now().isoformat() if assigned_to else None, g("notes"), asset_code,
        ),
    )
    if assigned_to:
        _record_asset_transfer(new_id, None, assigned_to, user["name"])
    log_action(user, "CREATE_ASSET", f"Menambah aset perusahaan baru: {item_name}")
    notify("data_updated", "company_asset")
    return {"message": "Aset perusahaan baru berhasil ditambahkan."}


@router.put("/api/company-assets/{aid}")
async def company_assets_update(aid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    g = body.get
    asset = db.query_one("SELECT * FROM company_assets WHERE id = ?", (aid,))
    if not asset:
        raise HTTPException(status_code=404, detail="Aset tidak ditemukan")
    condition = g("condition") or "Baik"
    if condition not in ASSET_CONDITIONS:
        raise HTTPException(status_code=400, detail="Kondisi tidak valid.")
    new_assigned = (g("assigned_to") or "").strip() or None
    # assigned_at cuma diperbarui kalau pemegangnya benar-benar berubah lewat form
    # Edit ini -- perpindahan normal sebaiknya lewat aksi "Pindah Tangan" tersendiri.
    assigned_at = asset["assigned_at"]
    if new_assigned != asset["assigned_to"]:
        assigned_at = datetime.datetime.now().isoformat() if new_assigned else None
        _record_asset_transfer(aid, asset["assigned_to"], new_assigned, user["name"])
    db.execute(
        "UPDATE company_assets SET item_name = ?, serial_number = ?, condition = ?, "
        "assigned_to = ?, assigned_at = ?, notes = ? WHERE id = ?",
        (
            g("item_name") or asset["item_name"], g("serial_number"), condition,
            new_assigned, assigned_at, g("notes"), aid,
        ),
    )
    log_action(user, "UPDATE_ASSET", f"Mengubah aset perusahaan: {asset['item_name']}")
    notify("data_updated", "company_asset")
    return {"message": "Aset perusahaan berhasil diperbarui."}


@router.put("/api/company-assets/{aid}/transfer")
async def company_assets_transfer(aid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    asset = db.query_one("SELECT * FROM company_assets WHERE id = ?", (aid,))
    if not asset:
        raise HTTPException(status_code=404, detail="Aset tidak ditemukan")
    # Nilai kosong = dikembalikan ke gudang/belum ditugaskan -- dropdown pemilihan di
    # frontend sudah punya opsi eksplisit untuk itu, jadi tidak perlu lagi dipaksa lewat
    # Edit Aset seperti sebelumnya.
    new_holder = (body.get("assigned_to") or "").strip() or None
    db.execute(
        "UPDATE company_assets SET assigned_to = ?, assigned_at = ? WHERE id = ?",
        (new_holder, datetime.datetime.now().isoformat() if new_holder else None, aid),
    )
    _record_asset_transfer(aid, asset["assigned_to"], new_holder, user["name"])
    log_action(
        user, "TRANSFER_ASSET",
        f"Memindahkan aset {asset['item_name']} dari {asset['assigned_to'] or 'gudang'} ke {new_holder or 'gudang'}",
    )
    notify("data_updated", "company_asset")
    return {"message": f"Aset berhasil dipindahkan ke {new_holder or 'gudang'}."}


@router.delete("/api/company-assets/{aid}")
async def company_assets_delete(aid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    asset = db.query_one("SELECT * FROM company_assets WHERE id = ?", (aid,))
    if not asset:
        raise HTTPException(status_code=404, detail="Aset tidak ditemukan")
    db.execute("DELETE FROM company_assets WHERE id = ?", (aid,))
    log_action(user, "DELETE_ASSET", f"Menghapus aset perusahaan: {asset['item_name']}")
    notify("data_updated", "company_asset")
    return {"message": "Aset perusahaan berhasil dihapus."}


# ===========================================================================
# SERAH TERIMA PERLENGKAPAN KE JAMAAH + PENANDAAN BERANGKAT
# ===========================================================================
@router.post("/api/jamaah/{jid}/inventory")
async def jamaah_inventory(jid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    items = body.get("items")
    if not items or not isinstance(items, list) or len(items) == 0:
        raise HTTPException(status_code=400, detail="Tidak ada barang yang dipilih")

    row = db.query_one("SELECT paid_amount, total_price FROM jamaah WHERE id = ?", (jid,))
    if not row:
        raise HTTPException(status_code=404, detail="Jamaah tidak ditemukan")

    # Ambang minimum DP kini dapat dikonfigurasi via Pengaturan (default 75%).
    try:
        min_pct = float(get_setting("equipment_min_dp_percent", "75"))
    except (TypeError, ValueError):
        min_pct = 75.0
    threshold = (row["total_price"] or 0) * min_pct / 100.0
    if (row["paid_amount"] or 0) < threshold and user["role"] != "admin":
        pct_label = int(min_pct) if float(min_pct).is_integer() else min_pct
        raise HTTPException(
            status_code=400,
            detail=f"Gagal! SOP GERBANG: Jamaah harus melunasi minimal {pct_label}% tagihan sebelum "
            "pengambilan fisik perlengkapan.",
        )

    success = 0
    for item_name in items:
        _, changed = db.execute(
            "UPDATE inventory SET stock = stock - 1 WHERE item_name = ? AND stock > 0", (item_name,)
        )
        if changed > 0:
            db.execute(
                "INSERT INTO jamaah_inventory (jamaah_id, item_name) VALUES (?, ?)", (jid, item_name)
            )
            success += 1
    log_action(user, "HANDOVER", f"Serah terima {success} logistik ke Jamaah ID {jid}")
    notify("data_updated", "inventory")
    notify("data_updated", "jamaah")
    return {"message": f"Berhasil menyerahkan {success} item perlengkapan."}


@router.put("/api/jamaah/{jid}/depart")
async def jamaah_depart(jid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    db.execute("UPDATE jamaah SET trip_status = 'OnTrip' WHERE id = ?", (jid,))
    db.execute("UPDATE jamaah SET status = 'On Trip' WHERE id = ?", (jid,))
    log_action(user, "DEPARTURE", f"Jamaah ID {jid} diberangkatkan (On Trip)")
    notify("data_updated", "jamaah")
    return {"message": "Status jamaah diubah menjadi ON TRIP."}


@router.post("/api/inventory/restock")
async def inventory_restock(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "ops")
    item_name = body.get("item_name")
    qty = body.get("qty")
    cost = body.get("cost")
    db.execute("UPDATE inventory SET stock = stock + ? WHERE item_name = ?", (int(qty), item_name))

    if cost and int(cost) > 0:
        db.execute(
            "INSERT INTO transactions (type, category, amount, description) VALUES (?, ?, ?, ?)",
            ("expense", "operational", int(cost), f"Pembelian Logistik Gudang: {qty}x {item_name}"),
        )
        log_action(user, "RESTOCK_EXPENSE", f"Beli {qty}x {item_name} seharga Rp {cost}")
        notify("data_updated", "transaction")
    else:
        log_action(user, "RESTOCK", f"Tambah stok {qty}x {item_name}")

    notify("data_updated", "inventory")
    return {"message": f"Stok {item_name} berhasil ditambah."}
