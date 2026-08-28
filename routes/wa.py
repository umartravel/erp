"""
Router WhatsApp:
- Status/Connect/Logout koneksi WA (via modul `whatsapp`).
- Send pesan (single atau broadcast semua jamaah, dengan jeda anti-banned).
- Remind Payment: broadcast pengingat ke jamaah yang belum lunas.
- Template pesan (CRUD ringan): daftar template siap-pakai.

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
"""
import asyncio

from fastapi import APIRouter

import db
import whatsapp as wa
from deps import (
    Depends,
    HTTPException,
    authenticate_token,
    fmt_id,
    json_body,
)

router = APIRouter(tags=["wa"])


@router.get("/api/wa/status")
async def wa_status(user=Depends(authenticate_token)):
    return wa.get_status()


@router.post("/api/wa/connect")
async def wa_connect(user=Depends(authenticate_token)):
    wa.connect_to_whatsapp()
    return {"message": "Membuka koneksi WhatsApp..."}


@router.post("/api/wa/logout")
async def wa_logout(user=Depends(authenticate_token)):
    await wa.logout_whatsapp()
    return {"message": "WhatsApp berhasil diputus. Silakan scan ulang."}


@router.post("/api/wa/send")
async def wa_send(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    target = body.get("target")
    message = body.get("message")
    if wa.get_status()["status"] != "connected":
        raise HTTPException(status_code=400, detail="WhatsApp belum terhubung!")

    if target == "all":
        rows = db.query_all("SELECT phone FROM jamaah", ())
        count = 0
        for row in rows:
            if row["phone"]:
                await wa.send_message(row["phone"], message)
                count += 1
                await asyncio.sleep(1.5)  # jeda anti-banned
        return {"message": f"Broadcast berhasil dikirim ke {count} jamaah."}
    else:
        success = await wa.send_message(target, message)
        if success:
            return {"message": "Pesan berhasil terkirim."}
        raise HTTPException(status_code=500, detail="Gagal mengirim pesan.")


@router.post("/api/wa/remind-payment")
async def wa_remind_payment(user=Depends(authenticate_token)):
    # FIX (integritas): jangkau SEMUA jamaah dengan sisa tagihan & booking aktif,
    # bukan hanya status 'Terdaftar'. Sebelumnya jamaah ber-status 'Lead'/'Waitlisted'
    # yang sudah berutang tidak pernah dapat pengingat.
    rows = db.query_all(
        "SELECT name, phone, total_price, paid_amount FROM jamaah "
        "WHERE total_price > paid_amount "
        "AND status NOT IN ('Lead - Follow Up', 'Cancelled', 'Lunas')",
        (),
    )
    count = 0
    for j in rows:
        if j["phone"]:
            sisa = (j["total_price"] or 0) - (j["paid_amount"] or 0)
            msg = (
                f"Assalamu'alaikum Bpk/Ibu {j['name']},\n\n"
                f"Kami dari *Umar Travel* ingin menginformasikan bahwa paket Umroh Anda "
                f"masih memiliki sisa tagihan sebesar *Rp {fmt_id(sisa)}*.\n\n"
                f"Mohon segera melengkapi pembayaran agar proses Visa dan manifestasi "
                f"penerbangan dapat segera kami proses. Jazakumullah khairan."
            )
            if wa.get_status()["status"] == "connected":
                await wa.send_message(j["phone"], msg)
                count += 1
                await asyncio.sleep(1.5)
    return {"message": f"Pengingat otomatis berhasil dikirim ke {count} jamaah yang belum lunas."}


# ===========================================================================
# TEMPLATE PESAN WA (dulu diberi label "LIVE CHAT" -- fitur live-chat sudah
# dihapus, tapi tabel wa_templates masih dipakai untuk quick-reply text
# di halaman broadcast/remind-payment).
# ===========================================================================
@router.get("/api/wa/templates")
async def wa_templates(user=Depends(authenticate_token)):
    return db.query_all("SELECT * FROM wa_templates", ()) or []


@router.post("/api/wa/templates")
async def wa_template_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    last_id, _ = db.execute(
        "INSERT INTO wa_templates (title, content) VALUES (?, ?)",
        (body.get("title"), body.get("content")),
    )
    return {"id": last_id}


@router.delete("/api/wa/templates/{tid}")
async def wa_template_delete(tid: int, user=Depends(authenticate_token)):
    db.execute("DELETE FROM wa_templates WHERE id = ?", (tid,))
    return {"success": True}
