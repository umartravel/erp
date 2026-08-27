"""
Parser CSV mutasi bank untuk rekonsiliasi. Support 3 pola:
- BCA: kolom "Tanggal", "Keterangan", "Cabang", "Jumlah", "DB/CR", "Saldo"
- Mandiri: kolom "Tanggal Transaksi"/"Tanggal", "Uraian"/"Keterangan", "Debet", "Kredit", "Saldo"
- Generic: deteksi otomatis via header (case-insensitive) -- fallback untuk BRI/BNI/dsb.

Output baris: dict dengan
    mutation_date : "YYYY-MM-DD"
    description   : str
    amount        : int rupiah (selalu positif)
    direction     : "credit" (uang masuk) atau "debit" (uang keluar)
    reference     : str | None
    balance       : int | None
"""
import csv
import hashlib
import io
import re
from datetime import datetime


# ---------------------------------------------------------------------------
# Helper parsing
# ---------------------------------------------------------------------------
_DATE_FORMATS = [
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y",
    "%Y-%m-%d", "%Y/%m/%d",
    "%d/%m/%y", "%d-%m-%y",
]


def _parse_date(raw):
    """Coba beberapa format tanggal Indonesia. Return YYYY-MM-DD atau None."""
    if not raw:
        return None
    raw = str(raw).strip()
    if not raw:
        return None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def _parse_amount(raw):
    """'1.500.000,00' / '1500000' / '1,500,000.00' / '-500.000' -> int (absolut)."""
    if raw is None:
        return 0
    s = str(raw).strip()
    if not s or s in ("-", "0", "0.00", "0,00"):
        return 0
    s = re.sub(r"[^\d.,\-]", "", s)
    if not s or s == "-":
        return 0
    # Normalisasi ID vs EN:
    #   ID: "1.500.000,50"  -> "1500000.50"
    #   EN: "1,500,000.50"  -> "1500000.50"
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) <= 2:
            s = parts[0].replace(".", "") + "." + parts[1]
        else:
            s = s.replace(",", "")
    else:
        parts = s.split(".")
        if len(parts) > 2 or (len(parts) == 2 and len(parts[1]) == 3):
            s = s.replace(".", "")
    try:
        return abs(int(round(float(s))))
    except ValueError:
        return 0


def _norm_header(h):
    return re.sub(r"\s+", " ", (h or "").strip().lower())


# ---------------------------------------------------------------------------
# Deteksi bank via header
# ---------------------------------------------------------------------------
def _detect_bank(headers):
    hs = [_norm_header(h) for h in headers]
    joined = " | ".join(hs)
    if "db/cr" in joined or "db / cr" in joined:
        return "BCA"
    if "debet" in joined and "kredit" in joined:
        return "Mandiri"
    return "Generic"


# ---------------------------------------------------------------------------
# Column mapper -- kembalikan index kolom untuk field tertentu
# ---------------------------------------------------------------------------
_FIELD_KEYWORDS = {
    "date": ["tanggal transaksi", "tanggal", "trans date", "date", "tgl"],
    "description": ["keterangan", "uraian", "description", "narasi", "berita"],
    "amount": ["jumlah", "nominal", "amount"],
    "debit": ["debet", "debit", "keluar", "out", "pengeluaran"],
    "credit": ["kredit", "credit", "masuk", "in", "pemasukan"],
    "dbcr_flag": ["db/cr", "db / cr", "type", "tipe"],
    "reference": ["ref", "reference", "no. ref", "no ref"],
    "balance": ["saldo", "balance"],
}


def _find_col(headers, keys):
    """Cari index kolom pertama yang cocok dengan salah satu keyword (exact-match dulu, lalu contains)."""
    hs = [_norm_header(h) for h in headers]
    for k in keys:
        for i, h in enumerate(hs):
            if h == k:
                return i
    for k in keys:
        for i, h in enumerate(hs):
            if k in h:
                return i
    return None


# ---------------------------------------------------------------------------
# Parse utama
# ---------------------------------------------------------------------------
def parse_csv(text, hint_bank=None):
    """
    Return: {"bank": str, "rows": [dict], "warnings": [str]}
    Baris dilewati (dengan warning) kalau tanggal atau amount tidak bisa diparse.
    """
    warnings = []
    text = (text or "").lstrip("﻿")  # buang BOM UTF-8

    sample = text[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    reader = csv.reader(io.StringIO(text), dialect)

    all_rows = [r for r in reader if any((c or "").strip() for c in r)]
    if not all_rows:
        return {"bank": "Unknown", "rows": [], "warnings": ["CSV kosong."]}

    # Cari baris header: baris pertama yang mengandung "tanggal" / "date" / "trans date"
    header_idx = 0
    for i, r in enumerate(all_rows[:15]):
        low = " | ".join(_norm_header(c) for c in r)
        if any(k in low for k in ("tanggal", "date", "trans date", "tgl")):
            header_idx = i
            break

    headers = all_rows[header_idx]
    body = all_rows[header_idx + 1:]

    bank = hint_bank or _detect_bank(headers)

    cix = {
        "date": _find_col(headers, _FIELD_KEYWORDS["date"]),
        "desc": _find_col(headers, _FIELD_KEYWORDS["description"]),
        "amount": _find_col(headers, _FIELD_KEYWORDS["amount"]),
        "debit": _find_col(headers, _FIELD_KEYWORDS["debit"]),
        "credit": _find_col(headers, _FIELD_KEYWORDS["credit"]),
        "dbcr": _find_col(headers, _FIELD_KEYWORDS["dbcr_flag"]),
        "ref": _find_col(headers, _FIELD_KEYWORDS["reference"]),
        "balance": _find_col(headers, _FIELD_KEYWORDS["balance"]),
    }

    if cix["date"] is None:
        return {"bank": bank, "rows": [], "warnings": ["Kolom tanggal tidak ditemukan. Pastikan CSV punya header 'Tanggal' atau 'Date'."]}
    if cix["amount"] is None and cix["debit"] is None and cix["credit"] is None:
        return {"bank": bank, "rows": [], "warnings": ["Kolom jumlah tidak ditemukan. Butuh salah satu dari: 'Jumlah', 'Debet+Kredit', atau 'Amount'."]}

    def _get(row, idx):
        if idx is None or idx >= len(row):
            return ""
        return row[idx]

    parsed = []
    for lineno, row in enumerate(body, start=header_idx + 2):
        if not any((c or "").strip() for c in row):
            continue
        d = _parse_date(_get(row, cix["date"]))
        if not d:
            warnings.append(f"Baris {lineno}: tanggal tidak dikenal '{_get(row, cix['date'])}', dilewati.")
            continue

        amount = 0
        direction = None
        if cix["debit"] is not None or cix["credit"] is not None:
            deb = _parse_amount(_get(row, cix["debit"]))
            cred = _parse_amount(_get(row, cix["credit"]))
            if cred > 0:
                amount, direction = cred, "credit"
            elif deb > 0:
                amount, direction = deb, "debit"
        elif cix["amount"] is not None:
            amount = _parse_amount(_get(row, cix["amount"]))
            flag = _norm_header(_get(row, cix["dbcr"])) if cix["dbcr"] is not None else ""
            if flag in ("cr", "credit", "kredit", "masuk", "c"):
                direction = "credit"
            elif flag in ("db", "debit", "debet", "keluar", "d"):
                direction = "debit"
            else:
                raw = str(_get(row, cix["amount"])).strip()
                direction = "debit" if raw.startswith("-") else "credit"

        if amount <= 0 or direction is None:
            warnings.append(f"Baris {lineno}: amount 0 / arah tidak jelas, dilewati.")
            continue

        parsed.append({
            "mutation_date": d,
            "description": (str(_get(row, cix["desc"])).strip() if cix["desc"] is not None else ""),
            "amount": amount,
            "direction": direction,
            "reference": (str(_get(row, cix["ref"])).strip() if cix["ref"] is not None else "") or None,
            "balance": _parse_amount(_get(row, cix["balance"])) if cix["balance"] is not None else None,
        })

    return {"bank": bank, "rows": parsed, "warnings": warnings}


def row_hash(bank, date, amount, direction, description):
    """Hash stabil untuk cegah duplicate import (baris CSV yang sama)."""
    key = f"{(bank or '').strip()}|{date}|{amount}|{direction}|{(description or '').strip()[:200]}"
    return hashlib.sha1(key.encode("utf-8")).hexdigest()
