"""
Server Socket.IO (real-time) untuk Umar CRM.
Setara dengan konfigurasi socket.io di server.js.

Frontend (index.html) tersambung via socket.io client v4 dan mendengarkan event:
  - wa_status               (status koneksi WhatsApp + QR)
  - wa_new_message          (pesan WA baru masuk/keluar)
  - wa_conversation_updated (ringkasan percakapan diperbarui)
  - data_updated            (sinyal refresh data: 'jamaah', 'transaction', dst.)
"""
import asyncio

import socketio

# AsyncServer dengan CORS terbuka (sama seperti origin '*' di server.js)
sio = socketio.AsyncServer(async_mode="asgi", cors_allowed_origins="*")

# Loop event utama disimpan saat startup agar notify() bisa dipanggil
# dengan aman dari thread lain (mis. background thread WhatsApp).
_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop):
    global _loop
    _loop = loop


def notify(event: str, data=None):
    """
    Emit event Socket.IO secara thread-safe dari konteks sync maupun thread lain.
    Aman dipanggil dari route handler biasa atau dari callback WhatsApp.
    """
    if _loop is None:
        return
    coro = sio.emit(event, data)
    try:
        asyncio.run_coroutine_threadsafe(coro, _loop)
    except RuntimeError:
        pass


@sio.event
async def connect(sid, environ):
    # Saat klien tersambung, kirim status WhatsApp terkini (mirip server.js)
    import whatsapp

    await sio.emit("wa_status", whatsapp.get_status(), to=sid)
