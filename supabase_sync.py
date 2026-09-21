"""
Phase SS-1.2 (2026-09-21): Core sync engine Supabase closings -> UMAR jamaah.

One-way sync: Supabase = source of truth untuk biodata + payment tracking.
UMAR = replica + extension (pipeline, journal PSAK, incidents dll).

Sync loop (dipanggil dari routes/supabase_sync.py atau scheduler app.py):
1. query_table('closings', filter='updated_at=gt.<last_sync_ts>')
2. Untuk setiap row: upsert ke UMAR jamaah + delta payment -> journal
3. detect_soft_deletes(): row di UMAR yg tidak ada di Supabase -> tag
   supabase_missing_since (BUKAN DELETE)
4. update sync_state.last_sync_ts + log semua ke sync_log

Anti-human-error: UMAR append-only. Hard-DELETE Supabase tidak propagate.
"""
import datetime
import json
import logging

import db
import journal_engine as je
import supabase_client

_log = logging.getLogger("supabase_sync")

# Mapping STATPAY Supabase -> UMAR payment_status.
_STATPAY_MAP = {
    "DP": "Cicilan",
    "LUNAS": "Lunas",
    "BELUM LUNAS": "Terdaftar",
}


def _parse_date_ddmmyyyy(s: str) -> str | None:
    """Convert 'DD/MM/YYYY' Supabase -> 'YYYY-MM-DD' UMAR. Return None kalau invalid.

    Priority: cek YYYY-MM-DD dulu (ISO format) supaya '2026-09-18' tidak
    di-mis-parse sebagai DD=2026/MM=09/YYYY=18. Baru fallback ke DD/MM/YYYY
    (format Supabase 'TANGGAL ORDER' seperti '18/09/2026').
    """
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    if not s:
        return None
    # Cek ISO YYYY-MM-DD dulu.
    try:
        datetime.date.fromisoformat(s)
        return s
    except ValueError:
        pass
    # Fallback: DD/MM/YYYY atau DD-MM-YYYY.
    for sep in ("/", "-"):
        parts = s.split(sep)
        if len(parts) == 3:
            try:
                dd, mm, yyyy = parts
                if len(yyyy) == 2:
                    yyyy = "20" + yyyy
                # Basic sanity check: DD/MM must be 1-2 digits, YYYY 4.
                if len(yyyy) != 4 or int(dd) > 31 or int(mm) > 12:
                    continue
                return f"{int(yyyy):04d}-{int(mm):02d}-{int(dd):02d}"
            except (ValueError, TypeError):
                continue
    return None


def _map_closing_to_jamaah(row: dict) -> dict:
    """Transform Supabase closings row (CAPS) -> UMAR jamaah dict (snake_case).

    Return dict siap INSERT/UPDATE. NULL-safe.
    """
    stat_raw = (row.get("STATPAY") or "").strip().upper()
    return {
        "external_id": row.get("ID JAMAAH"),
        "name": row.get("NAMA JAMAAH") or row.get("NAMA PEMESAN") or "",
        "orderer_name": row.get("NAMA PEMESAN"),
        "order_date": _parse_date_ddmmyyyy(row.get("TANGGAL ORDER")),
        "package_type": row.get("PAKET"),
        "package_ext_id": row.get("ID PAKET"),
        "admin_marketing": row.get("ADMIN MARKETING"),
        "channel": row.get("CHANNEL"),
        "sub_channel": row.get("SUB CHANNEL"),
        "agent_name_raw": row.get("NAMA_AGEN") or "",
        "agent_ext_id": row.get("ID_AGEN"),
        "total_price": int(row.get("HARGA") or 0),
        "paid_amount": int(row.get("TOTAL BAYAR") or 0),
        "dp_amount": int(row.get("DP") or 0),
        "payment_status": _STATPAY_MAP.get(stat_raw, "Terdaftar"),
        # Biodata (nullable di UMAR)
        "gender": row.get("JENIS KELAMIN"),
        "birth_place": row.get("TEMPAT LAHIR"),
        "birth_date": _parse_date_ddmmyyyy(row.get("TANGGAL LAHIR")),
        "age": row.get("USIA"),
        "phone": row.get("NO. TELP JAMAAH"),
        "family_phone": row.get("NO. TELP KELUARGA"),
        "email": row.get("EMAIL JAMAAH"),
        "father_name": row.get("NAMA AYAH"),
        "citizenship": row.get("KEWARGANEGARAAN"),
        "identity_type": row.get("JENIS IDENTITAS"),
        "nik": _sanitize_nik(row.get("NOMOR IDENTITAS")),
        "education": row.get("PENDIDIKAN"),
        "job": row.get("PEKERJAAN"),
        "marital_status": row.get("STATUS PERNIKAHAN"),
        "relation": row.get("HUBUNGAN"),
        "address": row.get("ALAMAT"),
        "province": row.get("PROVINSI"),
        "city": row.get("KAB/KOTA"),
        "subdistrict": row.get("KECAMATAN"),
        "village": row.get("KELURAHAN"),
        "passport_number": row.get("NO PASPORT"),
        "passport_issued": _parse_date_ddmmyyyy(row.get("ISSUED")),
        "passport_expiry": _parse_date_ddmmyyyy(row.get("EXPIRY")),
        "passport_location": row.get("KANTOR IMIGRASI"),
        "equipment_package": row.get("PERLENGKAPAN"),
        "room_type": row.get("REQ KAMAR"),
    }


def _resolve_agent_id(name_raw: str | None, ext_id: str | None) -> int | None:
    """Match agent_name/ext_id ke UMAR agents.id. Return None kalau tidak ada.

    Note: UMAR `agents` table TIDAK punya kolom external_id, jadi ext_id
    diabaikan untuk sekarang -- resolve hanya via name UPPER match.
    Kalau future add agents.external_id, extend lookup di sini.
    """
    if name_raw:
        r = db.query_one(
            "SELECT id FROM agents WHERE UPPER(name) = UPPER(?) LIMIT 1",
            (name_raw.strip(),),
        )
        if r:
            return r["id"]
    return None


def _sanitize_nik(raw: str | None) -> str | None:
    """Handle NIK garbage dari Excel export admin marketing.

    Common issues di Supabase closings:
    - Empty string "" (53 rows) -> NULL
    - Scientific notation "3,27512E+15" -> NULL (data lost precision di Excel)
    - Whitespace-only -> NULL
    - Bukan 16 digit angka valid -> NULL (best-effort)

    Return valid 16-digit NIK string atau None.
    """
    if not raw or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    if "E+" in s or "e+" in s or "," in s or "." in s:
        # Scientific notation atau desimal -> data rusak
        return None
    # Best-effort: 16 digit numeric.
    if s.isdigit() and len(s) == 16:
        return s
    # Longer/shorter: keep as-is (mungkin passport atau ID asing), tapi hanya
    # kalau ISO-alphanumeric (bukan format aneh).
    if s.replace("-", "").replace("/", "").isalnum() and 5 <= len(s) <= 30:
        return s
    return None


def _log_sync(action: str, external_id: str | None,
              old: dict | None = None, new: dict | None = None,
              error: str | None = None) -> None:
    """Write ke sync_log audit trail."""
    db.execute(
        "INSERT INTO sync_log (action, external_id, old_json, new_json, error_msg) "
        "VALUES (?, ?, ?, ?, ?)",
        (
            action,
            external_id,
            json.dumps(old, default=str) if old else None,
            json.dumps(new, default=str) if new else None,
            error,
        ),
    )


def _get_state(key: str, default: str = "") -> str:
    r = db.query_one("SELECT value FROM sync_state WHERE key = ?", (key,))
    return r["value"] if r else default


def _set_state(key: str, value: str) -> None:
    db.execute(
        "INSERT INTO sync_state (key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (key, value, datetime.datetime.utcnow().isoformat(sep=" ", timespec="seconds")),
    )


def _upsert_jamaah(mapped: dict) -> tuple[str, dict | None]:
    """
    INSERT atau UPDATE UMAR jamaah dari mapped dict. Return (action, old_row)
    action: 'insert' atau 'update' atau 'noop'.
    """
    ext_id = mapped["external_id"]
    if not ext_id:
        return ("noop", None)

    existing = db.query_one(
        "SELECT * FROM jamaah WHERE external_id = ?", (ext_id,)
    )

    # Resolve agent_id (nullable).
    agent_id = _resolve_agent_id(mapped["agent_name_raw"], mapped["agent_ext_id"])

    # Build kolom set + values common untuk INSERT + UPDATE.
    # Kolom UMAR-only yg TIDAK di-touch dari sync (preserve): pipeline_stage,
    # visa_status, trip_status, boq_id, boq_snapshot_*, sales_id, notes, doc_*,
    # lead_source, next_follow_up, last_contact, cancel_reason, mahram, dsb.
    payload = {
        "name": mapped["name"],
        "orderer_name": mapped["orderer_name"],
        "order_date": mapped["order_date"],
        "package_type": mapped["package_type"],
        "package_ext_id": mapped["package_ext_id"],
        "admin_marketing": mapped["admin_marketing"],
        "channel": mapped["channel"],
        "sub_channel": mapped["sub_channel"],
        "agent_name_raw": mapped["agent_name_raw"],
        "agent_ext_id": mapped["agent_ext_id"],
        "total_price": mapped["total_price"],
        "paid_amount": mapped["paid_amount"],
        "dp_amount": mapped["dp_amount"],
        "payment_status": mapped["payment_status"],
        "gender": mapped["gender"],
        "birth_place": mapped["birth_place"],
        "birth_date": mapped["birth_date"],
        "age": mapped["age"],
        "phone": mapped["phone"],
        "family_phone": mapped["family_phone"],
        "email": mapped["email"],
        "father_name": mapped["father_name"],
        "citizenship": mapped["citizenship"],
        "identity_type": mapped["identity_type"],
        "nik": mapped["nik"],
        "education": mapped["education"],
        "job": mapped["job"],
        "marital_status": mapped["marital_status"],
        "relation": mapped["relation"],
        "address": mapped["address"],
        "province": mapped["province"],
        "city": mapped["city"],
        "subdistrict": mapped["subdistrict"],
        "village": mapped["village"],
        "passport_number": mapped["passport_number"],
        "passport_issued": mapped["passport_issued"],
        "passport_expiry": mapped["passport_expiry"],
        "passport_location": mapped["passport_location"],
        "equipment_package": mapped["equipment_package"],
        "room_type": mapped["room_type"],
    }

    if existing is None:
        pipeline = "Booked" if mapped["paid_amount"] > 0 else "Terdaftar"
        cols = ["external_id"] + list(payload.keys()) + [
            "agent_id", "pipeline_stage", "status", "supabase_missing_since",
        ]
        values = [ext_id] + list(payload.values()) + [
            agent_id, pipeline, mapped["payment_status"], None,
        ]
        placeholders = ", ".join("?" * len(cols))
        col_list = ", ".join(cols)
        db.execute(f"INSERT INTO jamaah ({col_list}) VALUES ({placeholders})", tuple(values))
        return ("insert", None)

    # UPDATE existing. `agent_id` di-COALESCE supaya kalau resolve gagal
    # (agent_id=None), tidak overwrite existing.
    set_clauses = [f"{k}=?" for k in payload.keys()]
    values = list(payload.values())
    set_clauses.append("agent_id=COALESCE(?, agent_id)")
    values.append(agent_id)
    set_clauses.append("supabase_missing_since=NULL")
    db.execute(
        f"UPDATE jamaah SET {', '.join(set_clauses)} WHERE external_id=?",
        tuple(values) + (ext_id,),
    )
    return ("update", dict(existing))


def _record_delta_payment(ext_id: str, old_paid: int, new_paid: int,
                          name: str) -> None:
    """Kalau paid_amount naik, INSERT transactions row + journal Dr Bank/Cr 2101."""
    delta = new_paid - old_paid
    if delta <= 0:
        if delta < 0:
            _log.warning("Payment rollback detected ext_id=%s delta=%d", ext_id, delta)
        return

    jamaah_row = db.query_one("SELECT id FROM jamaah WHERE external_id = ?", (ext_id,))
    if not jamaah_row:
        return
    jid = jamaah_row["id"]

    tx_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, "
        "reference_id, status) VALUES ('income', 'payment', ?, ?, ?, 'POSTED')",
        (delta, f"Payment sync Supabase: {name} (delta Rp {delta:,})".replace(",", "."),
         jid),
    )
    # Dr 1101 Kas / Cr 2101 Pendapatan Diterima Dimuka.
    try:
        je.post_journal(tx_id, [
            (je.coa_id("1101"), delta, 0, f"Payment sync {ext_id}"),
            (je.coa_id("2101"), 0, delta, f"Payment sync {ext_id}"),
        ])
    except Exception as e:  # noqa: BLE001
        _log.error("Journal post failed for ext_id=%s tx=%s: %s", ext_id, tx_id, e)
        db.execute("DELETE FROM transactions WHERE id = ?", (tx_id,))
        raise


async def sync_closings(full: bool = False) -> dict:
    """
    Sync Supabase closings -> UMAR jamaah.

    - `full=True`: query semua closings (backfill mode)
    - `full=False`: query WHERE updated_at > last_sync_ts (incremental)

    Return: {'inserted': N, 'updated': N, 'soft_deleted': N, 'errors': N,
             'run_ts': ..., 'rows_processed': N}
    """
    run_ts = datetime.datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    inserted = updated = soft_deleted = errors = 0

    filter_str = ""
    if not full:
        last_sync_ts = _get_state("last_sync_ts")
        if last_sync_ts:
            filter_str = f"updated_at=gt.{last_sync_ts}"

    try:
        rows = await supabase_client.query_table(
            "closings", filter_str=filter_str, order_by="updated_at.asc"
        )
    except Exception as e:  # noqa: BLE001
        _log_sync("sync_failed", None, error=str(e))
        raise

    for row in rows:
        ext_id = row.get("ID JAMAAH")
        try:
            mapped = _map_closing_to_jamaah(row)
            old_paid = 0
            existing = db.query_one(
                "SELECT paid_amount FROM jamaah WHERE external_id = ?", (ext_id,)
            )
            if existing:
                old_paid = int(existing["paid_amount"] or 0)

            action, old_row = _upsert_jamaah(mapped)
            if action == "insert":
                inserted += 1
                if mapped["paid_amount"] > 0:
                    _record_delta_payment(ext_id, 0, mapped["paid_amount"], mapped["name"])
            elif action == "update":
                updated += 1
                if mapped["paid_amount"] > old_paid:
                    _record_delta_payment(ext_id, old_paid, mapped["paid_amount"], mapped["name"])
            _log_sync(action, ext_id, old=old_row, new=mapped)
        except Exception as e:  # noqa: BLE001
            errors += 1
            _log_sync("error", ext_id, error=str(e))
            _log.exception("Failed to sync row ext_id=%s", ext_id)

    # Soft-delete detection (hanya di full sync -- kita punya semua ext_id).
    if full:
        soft_deleted = await _detect_soft_deletes([r.get("ID JAMAAH") for r in rows])

    _set_state("last_sync_ts", run_ts)
    _set_state("last_run_inserted", str(inserted))
    _set_state("last_run_updated", str(updated))
    _set_state("last_run_soft_deleted", str(soft_deleted))
    _set_state("last_run_errors", str(errors))

    return {
        "inserted": inserted,
        "updated": updated,
        "soft_deleted": soft_deleted,
        "errors": errors,
        "run_ts": run_ts,
        "rows_processed": len(rows),
    }


async def _detect_soft_deletes(supabase_ext_ids: list[str]) -> int:
    """Row di UMAR yg tidak ada di Supabase -> tag supabase_missing_since."""
    supabase_set = {x for x in supabase_ext_ids if x}
    if not supabase_set:
        return 0

    umar_rows = db.query_all(
        "SELECT id, external_id, name FROM jamaah "
        "WHERE external_id IS NOT NULL AND supabase_missing_since IS NULL"
    )
    now = datetime.datetime.utcnow().isoformat(sep=" ", timespec="seconds")
    count = 0
    for r in umar_rows:
        if r["external_id"] not in supabase_set:
            db.execute(
                "UPDATE jamaah SET supabase_missing_since = ? WHERE id = ?",
                (now, r["id"]),
            )
            _log_sync("soft_delete", r["external_id"],
                      old={"name": r["name"]}, new={"supabase_missing_since": now})
            count += 1
    return count


async def restore_to_supabase(external_id: str) -> dict:
    """Push balik row UMAR ke Supabase closings (recovery dari soft-delete).

    Return Supabase row yg berhasil di-insert.
    """
    row = db.query_one(
        "SELECT * FROM jamaah WHERE external_id = ? AND supabase_missing_since IS NOT NULL",
        (external_id,),
    )
    if not row:
        raise ValueError(
            f"jamaah dgn external_id={external_id} tidak ada atau tidak soft-deleted"
        )

    supabase_row = {
        "ID JAMAAH": row["external_id"],
        "NAMA JAMAAH": row["name"],
        "PAKET": row["package_type"],
        "ADMIN MARKETING": row["admin_marketing"],
        "CHANNEL": row["channel"],
        "SUB CHANNEL": row["sub_channel"],
        "HARGA": row["total_price"],
        "TOTAL BAYAR": row["paid_amount"],
        "DP": row["dp_amount"],
        "STATPAY": {v: k for k, v in _STATPAY_MAP.items()}.get(
            row["payment_status"], "BELUM LUNAS"),
        "NAMA_AGEN": row["agent_name_raw"] or "",
        "ID_AGEN": row["agent_ext_id"],
    }
    result = await supabase_client.insert_row("closings", supabase_row)
    db.execute(
        "UPDATE jamaah SET supabase_missing_since = NULL WHERE external_id = ?",
        (external_id,),
    )
    _log_sync("restore", external_id, new=supabase_row)
    return result
