"""
Sprint AK-3: Month-End Closing engine (akrual amortisasi PSAK 72/115).

Sepanjang bulan, DP jamaah masuk sbg Kredit 2101 (Pendapatan Diterima Dimuka)
-- BUKAN Revenue -- karena obligasi belum terpenuhi. Baru saat jamaah
berangkat, obligasi selesai, dan Rp masuk itu boleh dipindah ke Revenue.
Analog utk procurement: pembayaran vendor ke paket future masuk 1108 Prepaid;
saat paket berangkat, dipindah ke 5xxx COGS.

close_month(year, month, closed_by):
  1. Precheck: tolak double-close (UNIQUE constraint di month_end_closings)
  2. Amortisasi Unearned Revenue -> Revenue:
       - Query jamaah dgn packages.departure_date di YYYY-MM AND paid_amount>0
       - Utk tiap jamaah, generate 1 tx 'closing_amortize' + journal:
           Dr 2101  paid_amount    (kurangi Unearned)
           Cr 4101  paid_amount    (akui Revenue Umrah Reguler)
  3. Realisasi Prepaid Expense -> COGS:
       - Query procurement dgn packages.departure_date di YYYY-MM AND deposit_paid>0
       - Map service_type -> COGS account (5101 Tiket / 5102 Hotel / 5103 Visa /
         5104 Bus / 5105 Konsumsi / 5106 Handling / 5107 Koper / 5108 Muthawif)
       - Generate 1 tx 'closing_cogs' + journal:
           Dr 5xxx  deposit_paid
           Cr 1108  deposit_paid
  4. Insert month_end_closings audit record dgn total dari langkah 2+3.

preview_month(year, month): dry-run tanpa write.
"""
import calendar

import db
import journal_engine as je


_SERVICE_TO_COGS: list[tuple[str, str]] = [
    ("tiket", "5101"),
    ("pesawat", "5101"),
    ("airline", "5101"),
    ("penerbangan", "5101"),
    ("hotel", "5102"),
    ("akomodasi", "5102"),
    ("visa", "5103"),
    ("asuransi", "5103"),
    ("bus", "5104"),
    ("transportasi", "5104"),
    ("ziarah", "5104"),
    ("katering", "5105"),
    ("konsumsi", "5105"),
    ("makan", "5105"),
    ("handling", "5106"),
    ("lounge", "5106"),
    ("koper", "5107"),
    ("seragam", "5107"),
    ("perlengkapan", "5107"),
    ("muthawif", "5108"),
    ("mutawwif", "5108"),
    ("pembimbing", "5108"),
    ("tour leader", "5108"),
]

_COGS_DEFAULT = "5101"


def _map_service_to_cogs(service_type: str | None) -> str:
    s = (service_type or "").lower()
    for keyword, code in _SERVICE_TO_COGS:
        if keyword in s:
            return code
    return _COGS_DEFAULT


def _month_bounds(year: int, month: int) -> tuple[str, str]:
    last_day = calendar.monthrange(year, month)[1]
    return (
        f"{year:04d}-{month:02d}-01",
        f"{year:04d}-{month:02d}-{last_day:02d}",
    )


def preview_month(year: int, month: int) -> dict:
    if not (1 <= month <= 12):
        raise ValueError(f"month harus 1-12, got {month}")
    start, end = _month_bounds(year, month)

    already_closed = db.query_one(
        "SELECT id, closed_at, closed_by FROM month_end_closings "
        "WHERE year = ? AND month = ?", (year, month),
    )

    jamaah_rows = db.query_all(
        "SELECT j.id, j.name, j.paid_amount, j.package_type, p.departure_date "
        "FROM jamaah j "
        "JOIN packages p ON p.name = j.package_type "
        "WHERE p.departure_date BETWEEN ? AND ? "
        "AND (j.paid_amount IS NOT NULL AND j.paid_amount > 0) "
        "AND (j.pipeline_stage IS NULL OR j.pipeline_stage != 'Cancelled')",
        (start, end),
    )
    revenue_total = sum(int(j["paid_amount"] or 0) for j in jamaah_rows)

    proc_rows = db.query_all(
        "SELECT p.id, p.vendor_name, p.service_type, p.deposit_paid, p.package_name, "
        "  pk.departure_date "
        "FROM procurement p "
        "LEFT JOIN packages pk ON pk.name = p.package_name "
        "WHERE pk.departure_date BETWEEN ? AND ? "
        "AND (p.deposit_paid IS NOT NULL AND p.deposit_paid > 0) "
        "AND p.status = 'Aktif'",
        (start, end),
    )
    cogs_total = sum(int(p["deposit_paid"] or 0) for p in proc_rows)

    cogs_by_account: dict[str, int] = {}
    for p in proc_rows:
        code = _map_service_to_cogs(p["service_type"])
        cogs_by_account[code] = cogs_by_account.get(code, 0) + int(p["deposit_paid"] or 0)

    return {
        "year": year,
        "month": month,
        "period_label": f"{start} -- {end}",
        "already_closed": already_closed is not None,
        "already_closed_at": already_closed["closed_at"] if already_closed else None,
        "already_closed_by": already_closed["closed_by"] if already_closed else None,
        "jamaah_count": len(jamaah_rows),
        "revenue_to_realize": revenue_total,
        "procurement_count": len(proc_rows),
        "cogs_to_recognize": cogs_total,
        "cogs_by_account": cogs_by_account,
        "sample_jamaah": [
            {"id": j["id"], "name": j["name"], "amount": int(j["paid_amount"] or 0)}
            for j in jamaah_rows[:5]
        ],
        "sample_procurement": [
            {"id": p["id"], "vendor": p["vendor_name"],
             "service": p["service_type"], "amount": int(p["deposit_paid"] or 0)}
            for p in proc_rows[:5]
        ],
    }


def close_month(year: int, month: int, closed_by: str | None = None,
                notes: str | None = None) -> dict:
    if not (1 <= month <= 12):
        raise ValueError(f"month harus 1-12, got {month}")

    existing = db.query_one(
        "SELECT id FROM month_end_closings WHERE year = ? AND month = ?",
        (year, month),
    )
    if existing:
        raise ValueError(
            f"Buku bulan {year}-{month:02d} sudah ditutup (id={existing['id']}). "
            f"Kalau perlu re-run, DELETE record itu dulu."
        )

    start, end = _month_bounds(year, month)
    period_label = f"{year}-{month:02d}"

    entries_count = 0
    revenue_realized = 0
    cogs_recognized = 0

    jamaah_rows = db.query_all(
        "SELECT j.id, j.name, j.paid_amount, j.package_type, p.departure_date "
        "FROM jamaah j "
        "JOIN packages p ON p.name = j.package_type "
        "WHERE p.departure_date BETWEEN ? AND ? "
        "AND (j.paid_amount IS NOT NULL AND j.paid_amount > 0) "
        "AND (j.pipeline_stage IS NULL OR j.pipeline_stage != 'Cancelled')",
        (start, end),
    )

    for j in jamaah_rows:
        amount = int(j["paid_amount"] or 0)
        if amount <= 0:
            continue
        desc = f"Realisasi Umrah {period_label}: {j['name']}"
        tx_id, _ = db.execute(
            "INSERT INTO transactions (type, category, amount, description, "
            "reference_id, package_name, status) "
            "VALUES ('closing', 'revenue_realized', ?, ?, ?, ?, 'POSTED')",
            (amount, desc, j["id"], j["package_type"]),
        )
        je.post_journal(tx_id, [
            (je.coa_id(je.COA_UNEARNED_REVENUE), amount, 0, desc),
            (je.coa_id(je.COA_REVENUE_UMRAH), 0, amount, desc),
        ])
        entries_count += 1
        revenue_realized += amount

    proc_rows = db.query_all(
        "SELECT p.id, p.vendor_name, p.service_type, p.deposit_paid, p.package_name, "
        "  pk.departure_date "
        "FROM procurement p "
        "LEFT JOIN packages pk ON pk.name = p.package_name "
        "WHERE pk.departure_date BETWEEN ? AND ? "
        "AND (p.deposit_paid IS NOT NULL AND p.deposit_paid > 0) "
        "AND p.status = 'Aktif'",
        (start, end),
    )

    for p in proc_rows:
        amount = int(p["deposit_paid"] or 0)
        if amount <= 0:
            continue
        cogs_code = _map_service_to_cogs(p["service_type"])
        desc = (f"Realisasi COGS {period_label}: "
                f"{p['service_type']} - {p['vendor_name']}")
        tx_id, _ = db.execute(
            "INSERT INTO transactions (type, category, amount, description, "
            "reference_id, package_name, status) "
            "VALUES ('closing', 'cogs_recognized', ?, ?, ?, ?, 'POSTED')",
            (amount, desc, p["id"], p["package_name"]),
        )
        je.post_journal(tx_id, [
            (je.coa_id(cogs_code), amount, 0, desc),
            (je.coa_id(je.COA_PREPAID_UMRAH), 0, amount, desc),
        ])
        entries_count += 1
        cogs_recognized += amount

    closing_id, _ = db.execute(
        "INSERT INTO month_end_closings "
        "(year, month, closed_by, entries_count, revenue_realized, cogs_recognized, notes) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (year, month, closed_by, entries_count, revenue_realized, cogs_recognized, notes),
    )

    return {
        "closing_id": closing_id,
        "year": year,
        "month": month,
        "period_label": period_label,
        "entries_count": entries_count,
        "revenue_realized": revenue_realized,
        "cogs_recognized": cogs_recognized,
        "jamaah_amortized": len(jamaah_rows),
        "procurement_recognized": len(proc_rows),
    }
