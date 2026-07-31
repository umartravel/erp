"""
Generator PDF dokumen operasional per paket keberangkatan (Manifest, Roomlist, Absensi)
memakai reportlab. Mengikuti pola layout & helper yang sama dengan expense_pdf.py.
Dipanggil oleh app.py pada endpoint GET /api/packages/{id}/manifest-pdf, /roomlist-pdf,
/absensi-pdf.
"""
import datetime
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

NAVY = colors.HexColor("#1e3a5f")
GRAY = colors.HexColor("#666666")
LIGHT_BG = colors.HexColor("#f2f2f2")
BORDER = colors.HexColor("#999999")

ABSENSI_CHECKPOINTS = ["Keberangkatan", "Tiba Madinah", "Tiba Mekkah", "Kepulangan"]
ABSENSI_EXTRA_BLANK_COLS = 3  # kolom kosong tambahan untuk TL tulis tangan checkpoint lain

ROOM_CAPACITY = {"QUAD": 4, "TRIPLE": 3, "DOUBLE": 2, "QUINT": 5}


def _fmt_date(s, fmt="%d %b %Y"):
    if not s:
        return "-"
    try:
        return datetime.datetime.strptime(str(s)[:10], "%Y-%m-%d").strftime(fmt)
    except ValueError:
        return str(s)[:10]


def _calc_age(birth_date, as_of=None):
    if not birth_date:
        return "-"
    try:
        bd = datetime.datetime.strptime(str(birth_date)[:10], "%Y-%m-%d").date()
    except ValueError:
        return "-"
    ref = as_of or datetime.date.today()
    return ref.year - bd.year - ((ref.month, ref.day) < (bd.month, bd.day))


def room_type_key(room_type):
    """Ambil kata kunci tipe kamar (QUAD/TRIPLE/DOUBLE/QUINT) dari nilai bebas di DB
    (mis. 'QUAD ROOM' atau 'QUAD' bisa sama-sama muncul -- data lama tidak konsisten)."""
    if not room_type:
        return None
    upper = room_type.upper()
    for key in ROOM_CAPACITY:
        if upper.startswith(key):
            return key
    return None


def auto_group_rooms(jamaah_rows):
    """Kelompokkan otomatis (kasar) per gender + tipe kamar, diberi label urut.
    Baris dengan tipe kamar tak dikenali (mis. 'LAINNYA..') digabung jadi 1 grup tanpa dipecah,
    karena kapasitasnya tidak diketahui sistem. Hasil: dict {jamaah_id: label_kelompok}."""
    buckets = {}
    unknown = []
    for j in jamaah_rows:
        key = room_type_key(j["room_type"])
        if key:
            buckets.setdefault((key, j["gender"] or "-"), []).append(j)
        else:
            unknown.append(j)

    labels = {}
    gender_label = {"Laki-Laki": "LAKI-LAKI", "Perempuan": "PEREMPUAN"}
    for (key, gender), members in buckets.items():
        capacity = ROOM_CAPACITY[key]
        glabel = gender_label.get(gender, gender)
        for idx, chunk_start in enumerate(range(0, len(members), capacity), start=1):
            chunk = members[chunk_start:chunk_start + capacity]
            label = f"{key} {idx} ({glabel})"
            for j in chunk:
                labels[j["id"]] = label
    if unknown:
        for j in unknown:
            labels[j["id"]] = "LAINNYA (Belum Dikelompokkan)"
    return labels


def _base_doc(landscape_mode=False):
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=landscape(A4) if landscape_mode else A4,
        leftMargin=14 * mm, rightMargin=14 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
    )
    return buf, doc


def _header(package, title, company, logo_path):
    styles = getSampleStyleSheet()
    p_small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8.5, leading=11)
    p_bold = ParagraphStyle("Bold", parent=p_small, fontName="Helvetica-Bold")
    p_title = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=16, textColor=NAVY, alignment=2, spaceAfter=0)
    p_title_meta = ParagraphStyle("TitleMeta", parent=p_small, alignment=2, textColor=colors.black)

    if logo_path:
        try:
            logo = Image(logo_path, width=26 * mm, height=13 * mm, kind="proportional")
        except Exception:  # noqa: BLE001
            logo = Paragraph(company.get("legal_name", "Umar Travel"), p_bold)
    else:
        logo = Paragraph(company.get("legal_name", "Umar Travel"), p_bold)

    header_right = [
        Paragraph(title, p_title),
        Paragraph(f"Paket : {package['name']}", p_title_meta),
        Paragraph(f"Tgl. Berangkat : {_fmt_date(package.get('departure_date'))}", p_title_meta),
    ]
    header_table = Table([[logo, header_right]], colWidths=[90 * mm, None])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    return [header_table, Spacer(1, 5 * mm)], p_small, p_bold


def _footer(canvas, doc_):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(GRAY)
    canvas.drawString(14 * mm, 9 * mm, f"Halaman {doc_.page}")
    canvas.drawRightString(doc_.pagesize[0] - 14 * mm, 9 * mm, "Dicetak oleh Umar CRM")
    canvas.restoreState()


def build_manifest_pdf(package, jamaah_list, company=None, logo_path=None) -> bytes:
    company = company or {}
    buf, doc = _base_doc(landscape_mode=True)
    elements, p_small, p_bold = _header(package, "Manifest Jamaah", company, logo_path)

    header_row = ["No", "Nama Lengkap", "Gender", "Hubungan", "Tempat Lahir", "Tgl Lahir", "Usia",
                  "No. Paspor", "Tgl Terbit", "Tgl Habis", "Kantor Penerbit"]
    table_data = [header_row]
    dep_date = None
    if package.get("departure_date"):
        try:
            dep_date = datetime.datetime.strptime(str(package["departure_date"])[:10], "%Y-%m-%d").date()
        except ValueError:
            dep_date = None
    for i, j in enumerate(jamaah_list, start=1):
        table_data.append([
            str(i), j["name"], j["gender"] or "-", j["relation"] or "-",
            j["birth_place"] or "-", _fmt_date(j["birth_date"]), str(_calc_age(j["birth_date"], dep_date)),
            j["passport_number"] or "-", _fmt_date(j["passport_issued"]), _fmt_date(j["passport_expiry"]),
            j["passport_issuer_city"] or "-",
        ])
    if len(table_data) == 1:
        table_data.append(["-", "Belum ada jamaah pada paket ini"] + ["-"] * 9)

    tbl = Table(table_data, colWidths=[9 * mm, 42 * mm, 16 * mm, 20 * mm, 26 * mm, 20 * mm, 12 * mm,
                                        24 * mm, 20 * mm, 20 * mm, 30 * mm], repeatRows=1)
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7.5),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (6, 0), (6, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(tbl)
    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def build_roomlist_pdf(package, jamaah_list, company=None, logo_path=None) -> bytes:
    """jamaah_list harus sudah punya `room_number` terisi (label kelompok kamar final)."""
    company = company or {}
    buf, doc = _base_doc(landscape_mode=False)
    elements, p_small, p_bold = _header(package, "Roomlist", company, logo_path)

    ordered = sorted(jamaah_list, key=lambda j: (j["room_number"] or "ZZZ", j["name"]))
    header_row = ["No", "Nama Jamaah", "L/P", "Hubungan", "Kamar"]
    table_data = [header_row]
    span_ranges = []
    row_idx = 1  # baris 0 = header
    i = 0
    n = 1
    while i < len(ordered):
        label = ordered[i]["room_number"] or "-"
        group = [ordered[i]]
        j = i + 1
        while j < len(ordered) and (ordered[j]["room_number"] or "-") == label:
            group.append(ordered[j])
            j += 1
        start_row = row_idx
        for m in group:
            gp = "L" if (m["gender"] or "").lower().startswith("l") else ("P" if m["gender"] else "-")
            table_data.append([str(n), m["name"], gp, m["relation"] or "-", label])
            n += 1
            row_idx += 1
        if len(group) > 1:
            span_ranges.append((start_row, row_idx - 1))
        i = j
    if len(table_data) == 1:
        table_data.append(["-", "Belum ada pengelompokan kamar untuk paket ini", "-", "-", "-"])

    tbl = Table(table_data, colWidths=[10 * mm, 65 * mm, 12 * mm, 30 * mm, 45 * mm], repeatRows=1)
    style = [
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (2, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for start, end in span_ranges:
        style.append(("SPAN", (4, start), (4, end)))
    tbl.setStyle(TableStyle(style))
    elements.append(tbl)

    staff_lines = []
    if package.get("tour_leader"):
        staff_lines.append(f"Tour Leader: {package['tour_leader']}")
    if package.get("mutawwif"):
        staff_lines.append(f"Muthawif: {package['mutawwif']}")
    if staff_lines:
        elements.append(Spacer(1, 5 * mm))
        elements.append(Paragraph("Staf Pendamping (belum termasuk pengelompokan kamar di atas):", p_bold))
        for line in staff_lines:
            elements.append(Paragraph(line, p_small))

    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()


def build_absensi_pdf(package, jamaah_list, company=None, logo_path=None) -> bytes:
    company = company or {}
    buf, doc = _base_doc(landscape_mode=True)
    elements, p_small, p_bold = _header(package, "Absensi Jamaah & Ceklist", company, logo_path)
    elements.append(Paragraph("Tour Leader: _______________________", p_small))
    elements.append(Spacer(1, 3 * mm))

    checkpoints = ABSENSI_CHECKPOINTS + [""] * ABSENSI_EXTRA_BLANK_COLS
    header_row = ["No", "Nama Jamaah"] + checkpoints
    table_data = [header_row]
    for i, j in enumerate(jamaah_list, start=1):
        table_data.append([str(i), j["name"]] + [""] * len(checkpoints))
    if len(table_data) == 1:
        table_data.append(["-", "Belum ada jamaah pada paket ini"] + [""] * len(checkpoints))

    col_widths = [10 * mm, 55 * mm] + [22 * mm] * len(checkpoints)
    tbl = Table(table_data, colWidths=col_widths, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("BACKGROUND", (0, 0), (-1, 0), LIGHT_BG),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(tbl)
    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
