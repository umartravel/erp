"""One-shot script: redact PII dari CSV data/marketing/ sebelum commit ke git.

Aturan:
- Field kategori/agregat DIPERTAHANKAN (butuh untuk chart & heatmap).
- Field identifikasi personal (nama, NIK, HP, email, alamat detail, paspor,
  tempat/tanggal lahir) DIKOSONGKAN atau diganti placeholder generik.
- Nama agen di-generalize ke "AGEN-NNN" agar leaderboard tetap terbaca tanpa
  membocorkan siapa; ID agen (ATRV-*) tetap sebagai identifier internal.
"""
import csv
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent
DIR = REPO / "data" / "marketing"

# -------- CLOSINGS --------
CLOSINGS_PATH = DIR / "closings.csv"
CLOSINGS_REDACT_EMPTY = {
    "NAMA PEMESAN", "NAMA JAMAAH", "NAMA AYAH",
    "TEMPAT LAHIR", "TANGGAL LAHIR",
    "NO. TELP JAMAAH", "NO. TELP KELUARGA", "EMAIL JAMAAH",
    "NOMOR IDENTITAS",
    "ALAMAT",
    "NO PASPORT", "ISSUED", "EXPIRY",
    "KELURAHAN",
    "NAMA_AGEN",
}
# Nama jamaah + pemesan diganti placeholder biar tetap ada "record identity" tanpa PII.
CLOSINGS_REDACT_PLACEHOLDER = {
    "NAMA JAMAAH": lambda i: f"JAMAAH-{i:03d}",
    "NAMA PEMESAN": lambda i: f"PEMESAN-{i:03d}",
    "NAMA_AGEN": lambda i: "",  # ganti di pass agen mapping di bawah
}

# -------- AGENTS --------
AGENTS_PATH = DIR / "agents.csv"
AGENTS_REDACT_EMPTY = {
    "DETAIL ALAMAT", "NOMOR WA", "AKUN IG", "EMAIL", "KELURAHAN",
}
# Nama agen diganti AGEN-001..NNN dan mapping-nya (by ID_AGEN, bukan nama karena
# closings pakai nama singkat sedangkan agents pakai nama lengkap) dipakai lagi di closings.
AGENTS_ID_MAP = {}   # ATRV-001IV25 -> "AGEN-001"


def _read(path):
    with open(path, "r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter=";"))


def _write(path, rows, fieldnames):
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, delimiter=";")
        w.writeheader()
        w.writerows(rows)


def anon_agents():
    rows = _read(AGENTS_PATH)
    for i, r in enumerate(rows, 1):
        agent_id = (r.get("ID") or "").strip().upper()
        placeholder = f"AGEN-{i:03d}"
        if agent_id:
            AGENTS_ID_MAP[agent_id] = placeholder
        r["NAMA AGEN"] = placeholder
        for k in AGENTS_REDACT_EMPTY:
            if k in r:
                r[k] = ""
    fieldnames = list(rows[0].keys()) if rows else []
    _write(AGENTS_PATH, rows, fieldnames)
    print(f"[anon] agents.csv: {len(rows)} row redacted -> {AGENTS_PATH}")


def anon_closings():
    rows = _read(CLOSINGS_PATH)
    for i, r in enumerate(rows, 1):
        # Empty-out PII fields
        for k in CLOSINGS_REDACT_EMPTY:
            if k in r:
                r[k] = ""
        # Placeholder pengganti nama supaya baris tetap "punya identitas"
        r["NAMA JAMAAH"] = f"JAMAAH-{i:03d}"
        if r.get("NAMA PEMESAN"):
            r["NAMA PEMESAN"] = f"PEMESAN-{i:03d}"
        # Remap nama_agen ke placeholder berbasis ID_AGEN (bukan nama, karena nama di
        # closings ("FITRI") tidak selalu match nama di agents.csv ("FAHMI R. MA'ARIF")).
        id_agen = (r.get("ID_AGEN") or "").strip().upper()
        if id_agen and id_agen in AGENTS_ID_MAP:
            r["NAMA_AGEN"] = AGENTS_ID_MAP[id_agen]
        else:
            # Agen yang tidak ada di direktori resmi: fallback placeholder generik
            r["NAMA_AGEN"] = "AGEN-EXT" if id_agen else ""
    fieldnames = list(rows[0].keys()) if rows else []
    _write(CLOSINGS_PATH, rows, fieldnames)
    print(f"[anon] closings.csv: {len(rows)} row redacted -> {CLOSINGS_PATH}")


if __name__ == "__main__":
    anon_agents()   # harus duluan supaya map-nya siap untuk closings
    anon_closings()
    print("Selesai. Cek file dan re-import via /api/marketing/import (role admin).")
