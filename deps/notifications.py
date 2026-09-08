"""
Phase 8c-1: Helper insert notifikasi in-app ke tabel `user_notifications`.

Helper ini dipanggil dari trigger points (expense submit/approve/reject,
refund pending, commission pending, incident critical, target achieved,
vendor at risk). Insert 1 row per (user_id, event) supaya bell counter
per-user tetap akurat.

API:
- notify_user(uid, kind, title, body=None, link=None)
- notify_users(uid_list, kind, title, body=None, link=None)
- notify_role(role, kind, title, body=None, link=None) -- broadcast ke
  semua user role tertentu (mis. semua management approver).

Semua fire-and-forget: kegagalan insert TIDAK boleh block user action
yg trigger. Wrap di try/except supaya tidak throw.
"""
import db
import realtime


def notify_user(user_id: int, kind: str, title: str,
                body: str | None = None, link: str | None = None) -> None:
    """Insert 1 notif untuk 1 user. Fire-and-forget."""
    if not user_id or not kind or not title:
        return
    try:
        db.execute(
            "INSERT INTO user_notifications (user_id, kind, title, body, link) "
            "VALUES (?, ?, ?, ?, ?)",
            (user_id, kind, title[:500], body, link),
        )
        # Broadcast socket event supaya bell UI langsung refresh counter
        # (client filter oleh user_id di frontend / re-fetch counter).
        realtime.notify("user_notification", {"user_id": user_id, "kind": kind})
    except Exception:  # noqa: BLE001
        # Silent -- notif failure jangan block action user (ex: submit expense).
        pass


def notify_users(user_ids: list[int], kind: str, title: str,
                 body: str | None = None, link: str | None = None) -> None:
    """Insert notif ke banyak user (mis. semua approver management)."""
    for uid in user_ids or []:
        notify_user(uid, kind, title, body, link)


def notify_role(role: str, kind: str, title: str,
                body: str | None = None, link: str | None = None) -> None:
    """Broadcast ke semua user dgn role tertentu."""
    if not role:
        return
    try:
        rows = db.query_all("SELECT id FROM users WHERE role = ?", (role,))
        for r in rows:
            notify_user(r["id"], kind, title, body, link)
    except Exception:  # noqa: BLE001
        pass
