"""
Generator PDF Executive Monthly Report untuk role management.
Konten: revenue MoM, sales attainment, top agen, insiden lapangan,
cash-on-hand, piutang top. Dipanggil oleh routes/mgmt_reports.py pada
endpoint GET /api/mgmt/monthly-pdf?month=YYYY-MM.
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
STATUS_AMBER = colors.HexColor("#B45309")
STATUS_GREEN = colors.HexColor("#166534")

# Layout budget: A4 210mm - (15mm left + 15mm right) = 180mm usable width.
USABLE_W = 180 * mm


def _fmt_rp_short(n):
    n = int(n or 0)
    if n >= 1_000_000_000:
        return f"Rp {n / 1_000_000_000:.1f}M".replace(".0M", "M")
    if n >= 1_000_000:
        return f"Rp {n / 1_000_000:.1f}jt".replace(".0jt", "jt")
    if n >= 1_000:
        return f"Rp {n / 1_000:.0f}rb"
    return f"Rp {n}"


def _pill(text):
    """HTML-colored severity/status pill inline in a Paragraph."""
    s = (text or "").strip()
    mapping = {
        "Critical": STATUS_RED, "High": STATUS_RED,
        "Medium": STATUS_AMBER, "Warning": STATUS_AMBER,
        "Low": STATUS_GREEN, "Info": STATUS_GREEN,
        "Open": STATUS_RED, "InProgress": STATUS_AMBER,
        "Resolved": STATUS_GREEN, "Closed": INK_SOFT,
        "DP": STATUS_AMBER, "Unpaid": STATUS_RED, "Lunas": STATUS_GREEN,
    }
    col = mapping.get(s, INK_SOFT)
    return f'<font color="{col.hexval()}"><b>{s or "-"}</b></font>'


def build_monthly_report_pdf(payload) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=14 * mm,
        title=f"Laporan Eksekutif {payload.get('month_label', '-')}",
        author="ERP UMAR Travel",
    )
    styles = getSampleStyleSheet()

    # Type scale -- pinned so tables and headings share a consistent rhythm.
    st_h1 = ParagraphStyle(
        "H1", parent=styles["Heading1"],
        fontName="Helvetica-Bold", fontSize=22, leading=26,
        textColor=BRAND_CHARCOAL, spaceAfter=0,
    )
    st_subtitle = ParagraphStyle(
        "Sub", parent=styles["Normal"],
        fontName="Helvetica", fontSize=8.5, leading=11,
        textColor=INK_SOFT, spaceAfter=0,
    )
    st_section = ParagraphStyle(
        "Section", parent=styles["Heading2"],
        fontName="Helvetica-Bold", fontSize=11, leading=14,
        textColor=BRAND_GOLD, spaceBefore=10, spaceAfter=4,
    )
    st_body_soft = ParagraphStyle(
        "BodySoft", parent=styles["Normal"],
        fontName="Helvetica", fontSize=9, leading=12, textColor=INK_SOFT,
    )
    st_footer = ParagraphStyle(
        "Footer", parent=styles["Normal"],
        fontName="Helvetica-Oblique", fontSize=7.5, leading=10, textColor=INK_SOFT,
        alignment=TA_CENTER,
    )
    st_kpi_label = ParagraphStyle(
        "KpiLabel", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=7, leading=9,
        textColor=BRAND_GOLD, spaceAfter=2,
    )
    st_kpi_value = ParagraphStyle(
        "KpiValue", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=18, leading=22,
        textColor=BRAND_CHARCOAL, spaceAfter=1,
    )
    st_kpi_hint = ParagraphStyle(
        "KpiHint", parent=styles["Normal"],
        fontName="Helvetica", fontSize=7.5, leading=10, textColor=INK_SOFT,
    )
    # Table cell paragraphs -- word-wrap in every cell (no more truncation).
    st_th = ParagraphStyle(
        "Th", parent=styles["Normal"],
        fontName="Helvetica-Bold", fontSize=8, leading=10,
        textColor=colors.white, alignment=TA_LEFT,
    )
    st_th_r = ParagraphStyle("ThR", parent=st_th, alignment=TA_RIGHT)
    st_th_c = ParagraphStyle("ThC", parent=st_th, alignment=TA_CENTER)
    st_td = ParagraphStyle(
        "Td", parent=styles["Normal"],
        fontName="Helvetica", fontSize=8.5, leading=11, textColor=INK,
    )
    st_td_b = ParagraphStyle("TdB", parent=st_td, fontName="Helvetica-Bold")
    st_td_r = ParagraphStyle("TdR", parent=st_td, alignment=TA_RIGHT)
    st_td_r_b = ParagraphStyle("TdRB", parent=st_td_r, fontName="Helvetica-Bold")
    st_td_c = ParagraphStyle("TdC", parent=st_td, alignment=TA_CENTER)

    story = []
    kpi = payload.get("kpi") or {}

    # -----------------------------------------------------------------------
    # HEADER
    # -----------------------------------------------------------------------
    story.append(Paragraph("Laporan Eksekutif Bulanan", st_h1))
    header_meta = (
        f"<b>{payload.get('month_label', '-')}</b>  &nbsp;·&nbsp;  "
        f"Digenerate oleh <b>{payload.get('generated_by', '-')}</b> "
        f"pada {payload.get('generated_at', '-')}"
    )
    story.append(Paragraph(header_meta, st_subtitle))
    rule = Table([[""]], colWidths=[USABLE_W], rowHeights=[0.6])
    rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.8, BRAND_GOLD)]))
    story.append(Spacer(1, 4 * mm))
    story.append(rule)
    story.append(Spacer(1, 5 * mm))

    # -----------------------------------------------------------------------
    # KPI CARDS (2 x 2 grid)
    # -----------------------------------------------------------------------
    mom = kpi.get("mom_pct")
    if mom is None:
        mom_hint = "MoM: n/a"
    else:
        arrow = "&#9650;" if mom >= 0 else "&#9660;"  # up/down triangles
        mom_col = STATUS_GREEN.hexval() if mom >= 0 else STATUS_RED.hexval()
        mom_hint = f'<font color="{mom_col}">{arrow} {abs(mom)}%</font> vs bulan lalu'
    rev_hint = (
        f"Target {_fmt_rp_short(kpi.get('revenue_target'))} "
        f"(<b>{kpi.get('revenue_pct')}%</b>)"
        if kpi.get("revenue_target")
        else "Target belum diset"
    )
    cls_hint = (
        f"Target {kpi.get('closing_target')} jamaah "
        f"(<b>{kpi.get('closing_pct')}%</b>)"
        if kpi.get("closing_target")
        else "Target belum diset"
    )
    revenue_hint = f"{mom_hint}  ·  {rev_hint}"

    def _kpi_cell(label, value, hint):
        return [
            Paragraph(label.upper(), st_kpi_label),
            Paragraph(value, st_kpi_value),
            Paragraph(hint, st_kpi_hint),
        ]

    kpi_data = [
        [
            _kpi_cell("Revenue Bulan Ini", _fmt_rp_short(kpi.get("revenue")), revenue_hint),
            _kpi_cell("Closing Jamaah", str(kpi.get("closing", 0)), cls_hint),
        ],
        [
            _kpi_cell("Cash-on-Hand", _fmt_rp_short(kpi.get("cash_saldo")), "Saldo buku kas"),
            _kpi_cell(
                "Piutang Jamaah",
                _fmt_rp_short(kpi.get("piutang_total")),
                f"{kpi.get('piutang_count', 0)} jamaah belum lunas",
            ),
        ],
    ]
    kpi_table = Table(
        kpi_data,
        colWidths=[USABLE_W / 2, USABLE_W / 2],
        rowHeights=[26 * mm, 26 * mm],
    )
    kpi_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.6, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.4, BORDER),
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_CREAM),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 10),
        ("RIGHTPADDING", (0, 0), (-1, -1), 10),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(kpi_table)

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
        # Zebra: baris data ganjil pakai warna cream soft.
        for r in range(1, n_rows):
            if r % 2 == 1:
                s.append(("BACKGROUND", (0, r), (-1, r), ROW_ZEBRA))
        return s

    # -----------------------------------------------------------------------
    # SALES ATTAINMENT
    # -----------------------------------------------------------------------
    story.append(Paragraph("Sales Attainment Bulan Ini", st_section))
    sales_perf = payload.get("sales_perf") or []
    if sales_perf:
        header = [
            Paragraph("Sales", st_th),
            Paragraph("Closing", st_th_r),
            Paragraph("% Target", st_th_r),
            Paragraph("Omzet", st_th_r),
            Paragraph("% Target", st_th_r),
        ]
        rows = [header]
        for s in sales_perf:
            cls_pct = s.get("closing_pct")
            omz_pct = s.get("omzet_pct")
            rows.append([
                Paragraph(s.get("name", "-"), st_td_b),
                Paragraph(str(s.get("actual_closing", 0)), st_td_r),
                Paragraph(f"{cls_pct}%" if cls_pct is not None else "&mdash;", st_td_r),
                Paragraph(_fmt_rp_short(s.get("actual_omzet")), st_td_r),
                Paragraph(f"{omz_pct}%" if omz_pct is not None else "&mdash;", st_td_r),
            ])
        t = Table(rows, colWidths=[55 * mm, 22 * mm, 23 * mm, 55 * mm, 25 * mm])
        t.setStyle(TableStyle(_base_table_style(len(rows))))
        story.append(t)
    else:
        story.append(Paragraph("<i>Tidak ada data sales untuk bulan ini.</i>", st_body_soft))

    # -----------------------------------------------------------------------
    # TOP AGEN
    # -----------------------------------------------------------------------
    story.append(Paragraph("Top 10 Agen Bulan Ini", st_section))
    top_agents = payload.get("top_agents") or []
    if top_agents:
        header = [
            Paragraph("#", st_th_c),
            Paragraph("Nama Agen", st_th),
            Paragraph("Closing", st_th_r),
            Paragraph("Omzet", st_th_r),
        ]
        rows = [header]
        for i, a in enumerate(top_agents, 1):
            rows.append([
                Paragraph(str(i), st_td_c),
                Paragraph(a.get("name") or "-", st_td_b),
                Paragraph(str(a.get("closings", 0)), st_td_r),
                Paragraph(_fmt_rp_short(a.get("omzet")), st_td_r_b),
            ])
        t = Table(rows, colWidths=[10 * mm, 100 * mm, 25 * mm, 45 * mm])
        t.setStyle(TableStyle(_base_table_style(len(rows))))
        story.append(t)
    else:
        story.append(Paragraph("<i>Belum ada closing agen bulan ini.</i>", st_body_soft))

    # -----------------------------------------------------------------------
    # INSIDEN LAPANGAN
    # -----------------------------------------------------------------------
    story.append(Paragraph("Insiden Lapangan (Aktif Bulan Ini)", st_section))
    incidents = payload.get("incidents") or []
    if incidents:
        header = [
            Paragraph("Severity", st_th_c),
            Paragraph("Paket", st_th),
            Paragraph("Deskripsi", st_th),
            Paragraph("Status", st_th_c),
        ]
        rows = [header]
        for inc in incidents:
            desc_text = (inc.get("incident_text") or "-").strip()
            rows.append([
                Paragraph(_pill(inc.get("severity", "-")), st_td_c),
                Paragraph(inc.get("package_name") or "-", st_td_b),
                Paragraph(desc_text, st_td),
                Paragraph(_pill(inc.get("status", "-")), st_td_c),
            ])
        t = Table(rows, colWidths=[22 * mm, 42 * mm, 95 * mm, 21 * mm])
        t.setStyle(TableStyle(_base_table_style(len(rows))))
        story.append(t)
    else:
        story.append(Paragraph("<i>Tidak ada insiden aktif.</i>", st_body_soft))

    # -----------------------------------------------------------------------
    # PIUTANG TOP
    # -----------------------------------------------------------------------
    story.append(Paragraph("Top 10 Piutang Jamaah", st_section))
    piutang_top = payload.get("piutang_top") or []
    if piutang_top:
        header = [
            Paragraph("Nama Jamaah", st_th),
            Paragraph("Paket", st_th),
            Paragraph("Sisa", st_th_r),
            Paragraph("Status", st_th_c),
        ]
        rows = [header]
        for j in piutang_top:
            rows.append([
                Paragraph(j.get("name") or "-", st_td_b),
                Paragraph(j.get("package_type") or "-", st_td),
                Paragraph(_fmt_rp_short(j.get("sisa")), st_td_r_b),
                Paragraph(_pill(j.get("payment_status", "-")), st_td_c),
            ])
        t = Table(rows, colWidths=[55 * mm, 70 * mm, 32 * mm, 23 * mm])
        t.setStyle(TableStyle(_base_table_style(len(rows))))
        story.append(t)
    else:
        story.append(Paragraph("<i>Tidak ada piutang tercatat.</i>", st_body_soft))

    # -----------------------------------------------------------------------
    # FOOTER
    # -----------------------------------------------------------------------
    story.append(Spacer(1, 8 * mm))
    footer_rule = Table([[""]], colWidths=[USABLE_W], rowHeights=[0.4])
    footer_rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER)]))
    story.append(footer_rule)
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        f"Laporan digenerate otomatis oleh sistem ERP UMAR Travel  ·  "
        f"Data snapshot per {payload.get('generated_at', '-')}",
        st_footer,
    ))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
