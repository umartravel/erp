"""
Importer CSV untuk halaman Marketing Analytics.

Sumber: data/marketing/*.csv -- disalin apa adanya dari repo web-umar (public GitHub).
Skema tujuan: marketing_closings + marketing_agents (lihat db.py).

Konvensi normalisasi:
- Semua string di-strip; kosong dinormalkan ke None.
- Field mata uang (Rp24.600.000, 24,600,000, dsb) -> integer rupiah.
- Provinsi + kab/kota di-uppercase agar konsisten dengan kamus koordinat (js/marketing.js).
- ID JAMAAH & ID AGEN dipakai apa adanya sebagai primary key -- upsert kalau baris dengan
  id yang sama sudah ada.
"""
import csv
import os
import re

import db

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_DIR = os.path.join(BASE_DIR, "data", "marketing")
CLOSINGS_CSV = os.path.join(CSV_DIR, "closings.csv")
AGENTS_CSV = os.path.join(CSV_DIR, "agents.csv")


def _clean_str(val):
    if val is None:
        return None
    s = str(val).strip()
    return s if s else None


def _clean_upper(val):
    s = _clean_str(val)
    return s.upper() if s else None


def _clean_money(val):
    """'Rp24.600.000' / '24,600,000' / '' / None -> int"""
    if val is None:
        return 0
    s = str(val).strip()
    if not s:
        return 0
    # Hilangkan simbol Rp, spasi, titik/koma pemisah ribuan
    s = re.sub(r"[^\d-]", "", s)
    if not s or s == "-":
        return 0
    try:
        return int(s)
    except ValueError:
        return 0


def _clean_int(val):
    if val is None or val == "":
        return None
    s = str(val).strip()
    if not s:
        return None
    # Kadang muncul notasi ilmiah dari Excel export (3,17308E+15).
    if "E+" in s.upper() or "E-" in s.upper():
        try:
            return int(float(s.replace(",", ".")))
        except (ValueError, OverflowError):
            return None
    s = re.sub(r"[^\d-]", "", s)
    if not s or s == "-":
        return None
    try:
        return int(s)
    except ValueError:
        return None


def import_closings(csv_path=CLOSINGS_CSV):
    """Impor CSV transaksi jamaah historis. Idempoten via UPSERT on id_jamaah.
    Return: (inserted_or_updated_count, skipped_count).
    """
    if not os.path.exists(csv_path):
        print(f"[marketing_importer] File tidak ditemukan: {csv_path}")
        return (0, 0)

    upserted = 0
    skipped = 0
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            id_jamaah = _clean_str(row.get("ID JAMAAH"))
            nama_jamaah = _clean_str(row.get("NAMA JAMAAH"))
            if not id_jamaah or not nama_jamaah:
                skipped += 1
                continue

            data = {
                "id_jamaah": id_jamaah,
                "nama_pemesan": _clean_str(row.get("NAMA PEMESAN")),
                "tanggal_order": _clean_str(row.get("TANGGAL ORDER")),
                "paket": _clean_str(row.get("PAKET")),
                "admin_marketing": _clean_upper(row.get("ADMIN MARKETING")),
                "channel": _clean_upper(row.get("CHANNEL")),
                "sub_channel": _clean_upper(row.get("SUB CHANNEL")),
                "nama_jamaah": nama_jamaah,
                "jenis_kelamin": _clean_upper(row.get("JENIS KELAMIN")),
                "tempat_lahir": _clean_str(row.get("TEMPAT LAHIR")),
                "tanggal_lahir": _clean_str(row.get("TANGGAL LAHIR")),
                "usia": _clean_int(row.get("USIA")),
                "no_telp_jamaah": _clean_str(row.get("NO. TELP JAMAAH")),
                "no_telp_keluarga": _clean_str(row.get("NO. TELP KELUARGA")),
                "email_jamaah": _clean_str(row.get("EMAIL JAMAAH")),
                "nama_ayah": _clean_str(row.get("NAMA AYAH")),
                "kewarganegaraan": _clean_upper(row.get("KEWARGANEGARAAN")),
                "jenis_identitas": _clean_upper(row.get("JENIS IDENTITAS")),
                "nomor_identitas": _clean_str(row.get("NOMOR IDENTITAS")),
                "pendidikan": _clean_upper(row.get("PENDIDIKAN")),
                "pekerjaan": _clean_upper(row.get("PEKERJAAN")),
                "status_pernikahan": _clean_upper(row.get("STATUS PERNIKAHAN")),
                "hubungan": _clean_upper(row.get("HUBUNGAN")),
                "alamat": _clean_str(row.get("ALAMAT")),
                "provinsi": _clean_upper(row.get("PROVINSI")),
                "kab_kota": _clean_upper(row.get("KAB/KOTA")),
                "kecamatan": _clean_upper(row.get("KECAMATAN")),
                "kelurahan": _clean_upper(row.get("KELURAHAN")),
                "no_paspor": _clean_str(row.get("NO PASPORT")),
                "issued": _clean_str(row.get("ISSUED")),
                "expiry": _clean_str(row.get("EXPIRY")),
                "kantor_imigrasi": _clean_upper(row.get("KANTOR IMIGRASI")),
                "perlengkapan": _clean_upper(row.get("PERLENGKAPAN")),
                "req_kamar": _clean_upper(row.get("REQ KAMAR")),
                "harga": _clean_money(row.get("HARGA")),
                "total_bayar": _clean_money(row.get("TOTAL BAYAR")),
                "kurang": _clean_money(row.get("KURANG")),
                "statpay": _clean_upper(row.get("STATPAY")) or "LUNAS",
                "dp": _clean_money(row.get("DP")),
                "is_transaksi_agen": _clean_upper(row.get("IS_TRANSAKSI_AGEN")) or "TIDAK",
                "nama_agen": _clean_str(row.get("NAMA_AGEN")),
                "id_agen": _clean_str(row.get("ID_AGEN")),
            }

            cols = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            update_set = ", ".join([f"{k}=excluded.{k}" for k in data if k != "id_jamaah"])
            sql = (
                f"INSERT INTO marketing_closings ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT(id_jamaah) DO UPDATE SET {update_set}"
            )
            db.execute(sql, list(data.values()))
            upserted += 1

    print(f"[marketing_importer] marketing_closings: upsert {upserted} row, skip {skipped}")
    return (upserted, skipped)


def import_agents(csv_path=AGENTS_CSV):
    """Impor CSV direktori agen. Idempoten via UPSERT on id_agen."""
    if not os.path.exists(csv_path):
        print(f"[marketing_importer] File tidak ditemukan: {csv_path}")
        return (0, 0)

    upserted = 0
    skipped = 0
    with open(csv_path, "r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f, delimiter=";")
        for row in reader:
            id_agen = _clean_str(row.get("ID"))
            nama_agen = _clean_str(row.get("NAMA AGEN"))
            if not id_agen or not nama_agen:
                skipped += 1
                continue

            data = {
                "id_agen": id_agen,
                "no": _clean_int(row.get("NO")),
                "tanggal_pendaftaran": _clean_str(row.get("TANGGAL PENDAFTARAN")),
                "date_day": _clean_int(row.get("DATE")),
                "month_name": _clean_upper(row.get("MONTH")),
                "year_val": _clean_int(row.get("YEAR")),
                "nama_agen": nama_agen,
                "provinsi": _clean_upper(row.get("PROVINSI")),
                "kab_kota": _clean_upper(row.get("KAB/KOTA")),
                "kecamatan": _clean_upper(row.get("KECAMATAN")),
                "kelurahan": _clean_upper(row.get("KELURAHAN")),
                "detail_alamat": _clean_str(row.get("DETAIL ALAMAT")),
                "nomor_wa": _clean_str(row.get("NOMOR WA")),
                "akun_ig": _clean_str(row.get("AKUN IG")),
                "email": _clean_str(row.get("EMAIL")),
                "nomor_surat": _clean_str(row.get("NOMOR SURAT")),
            }

            cols = ", ".join(data.keys())
            placeholders = ", ".join(["?"] * len(data))
            update_set = ", ".join([f"{k}=excluded.{k}" for k in data if k != "id_agen"])
            sql = (
                f"INSERT INTO marketing_agents ({cols}) VALUES ({placeholders}) "
                f"ON CONFLICT(id_agen) DO UPDATE SET {update_set}"
            )
            db.execute(sql, list(data.values()))
            upserted += 1

    print(f"[marketing_importer] marketing_agents: upsert {upserted} row, skip {skipped}")
    return (upserted, skipped)


def import_all_if_empty():
    """Panggil ini di init_db(). Auto-import HANYA kalau tabel kosong -- supaya tidak
    menimpa perubahan manual pengguna yang mungkin sudah edit data ini."""
    try:
        c = db.query_one("SELECT COUNT(*) as c FROM marketing_closings")
        if c and c["c"] > 0:
            print(f"[marketing_importer] marketing_closings sudah ada {c['c']} row, skip auto-import.")
        else:
            import_closings()
    except Exception as e:  # noqa: BLE001
        print(f"[marketing_importer] gagal auto-import closings: {e}")

    try:
        c = db.query_one("SELECT COUNT(*) as c FROM marketing_agents")
        if c and c["c"] > 0:
            print(f"[marketing_importer] marketing_agents sudah ada {c['c']} row, skip auto-import.")
        else:
            import_agents()
    except Exception as e:  # noqa: BLE001
        print(f"[marketing_importer] gagal auto-import agents: {e}")


def import_all_force():
    """Untuk endpoint admin: re-import semua (upsert -- data lama akan ditimpa nilai baru)."""
    c1 = import_closings()
    c2 = import_agents()
    return {
        "closings": {"upserted": c1[0], "skipped": c1[1]},
        "agents": {"upserted": c2[0], "skipped": c2[1]},
    }
