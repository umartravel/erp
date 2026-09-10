"""
Phase F4: Generator PDF Analisis Keuangan (yearly + monthly).

Dipanggil oleh routes/finance_categories.py di endpoint:
- GET /api/finance/export/year.pdf?year=YYYY
- GET /api/finance/export/month.pdf?year=YYYY&month=MM

Layout & style consistent dgn mgmt_pdf.py (palet Luxury UMAR).
"""
import io

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT, TA_RIGHT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

BRAND_GOLD = colors.HexColor("#B8860B")
BRAND_CHARCOAL = colors.HexColor("#1D1D1B")
BRAND_CREAM = colors.HexColor("#FFFDF7")
INK = colors.HexColor("#1F1F1D")
INK_SOFT = colors.HexColor("#6B7280")
BORDER = colors.HexColor("#E5DFCE")
ROW_ZEBRA = colors.HexColor("#FBF7EE")
STATUS_RED = colors.HexColor("#B91C1C")
STATUS_GREEN = colors.HexColor("#166534")

USABLE_W = 180 * mm

_MONTH_NAMES = [
    "", "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]


def _fmt_rp(n):
    return f"Rp {int(n or 0):,}".replace(",", ".")


def _fmt_rp_short(n):
    n = int(n or 0)
    if n >= 1_000_000_000:
        return f"Rp {n / 1_000_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000_000:
        return f"Rp {n / 1_000_000:.1f}jt".replace(".0jt", "jt")
    if n >= 1_000:
        return f"Rp {n / 1_000:.0f}rb"
    return f"Rp {n}"


def _styles():
    styles = getSampleStyleSheet()
    return {
        "h1": ParagraphStyle(
            "H1", parent=styles["Heading1"],
            fontName="Helvetica-Bold", fontSize=22, leading=26,
            textColor=BRAND_CHARCOAL, spaceAfter=0,
        ),
        "sub": ParagraphStyle(
            "Sub", parent=styles["Normal"],
            fontName="Helvetica", fontSize=8.5, leading=11,
            textColor=INK_SOFT, spaceAfter=0,
        ),
        "section": ParagraphStyle(
            "Section", parent=styles["Heading2"],
            fontName="Helvetica-Bold", fontSize=11, leading=14,
            textColor=BRAND_GOLD, spaceBefore=10, spaceAfter=4,
        ),
        "body_soft": ParagraphStyle(
            "BodySoft", parent=styles["Normal"],
            fontName="Helvetica", fontSize=9, leading=12, textColor=INK_SOFT,
        ),
        "footer": ParagraphStyle(
            "Footer", parent=styles["Normal"],
            fontName="Helvetica-Oblique", fontSize=7.5, leading=10,
            textColor=INK_SOFT, alignment=TA_CENTER,
        ),
        "kpi_label": ParagraphStyle(
            "KpiLabel", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=7, leading=9,
            textColor=BRAND_GOLD, spaceAfter=2,
        ),
        "kpi_value": ParagraphStyle(
            "KpiValue", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=16, leading=20,
            textColor=BRAND_CHARCOAL, spaceAfter=1,
        ),
        "kpi_hint": ParagraphStyle(
            "KpiHint", parent=styles["Normal"],
            fontName="Helvetica", fontSize=7.5, leading=10, textColor=INK_SOFT,
        ),
        "th": ParagraphStyle(
            "Th", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=8, leading=10,
            textColor=colors.white, alignment=TA_LEFT,
        ),
        "th_r": ParagraphStyle(
            "ThR", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=8, leading=10,
            textColor=colors.white, alignment=TA_RIGHT,
        ),
        "th_c": ParagraphStyle(
            "ThC", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=8, leading=10,
            textColor=colors.white, alignment=TA_CENTER,
        ),
        "td": ParagraphStyle(
            "Td", parent=styles["Normal"],
            fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK,
        ),
        "td_b": ParagraphStyle(
            "TdB", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=8.5, leading=11, textColor=INK,
        ),
        "td_r": ParagraphStyle(
            "TdR", parent=styles["Normal"],
            fontName="Helvetica", fontSize=8.5, leading=11,
            textColor=INK, alignment=TA_RIGHT,
        ),
        "td_r_b": ParagraphStyle(
            "TdRB", parent=styles["Normal"],
            fontName="Helvetica-Bold", fontSize=8.5, leading=11,
            textColor=INK, alignment=TA_RIGHT,
        ),
        "td_c": ParagraphStyle(
            "TdC", parent=styles["Normal"],
            fontName="Helvetica", fontSize=8.5, leading=11,
            textColor=INK, alignment=TA_CENTER,
        ),
    }


def _base_table_style(n_rows):
    s = [
        ("BACKGROUND", (0, 0), (-1, 0), BRAND_CHARCOAL),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("LINEBELOW", (0, 0), (-1, 0), 0.4, BRAND_CHARCOAL),
        ("LINEBELOW", (0, -1), (-1, -1), 0.4, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for r in range(1, n_rows):
        if r % 2 == 1:
            s.append(("BACKGROUND", (0, r), (-1, r), ROW_ZEBRA))
    return s


def _header_block(story, title, subtitle, st):
    story.append(Paragraph(title, st["h1"]))
    story.append(Paragraph(subtitle, st["sub"]))
    rule = Table([[""]], colWidths=[USABLE_W], rowHeights=[0.6])
    rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.8, BRAND_GOLD)]))
    story.append(Spacer(1, 4 * mm))
    story.append(rule)
    story.append(Spacer(1, 5 * mm))


def _kpi_cards(story, cards, st):
    def _cell(label, value, hint):
        return [
            Paragraph(label.upper(), st["kpi_label"]),
            Paragraph(value, st["kpi_value"]),
            Paragraph(hint, st["kpi_hint"]),
        ]

    rows = []
    for i in range(0, len(cards), 2):
        pair = cards[i:i + 2]
        row = [_cell(*pair[0])]
        row.append(_cell(*pair[1]) if len(pair) > 1 else [Paragraph("", st["kpi_hint"])])
        rows.append(row)
    kpi_table = Table(
        rows,
        colWidths=[USABLE_W / 2, USABLE_W / 2],
        rowHeights=[22 * mm] * len(rows),
    )
    kpi_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_CREAM),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    story.append(kpi_table)


def _category_table(story, title, cats, st, group_filter):
    filtered = [c for c in (cats or []) if c.get("group") == group_filter and c.get("total", 0) > 0]
    story.append(Paragraph(title, st["section"]))
    if not filtered:
        label = "pemasukan" if group_filter == "income" else "pengeluaran"
        story.append(Paragraph(
            f"<i>Tidak ada {label} tercatat pada periode ini.</i>", st["body_soft"]))
        return
    header = [
        Paragraph("Kategori", st["th"]),
        Paragraph("Nominal", st["th_r"]),
        Paragraph("% Kontribusi", st["th_r"]),
    ]
    rows = [header]
    grand = sum(c["total"] for c in filtered) or 1
    for c in filtered:
        pct = round(c["total"] * 100.0 / grand, 1)
        rows.append([
            Paragraph(f"<b>{c['name']}</b>", st["td_b"]),
            Paragraph(_fmt_rp(c["total"]), st["td_r_b"]),
            Paragraph(f"{pct}%", st["td_r"]),
        ])
        for sub in c.get("subcategories") or []:
            if not sub.get("total"):
                continue
            sub_pct = round(sub["total"] * 100.0 / grand, 1)
            rows.append([
                Paragraph(f"&nbsp;&nbsp;&nbsp;&nbsp;<i>{sub['name']}</i>", st["td"]),
                Paragraph(_fmt_rp(sub["total"]), st["td_r"]),
                Paragraph(f"{sub_pct}%", st["td_r"]),
            ])
    rows.append([
        Paragraph("<b>TOTAL</b>", st["td_b"]),
        Paragraph(_fmt_rp(grand), st["td_r_b"]),
        Paragraph("100.0%", st["td_r_b"]),
    ])
    t = Table(rows, colWidths=[110 * mm, 40 * mm, 30 * mm])
    styleset = _base_table_style(len(rows))
    styleset.append(("LINEABOVE", (0, -1), (-1, -1), 0.6, BRAND_CHARCOAL))
    styleset.append(("BACKGROUND", (0, -1), (-1, -1), BRAND_CREAM))
    t.setStyle(TableStyle(styleset))
    story.append(t)


def _footer(story, generated_by, generated_at, st):
    story.append(Spacer(1, 8 * mm))
    footer_rule = Table([[""]], colWidths=[USABLE_W], rowHeights=[0.4])
    footer_rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER)]))
    story.append(footer_rule)
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        f"Laporan digenerate otomatis oleh ERP UMAR Travel  ·  "
        f"Oleh {generated_by} pada {generated_at}",
        st["footer"],
    ))


def build_year_report_pdf(payload) -> bytes:
    """payload: hasil summary_year + {'generated_by', 'generated_at'}."""
    buf = io.BytesIO()
    year = payload.get("year", "-")
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=14 * mm,
        title=f"Analisis Keuangan {year}", author="ERP UMAR Travel",
    )
    st = _styles()
    story = []

    _header_block(
        story,
        f"Analisis Keuangan {year}",
        f"Rekap pemasukan &amp; pengeluaran tahunan  ·  Digenerate "
        f"<b>{payload.get('generated_by', '-')}</b> pada {payload.get('generated_at', '-')}",
        st,
    )

    net = payload.get("net_saldo", 0)
    net_col = STATUS_GREEN.hexval() if net >= 0 else STATUS_RED.hexval()
    monthly = payload.get("monthly") or []
    surplus_count = sum(1 for m in monthly if m.get("net", 0) > 0)
    best_month = max(monthly, key=lambda m: m.get("net", 0), default=None) if monthly else None
    best_hint = (
        f"Bulan terbaik: {_MONTH_NAMES[best_month['month']]}"
        if best_month and best_month.get("net", 0) > 0 else "Belum ada bulan surplus"
    )

    _kpi_cards(story, [
        ("Total Pemasukan", _fmt_rp_short(payload.get("total_income")),
         "Semua pemasukan tahun ini"),
        ("Total Pengeluaran", _fmt_rp_short(payload.get("total_expense")),
         "Semua pengeluaran tahun ini"),
        ("Saldo Bersih", f'<font color="{net_col}">{_fmt_rp_short(net)}</font>',
         "Pemasukan - Pengeluaran"),
        ("Ringkasan Bulanan", f"{surplus_count}/12",
         f"Bulan surplus  ·  {best_hint}"),
    ], st)

    story.append(Paragraph("Tren Bulanan", st["section"]))
    header = [
        Paragraph("Bulan", st["th"]),
        Paragraph("Pemasukan", st["th_r"]),
        Paragraph("Pengeluaran", st["th_r"]),
        Paragraph("Saldo Bersih", st["th_r"]),
    ]
    rows = [header]
    for m in monthly:
        col = STATUS_GREEN.hexval() if m.get("net", 0) >= 0 else STATUS_RED.hexval()
        rows.append([
            Paragraph(f"<b>{_MONTH_NAMES[m['month']]}</b>", st["td_b"]),
            Paragraph(_fmt_rp(m.get("income")), st["td_r"]),
            Paragraph(_fmt_rp(m.get("expense")), st["td_r"]),
            Paragraph(f'<font color="{col}"><b>{_fmt_rp(m.get("net"))}</b></font>', st["td_r"]),
        ])
    total_income = payload.get("total_income", 0)
    total_expense = payload.get("total_expense", 0)
    rows.append([
        Paragraph("<b>TOTAL</b>", st["td_b"]),
        Paragraph(_fmt_rp(total_income), st["td_r_b"]),
        Paragraph(_fmt_rp(total_expense), st["td_r_b"]),
        Paragraph(_fmt_rp(net), st["td_r_b"]),
    ])
    t = Table(rows, colWidths=[40 * mm, 45 * mm, 45 * mm, 50 * mm])
    tstyle = _base_table_style(len(rows))
    tstyle.append(("LINEABOVE", (0, -1), (-1, -1), 0.6, BRAND_CHARCOAL))
    tstyle.append(("BACKGROUND", (0, -1), (-1, -1), BRAND_CREAM))
    t.setStyle(TableStyle(tstyle))
    story.append(t)

    _category_table(story, "Pemasukan per Kategori", payload.get("by_category"), st, "income")
    _category_table(story, "Pengeluaran per Kategori", payload.get("by_category"), st, "expense")

    _footer(story, payload.get("generated_by", "-"), payload.get("generated_at", "-"), st)

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()


def build_month_report_pdf(payload) -> bytes:
    """payload: hasil summary_month + {'generated_by', 'generated_at'}."""
    buf = io.BytesIO()
    year = payload.get("year", "-")
    month = payload.get("month", 0)
    month_label = f"{_MONTH_NAMES[month]} {year}" if 1 <= month <= 12 else str(year)
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=14 * mm,
        title=f"Analisis Keuangan {month_label}", author="ERP UMAR Travel",
    )
    st = _styles()
    story = []

    _header_block(
        story,
        f"Analisis Keuangan {month_label}",
        f"Rekap pemasukan &amp; pengeluaran bulanan  ·  Digenerate "
        f"<b>{payload.get('generated_by', '-')}</b> pada {payload.get('generated_at', '-')}",
        st,
    )

    net = payload.get("net", 0)
    net_col = STATUS_GREEN.hexval() if net >= 0 else STATUS_RED.hexval()
    tx_count = len(payload.get("transactions") or [])

    _kpi_cards(story, [
        ("Total Pemasukan", _fmt_rp_short(payload.get("total_income")),
         "Bulan ini"),
        ("Total Pengeluaran", _fmt_rp_short(payload.get("total_expense")),
         "Bulan ini"),
        ("Saldo Bersih", f'<font color="{net_col}">{_fmt_rp_short(net)}</font>',
         "Pemasukan - Pengeluaran"),
        ("Jumlah Transaksi", str(tx_count), "Transaksi tercatat bulan ini"),
    ], st)

    _category_table(story, "Pemasukan per Kategori", payload.get("by_category"), st, "income")
    _category_table(story, "Pengeluaran per Kategori", payload.get("by_category"), st, "expense")

    story.append(Paragraph("Detail Transaksi (50 terakhir)", st["section"]))
    txs = payload.get("transactions") or []
    if not txs:
        story.append(Paragraph(
            "<i>Belum ada transaksi tercatat pada bulan ini.</i>", st["body_soft"]))
    else:
        header = [
            Paragraph("Tanggal", st["th"]),
            Paragraph("Tipe", st["th_c"]),
            Paragraph("Kategori", st["th"]),
            Paragraph("Deskripsi", st["th"]),
            Paragraph("Nominal", st["th_r"]),
        ]
        rows = [header]
        for tx in txs:
            tanggal = (tx.get("created_at") or "")[:10]
            tipe = "Masuk" if tx.get("type") == "income" else "Keluar"
            tipe_col = STATUS_GREEN.hexval() if tx.get("type") == "income" else STATUS_RED.hexval()
            kategori = tx.get("category_name") or "<i>Tanpa Kategori</i>"
            if tx.get("parent_name"):
                kategori = f"{tx['parent_name']} / {kategori}"
            desc = (tx.get("description") or "-")[:80]
            rows.append([
                Paragraph(tanggal, st["td"]),
                Paragraph(f'<font color="{tipe_col}"><b>{tipe}</b></font>', st["td_c"]),
                Paragraph(kategori, st["td"]),
                Paragraph(desc, st["td"]),
                Paragraph(_fmt_rp(tx.get("amount")), st["td_r"]),
            ])
        t = Table(rows, colWidths=[22 * mm, 18 * mm, 44 * mm, 66 * mm, 30 * mm])
        t.setStyle(TableStyle(_base_table_style(len(rows))))
        story.append(t)

    _footer(story, payload.get("generated_by", "-"), payload.get("generated_at", "-"), st)

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
