"""
Generator PDF Expense Report (hardcopy) memakai reportlab.
Layout mengikuti format resmi ala Odoo (kop surat, kotak info, tabel item).
Dipanggil oleh app.py pada endpoint GET /api/expense-reports/{id}/pdf.
"""
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

NAVY = colors.HexColor("#1e3a5f")
GRAY = colors.HexColor("#666666")
LIGHT_BG = colors.HexColor("#f2f2f2")
BORDER = colors.HexColor("#999999")

STATUS_COLOR = {
    "Draft": colors.HexColor("#6b7280"),
    "Submitted": colors.HexColor("#b45309"),
    "Approved": colors.HexColor("#047857"),
    "Rejected": colors.HexColor("#b91c1c"),
    "Paid": colors.HexColor("#047857"),
}


def _fmt_rp(n):
    return f"{n:,.2f}".replace(",", "#").replace(".", ",").replace("#", ".")


def _fmt_date(s):
    if not s:
        return "-"
    d = str(s)[:10]
    parts = d.split("-")
    return f"{parts[2]}/{parts[1]}/{parts[0]}" if len(parts) == 3 else d


def _line_totals(line):
    net = (line["unit_price_net"] or 0) * (line["qty"] or 0)
    tax = round(net * (line["tax_percent"] or 0) / 100.0)
    return net, tax, net + tax


def build_expense_pdf(report, lines, company=None, logo_path=None) -> bytes:
    company = company or {}
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=16 * mm, rightMargin=16 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
    )
    styles = getSampleStyleSheet()
    p_small = ParagraphStyle("Small", parent=styles["Normal"], fontSize=8.5, leading=11)
    p_small_gray = ParagraphStyle("SmallGray", parent=p_small, textColor=GRAY)
    p_bold = ParagraphStyle("Bold", parent=p_small, fontName="Helvetica-Bold")
    p_label = ParagraphStyle("Label", parent=p_small, fontName="Helvetica-Bold", fontSize=8.5)
    p_title = ParagraphStyle("Title", parent=styles["Heading1"], fontSize=17, textColor=NAVY, alignment=2, spaceAfter=0)
    p_title_meta = ParagraphStyle("TitleMeta", parent=p_small, alignment=2, textColor=colors.black)

    elements = []

    # --- Header: logo + tagline (kiri) | judul + ref + status (kanan) ---
    if logo_path:
        try:
            logo = Image(logo_path, width=28 * mm, height=14 * mm, kind="proportional")
        except Exception:  # noqa: BLE001
            logo = Paragraph(company.get("legal_name", "Umar Travel"), p_bold)
    else:
        logo = Paragraph(company.get("legal_name", "Umar Travel"), p_bold)

    status_style = ParagraphStyle(
        "Status", parent=p_title_meta, fontName="Helvetica-Bold", fontSize=11,
        textColor=STATUS_COLOR.get(report["status"], colors.black),
    )
    header_right = [
        Paragraph("Expense report", p_title),
        Paragraph(f"Ref. : {report['ref']}", p_title_meta),
        Paragraph(f"Start date : {_fmt_date(report['period_from'])}", p_title_meta),
        Paragraph(f"End date : {_fmt_date(report['period_to'])}", p_title_meta),
        Spacer(1, 2),
        Paragraph(report["status"], status_style),
    ]
    header_table = Table([[logo, header_right]], colWidths=[90 * mm, 84 * mm])
    header_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
    ]))
    elements.append(header_table)
    elements.append(Spacer(1, 4 * mm))

    # --- Kotak info: Company (kiri) | Expense report info (kanan) ---
    company_lines = [Paragraph(company.get("legal_name", "-"), p_bold)]
    if company.get("address"):
        company_lines.append(Paragraph(company["address"], p_small))
    company_lines.append(Spacer(1, 3))
    if company.get("email"):
        company_lines.append(Paragraph(f"Email : {company['email']}", p_small))
    if company.get("website"):
        company_lines.append(Paragraph(f"Web : {company['website']}", p_small))

    info_lines = [
        Paragraph(f"Recorded by : {report['user_name'] or '-'}", p_small),
        Paragraph(f"Creation date : {_fmt_date(report['created_at'])}", p_small),
        Paragraph(f"Approved by : {report['reviewed_by'] or report['approver_name'] or '-'}", p_small),
        Paragraph(f"Approving date : {_fmt_date(report['validation_date']) if report['validation_date'] else '-'}", p_small),
    ]

    box_data = [
        [Paragraph("Information company", p_label), Paragraph("Information expense report :", p_label)],
        [Table([[c] for c in company_lines], style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)]),
         Table([[c] for c in info_lines], style=[("LEFTPADDING", (0, 0), (-1, -1), 0), ("TOPPADDING", (0, 0), (-1, -1), 1), ("BOTTOMPADDING", (0, 0), (-1, -1), 1)])],
    ]
    box_table = Table(box_data, colWidths=[87 * mm, 87 * mm])
    box_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOTTOMPADDING", (0, 0), (0, 0), 4),
        ("BOX", (1, 1), (1, 1), 0.75, BORDER),
        ("BACKGROUND", (0, 1), (0, 1), LIGHT_BG),
        ("LEFTPADDING", (0, 1), (-1, 1), 6),
        ("RIGHTPADDING", (0, 1), (-1, 1), 6),
        ("TOPPADDING", (0, 1), (-1, 1), 6),
        ("BOTTOMPADDING", (0, 1), (-1, 1), 6),
    ]))
    elements.append(box_table)
    elements.append(Spacer(1, 4 * mm))

    # --- Note (opsional) — dipakai bebas oleh pengaju, mis. info rekening tujuan
    # pencairan (karyawan/vendor/mitra), karena tujuan pembayaran expense bisa
    # berbeda-beda tiap laporan, bukan selalu rekening pribadi karyawan. ---
    if report.get("note"):
        for note_line in str(report["note"]).split("\n"):
            elements.append(Paragraph(note_line, p_small))
        elements.append(Spacer(1, 3 * mm))

    # --- Tabel item ---
    elements.append(Paragraph("Amount in Indonesia Rupiah currency", ParagraphStyle("Curr", parent=p_small, alignment=2)))
    elements.append(Spacer(1, 1))

    desc_style = ParagraphStyle("Desc", parent=p_small, leading=10)
    header_row = ["No", "Description", "Sales tax", "U.P. (excl. tax)", "Qty", "Total (excl. tax)", "Total (inc. tax)"]
    table_data = [header_row]
    net_sum = tax_sum = 0
    for i, ln in enumerate(lines, start=1):
        n, t, g = _line_totals(ln)
        net_sum += n
        tax_sum += t
        desc_parts = [f"Date:{_fmt_date(ln['date'])} Type:{ln['category'] or '-'}"]
        if report.get("project_name"):
            desc_parts.append(f"Project:{report['project_name']}")
        if ln["description"]:
            desc_parts.append(ln["description"])
        desc = Paragraph("<br/>".join(desc_parts), desc_style)
        table_data.append([
            str(i), desc, f"{ln['tax_percent'] or 0:g}%",
            _fmt_rp(ln["unit_price_net"] or 0), str(ln["qty"] or 0),
            _fmt_rp(n), _fmt_rp(g),
        ])
    if not lines:
        table_data.append(["-", "Belum ada item pengeluaran", "-", "-", "-", "-", "-"])

    item_table = Table(
        table_data,
        colWidths=[8 * mm, 60 * mm, 18 * mm, 26 * mm, 10 * mm, 28 * mm, 24 * mm],
        repeatRows=1,
    )
    item_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(item_table)
    elements.append(Spacer(1, 6 * mm))

    gross_sum = net_sum + tax_sum
    totals_data = [
        ["Total (excl. tax)", _fmt_rp(net_sum)],
        ["Total tax", _fmt_rp(tax_sum)],
        ["Total (inc. tax)", _fmt_rp(gross_sum)],
    ]
    totals_table = Table(totals_data, colWidths=[45 * mm, 40 * mm], hAlign="RIGHT")
    totals_table.setStyle(TableStyle([
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("GRID", (0, 0), (-1, -1), 0.5, BORDER),
        ("ALIGN", (1, 0), (1, -1), "RIGHT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("FONTNAME", (0, 2), (-1, 2), "Helvetica-Bold"),
        ("BACKGROUND", (0, 2), (-1, 2), LIGHT_BG),
    ]))
    elements.append(totals_table)

    def _footer(canvas, doc_):
        canvas.saveState()
        canvas.setFont("Helvetica", 7.5)
        canvas.setFillColor(GRAY)
        canvas.drawString(16 * mm, 10 * mm, f"{doc_.page} / {doc_.page}")
        canvas.drawRightString(doc_.pagesize[0] - 16 * mm, 10 * mm, "Generated by Umar CRM")
        canvas.restoreState()

    doc.build(elements, onFirstPage=_footer, onLaterPages=_footer)
    return buf.getvalue()
