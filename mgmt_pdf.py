"""
Generator PDF Executive Monthly Report untuk role management.
Konten: revenue MoM, sales attainment, top agen, insiden lapangan,
cash-on-hand, piutang top. Dipanggil oleh app.py pada endpoint
GET /api/mgmt/monthly-pdf?month=YYYY-MM.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

BRAND_GOLD = colors.HexColor("#B8860B")
BRAND_CHARCOAL = colors.HexColor("#1D1D1B")
BRAND_CREAM = colors.HexColor("#F4F1EA")
GRAY = colors.HexColor("#666666")
BORDER = colors.HexColor("#cccccc")


def _fmt_rp_short(n):
    n = int(n or 0)
    if n >= 1_000_000_000:
        return f"Rp {n / 1_000_000_000:.1f}M".replace(".0", "")
    if n >= 1_000_000:
        return f"Rp {n / 1_000_000:.1f}jt".replace(".0", "")
    if n >= 1_000:
        return f"Rp {n / 1_000:.0f}rb"
    return f"Rp {n}"


def build_monthly_report_pdf(payload) -> bytes:
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    p_small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8, leading=10)
    p_body = ParagraphStyle("Body", parent=styles["Normal"], fontSize=9, leading=12)
    p_h1 = ParagraphStyle("H1", parent=styles["Heading1"], fontSize=20, textColor=BRAND_CHARCOAL, spaceAfter=2)
    p_h2 = ParagraphStyle("H2", parent=styles["Heading2"], fontSize=13, textColor=BRAND_GOLD, spaceBefore=8, spaceAfter=4)
    p_gray = ParagraphStyle("Gray", parent=p_body, textColor=GRAY)

    story = []
    kpi = payload.get("kpi") or {}

    story.append(Paragraph("Laporan Eksekutif Bulanan", p_h1))
    story.append(Paragraph(
        f"<b>{payload.get('month_label', '-')}</b> - Digenerate oleh {payload.get('generated_by', '-')} pada {payload.get('generated_at', '-')}",
        p_gray,
    ))
    story.append(Spacer(1, 4 * mm))

    def _kpi_cell(label, value, hint=""):
        return [
            Paragraph(f"<font size=7 color=grey>{label}</font>", p_small),
            Paragraph(f"<font size=13><b>{value}</b></font>", p_body),
            Paragraph(f"<font size=7 color=grey>{hint}</font>", p_small) if hint else Paragraph("", p_small),
        ]

    mom = kpi.get("mom_pct")
    mom_hint = "vs bulan lalu: n/a"
    if mom is not None:
        arrow = "naik" if mom >= 0 else "turun"
        mom_hint = f"{arrow} {abs(mom)}% vs bulan lalu"

    rev_tgt_hint = f"target {_fmt_rp_short(kpi.get('revenue_target'))} ({kpi.get('revenue_pct')}%)" if kpi.get("revenue_target") else "target belum diset"
    cls_tgt_hint = f"target {kpi.get('closing_target')} ({kpi.get('closing_pct')}%)" if kpi.get("closing_target") else "target belum diset"

    kpi_data = [
        [
            _kpi_cell("REVENUE BULAN INI", _fmt_rp_short(kpi.get("revenue")), f"{mom_hint} - {rev_tgt_hint}"),
            _kpi_cell("CLOSING JAMAAH", str(kpi.get("closing", 0)), cls_tgt_hint),
        ],
        [
            _kpi_cell("CASH-ON-HAND", _fmt_rp_short(kpi.get("cash_saldo")), "dari buku kas"),
            _kpi_cell("PIUTANG TOTAL", _fmt_rp_short(kpi.get("piutang_total")), f"{kpi.get('piutang_count', 0)} jamaah belum lunas"),
        ],
    ]
    kpi_table = Table(kpi_data, colWidths=[85 * mm, 85 * mm], rowHeights=[25 * mm, 25 * mm])
    kpi_table.setStyle(TableStyle([
        ("BOX", (0, 0), (-1, -1), 0.5, BORDER),
        ("INNERGRID", (0, 0), (-1, -1), 0.3, BORDER),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BACKGROUND", (0, 0), (-1, -1), BRAND_CREAM),
    ]))
    story.append(kpi_table)

    # Sales Attainment
    story.append(Paragraph("Sales Attainment Bulan Ini", p_h2))
    sales_perf = payload.get("sales_perf") or []
    if sales_perf:
        rows = [["Sales", "Closing", "% Tgt", "Omzet", "% Tgt"]]
        for s in sales_perf:
            rows.append([
                s.get("name", "-"),
                str(s.get("actual_closing", 0)),
                f"{s.get('closing_pct')}%" if s.get("closing_pct") is not None else "-",
                _fmt_rp_short(s.get("actual_omzet")),
                f"{s.get('omzet_pct')}%" if s.get("omzet_pct") is not None else "-",
            ])
        t = Table(rows, colWidths=[45 * mm, 20 * mm, 20 * mm, 45 * mm, 20 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_CHARCOAL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("<i>Tidak ada data sales.</i>", p_gray))

    # Top Agen
    story.append(Paragraph("Top 10 Agen Bulan Ini", p_h2))
    top_agents = payload.get("top_agents") or []
    if top_agents:
        rows = [["#", "Nama Agen", "Closing", "Omzet"]]
        for i, a in enumerate(top_agents, 1):
            rows.append([str(i), a.get("name", "-"), str(a.get("closings", 0)), _fmt_rp_short(a.get("omzet"))])
        t = Table(rows, colWidths=[10 * mm, 90 * mm, 25 * mm, 30 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_CHARCOAL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 8),
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("ALIGN", (2, 1), (-1, -1), "RIGHT"),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("<i>Belum ada closing agen bulan ini.</i>", p_gray))

    # Insiden Lapangan
    story.append(Paragraph("Insiden Lapangan (Aktif Bulan Ini)", p_h2))
    incidents = payload.get("incidents") or []
    if incidents:
        rows = [["Severity", "Paket", "Deskripsi", "Status"]]
        for inc in incidents:
            rows.append([
                inc.get("severity", "-"),
                inc.get("package_name", "-"),
                (inc.get("incident_text") or "")[:80],
                inc.get("status", "-"),
            ])
        t = Table(rows, colWidths=[20 * mm, 40 * mm, 80 * mm, 20 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_CHARCOAL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("<i>Tidak ada insiden aktif.</i>", p_gray))

    # Piutang Top
    story.append(Paragraph("Top 10 Piutang Jamaah", p_h2))
    piutang_top = payload.get("piutang_top") or []
    if piutang_top:
        rows = [["Nama Jamaah", "Paket", "Sisa", "Status"]]
        for j in piutang_top:
            rows.append([
                j.get("name", "-"),
                (j.get("package_type") or "")[:40],
                _fmt_rp_short(j.get("sisa")),
                j.get("payment_status", "-"),
            ])
        t = Table(rows, colWidths=[55 * mm, 60 * mm, 25 * mm, 20 * mm])
        t.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), BRAND_CHARCOAL),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
            ("FONTSIZE", (0, 0), (-1, -1), 7.5),
            ("GRID", (0, 0), (-1, -1), 0.3, BORDER),
            ("ALIGN", (2, 1), (2, -1), "RIGHT"),
        ]))
        story.append(t)
    else:
        story.append(Paragraph("<i>Tidak ada piutang tercatat.</i>", p_gray))

    story.append(Spacer(1, 6 * mm))
    story.append(Paragraph(
        f"<font size=7 color=grey>Laporan ini digenerate otomatis oleh sistem ERP UMAR Travel. Data snapshot per {payload.get('generated_at', '-')}.</font>",
        p_small,
    ))

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
