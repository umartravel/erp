"""
Manajemen WhatsApp Bot untuk Umar CRM (versi Python).
Setara dengan whatsapp.js (Baileys) pada versi Node.js.

Karena Baileys (Node) tidak punya padanan resmi di Python, modul ini memakai
`neonize` (berbasis whatsmeow/Go) sebagai backend asli, dengan FALLBACK STUB
otomatis bila neonize belum terpasang -> aplikasi tetap berjalan penuh.

Antarmuka publik (sama persis perannya dengan whatsapp.js):
    connect_to_whatsapp()         -> mulai/segarkan koneksi (non-blocking)
    await logout_whatsapp()       -> putus & hapus sesi
    get_status()                  -> {"status": ..., "qr": ...}
    await send_message(jid, text, media_obj=None) -> bool

Event dikirim ke frontend lewat realtime.notify():
    status_change      -> wa_status
    new_message        -> wa_new_message
    conversation_updated -> wa_conversation_updated
"""
import asyncio
import base64
import os
import re
import shutil
import threading
import time
from datetime import datetime, timezone

import db
import realtime

# Status: disconnected, connecting, qr, connected
_status = "disconnected"
_qr_data_url = ""
_backend = None  # instance backend aktif (Neonize / Stub)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
# Media WA disimpan di folder PRIVAT (sama dengan dokumen PII), dilayani lewat
# route /uploads/{file} yang ber-autentikasi di app.py.
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads_private")
# Sesi neonize (format berbeda dari Baileys lama yang sudah tidak dipakai).
NEONIZE_SESSION = os.path.join(BASE_DIR, "neonize_auth.sqlite3")

# Mode simulasi: bila true, stub menganggap pengiriman "berhasil" untuk
# menguji alur CRM tanpa WhatsApp asli. Aktifkan via env WA_SIMULATE=1.
SIMULATE = os.environ.get("WA_SIMULATE", "0") == "1"


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _set_status(status, qr=""):
    global _status, _qr_data_url
    _status = status
    _qr_data_url = qr
    realtime.notify("wa_status", {"status": status, "qr": qr})


def get_status():
    return {"status": _status, "qr": _qr_data_url}


def _format_jid(jid_or_phone: str) -> str:
    if any(s in jid_or_phone for s in ("@lid", "@g.us", "@s.whatsapp.net")):
        return jid_or_phone
    phone = re.sub(r"\D", "", jid_or_phone)
    if phone.startswith("0"):
        phone = "62" + phone[1:]
    return f"{phone}@s.whatsapp.net"


# ===========================================================================
# Backend STUB (fallback) - dipakai bila neonize tidak terpasang
# ===========================================================================
class StubBackend:
    name = "stub"

    def connect(self):
        # Tanpa WhatsApp asli; tetap "disconnected" (atau anggap tersambung saat SIMULATE)
        if SIMULATE:
            _set_status("connected")
            print("[WA] Mode SIMULASI aktif - pengiriman dianggap berhasil (tanpa WA asli).")
        else:
            _set_status("disconnected")
            print(
                "[WA] Backend WhatsApp asli (neonize) tidak tersedia. "
                "Pasang `neonize` lalu restart, atau jalankan dengan WA_SIMULATE=1 untuk uji coba."
            )

    async def logout(self):
        _set_status("disconnected")

    async def send(self, jid_or_phone, message, media_obj=None):
        if SIMULATE:
            print(f"[WA-SIMULASI] -> {jid_or_phone}: {message[:60]}")
            return True
        print("[WA] Tidak terkirim: WhatsApp belum terhubung.")
        return False


# ===========================================================================
# Backend NEONIZE (asli) - berbasis whatsmeow
# ===========================================================================
class NeonizeBackend:
    """
    Integrasi neonize. Beberapa detail API (terutama penangkapan QR dan
    struktur objek pesan) dapat berbeda antar versi neonize -> seluruh akses
    dibungkus guard agar kegagalan tidak menjatuhkan aplikasi. Sesuaikan
    dengan versi neonize terpasang bila perlu (lihat dokumentasi neonize).
    """

    name = "neonize"

    def __init__(self):
        from neonize.client import NewClient  # noqa: F401  (validasi import)

        self.NewClient = NewClient
        self.client = None
        self._thread = None

    # -- util QR -> data URL PNG (frontend menampilkan <img src=...>) --
    @staticmethod
    def _qr_to_data_url(qr_string: str) -> str:
        try:
            import io

            import qrcode

            img = qrcode.make(qr_string)
            buf = io.BytesIO()
            img.save(buf, format="PNG")
            b64 = base64.b64encode(buf.getvalue()).decode()
            return f"data:image/png;base64,{b64}"
        except Exception:  # noqa: BLE001
            return ""

    def connect(self):
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self):
        _set_status("connecting")
        try:
            self._build_client()
            # connect() bersifat blocking; dijalankan di thread khusus ini.
            self.client.connect()
        except Exception as e:  # noqa: BLE001
            print(f"[WA] Error inisialisasi neonize: {e}")
            _set_status("disconnected")

    def _build_client(self):
        from neonize.events import (  # type: ignore
            ConnectedEv,
            MessageEv,
            PairStatusEv,
        )

        # qr_callback: dipanggil neonize dengan string QR (bila didukung versi ini)
        def on_qr(_client, qr_string):
            _set_status("qr", self._qr_to_data_url(qr_string))

        try:
            self.client = self.NewClient(NEONIZE_SESSION, on_qr=on_qr)
        except TypeError:
            # Versi neonize tanpa parameter on_qr -> QR akan tampil di terminal.
            self.client = self.NewClient(NEONIZE_SESSION)
            print("[WA] Versi neonize ini menampilkan QR di terminal. Silakan scan dari terminal.")

        @self.client.event(ConnectedEv)
        def _on_connected(_client, _ev):  # noqa: ANN001
            _set_status("connected")
            print("✅ WhatsApp Bot CRM Umar Berhasil Terhubung (neonize)!")

        @self.client.event(PairStatusEv)
        def _on_pair(_client, _ev):  # noqa: ANN001
            _set_status("connected")

        @self.client.event(MessageEv)
        def _on_message(_client, message):  # noqa: ANN001
            try:
                self._handle_incoming(message)
            except Exception as e:  # noqa: BLE001
                print(f"[WA] Gagal memproses pesan masuk: {e}")

    def _handle_incoming(self, message):
        """Ekstrak teks/media dari objek pesan neonize lalu simpan ke DB."""
        info = getattr(message, "Info", None)
        source = getattr(info, "MessageSource", None) if info else None
        remote_jid = ""
        is_from_me = False
        if source is not None:
            chat = getattr(source, "Chat", None)
            remote_jid = getattr(chat, "User", "") or str(chat) if chat else ""
            # Bentuk JID lengkap
            server = getattr(chat, "Server", "s.whatsapp.net") if chat else "s.whatsapp.net"
            if remote_jid and "@" not in remote_jid:
                remote_jid = f"{remote_jid}@{server}"
            is_from_me = bool(getattr(source, "IsFromMe", False))

        if not remote_jid or "status" in remote_jid or "g.us" in remote_jid:
            return

        msg = getattr(message, "Message", None)
        text = ""
        media_url = None
        media_type = None
        if msg is not None:
            text = getattr(msg, "conversation", "") or ""
            ext_text = getattr(msg, "extendedTextMessage", None)
            if not text and ext_text is not None:
                text = getattr(ext_text, "text", "") or ""

            # Unduh media bila ada (image/video/audio/document)
            for attr, mtype, ext in (
                ("imageMessage", "image", "jpg"),
                ("videoMessage", "video", "mp4"),
                ("audioMessage", "audio", "ogg"),
                ("documentMessage", "document", "bin"),
            ):
                if getattr(msg, attr, None) is not None and getattr(msg, attr).ListFields():
                    try:
                        data = self.client.download_any(msg)
                        fname = f"wa_{int(time.time()*1000)}.{ext}"
                        os.makedirs(UPLOAD_DIR, exist_ok=True)
                        with open(os.path.join(UPLOAD_DIR, fname), "wb") as f:
                            f.write(data)
                        media_url = f"/uploads/{fname}"
                        media_type = mtype
                        cap = getattr(getattr(msg, attr), "caption", "")
                        text = cap or text or f"[Lampiran {mtype}]"
                    except Exception as e:  # noqa: BLE001
                        print(f"[WA] Gagal download media: {e}")
                        text = text or "[Gagal memuat media]"
                    break

        # Pesan masuk/keluar tidak lagi disimpan ke DB -- tim UMAR chat via WA HP
        # masing-masing, ERP hanya untuk broadcast/auto-remind via /api/wa/send.
        # (Modul Live Chat WhatsApp dihapus 2026-08-17.)

    async def logout(self):
        try:
            if self.client:
                await asyncio.to_thread(self.client.logout)
        except Exception as e:  # noqa: BLE001
            print(f"[WA] Error logout: {e}")
        _set_status("disconnected")
        self.client = None

    async def send(self, jid_or_phone, message, media_obj=None):
        if _status != "connected" or not self.client:
            print("[WA] Tidak terkirim: WhatsApp belum terhubung.")
            return False
        jid = _format_jid(jid_or_phone)
        try:
            if media_obj and media_obj.get("url"):
                # url berbentuk /uploads/<nama> -> petakan ke folder privat
                file_path = os.path.join(UPLOAD_DIR, os.path.basename(media_obj["url"]))
                if os.path.exists(file_path):
                    mtype = media_obj.get("type")
                    if mtype == "image":
                        await asyncio.to_thread(
                            self.client.send_image, jid, file_path, message or ""
                        )
                    elif mtype == "video":
                        await asyncio.to_thread(
                            self.client.send_video, jid, file_path, message or ""
                        )
                    elif mtype == "audio":
                        await asyncio.to_thread(self.client.send_audio, jid, file_path)
                    else:
                        await asyncio.to_thread(
                            self.client.send_document,
                            jid,
                            file_path,
                            media_obj.get("fileName", "document"),
                            message or "",
                        )
                    return True
            await asyncio.to_thread(self.client.send_message, jid, message)
            return True
        except Exception as e:  # noqa: BLE001
            print(f"[WA] Gagal mengirim pesan: {e}")
            return False


# ---------------------------------------------------------------------------
# Pemilihan backend + API publik
# ---------------------------------------------------------------------------
def _get_backend():
    global _backend
    if _backend is not None:
        return _backend
    # WA_SIMULATE=1 -> PAKSA StubBackend, jangan colek neonize sama sekali.
    # Penting utk pytest/CI: neonize versi baru bs instantiate sukses lalu
    # BLOCK di connect() menunggu QR/network -> pytest hang.
    if SIMULATE:
        _backend = StubBackend()
        return _backend
    try:
        _backend = NeonizeBackend()
        print("[WA] Backend WhatsApp: neonize (asli).")
    except Exception as e:  # noqa: BLE001  (import gagal / neonize tidak ada)
        _backend = StubBackend()
        print(f"[WA] neonize tidak tersedia ({e}). Memakai backend stub.")
    return _backend


def connect_to_whatsapp():
    if _status in ("connected", "connecting"):
        return
    _get_backend().connect()


async def logout_whatsapp():
    await _get_backend().logout()
    # Bersihkan sesi neonize lama agar bisa scan ulang nomor baru
    try:
        if os.path.exists(NEONIZE_SESSION):
            os.remove(NEONIZE_SESSION)
        old_baileys = os.path.join(os.path.dirname(NEONIZE_SESSION), "baileys_auth_info")
        if os.path.isdir(old_baileys):
            shutil.rmtree(old_baileys, ignore_errors=True)
    except Exception as e:  # noqa: BLE001
        print(f"[WA] Gagal hapus sesi: {e}")


async def send_message(jid_or_phone, message, media_obj=None):
    return await _get_backend().send(jid_or_phone, message, media_obj)
