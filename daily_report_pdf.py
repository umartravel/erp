"""
Phase DT-5: Generator PDF Digest Laporan Harian Tim.

Dipanggil oleh routes/daily_reports.py di endpoint:
- GET /api/daily-reports/export.pdf?date_from=YYYY-MM-DD&date_to=YYYY-MM-DD&role=X

Layout & palet consistent dgn finance_analytics_pdf.py (Luxury UMAR:
gold + charcoal + cream).

Payload shape (dari _team_summary_data + _generated_meta):
{
  date_from, date_to, total_days, role_filter,
  users: [{user_name, username, role, submitted_days, submit_rate_pct,
           total_tasks, done_tasks, done_pct}, ...],
  generated_by, generated_at,
}
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
STATUS_AMBER = colors.HexColor("#B45309")

USABLE_W = 180 * mm

_ROLE_LABEL = {
    "sales": "Sales", "ops": "Operasional", "finance": "Finance",
    "management": "Management", "admin": "Admin",
}


def _role_label(role_key):
    return _ROLE_LABEL.get(role_key or "", role_key or "-")


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


def _footer(story, generated_by, generated_at, st):
    story.append(Spacer(1, 8 * mm))
    footer_rule = Table([[""]], colWidths=[USABLE_W], rowHeights=[0.4])
    footer_rule.setStyle(TableStyle([("LINEBELOW", (0, 0), (-1, -1), 0.4, BORDER)]))
    story.append(footer_rule)
    story.append(Spacer(1, 3 * mm))
    story.append(Paragraph(
        f"Laporan digenerate otomatis oleh ERP UMAR Travel  &middot;  "
        f"Oleh {generated_by} pada {generated_at}",
        st["footer"],
    ))


def build_daily_digest_pdf(payload) -> bytes:
    """payload: {date_from, date_to, total_days, role_filter, users:[...],
    generated_by, generated_at}. Returns raw PDF bytes."""
    buf = io.BytesIO()
    date_from = payload.get("date_from", "-")
    date_to = payload.get("date_to", "-")
    role_filter = payload.get("role_filter") or "semua"
    role_label = (_role_label(role_filter)
                  if role_filter != "semua" else "Semua Role")

    doc = SimpleDocTemplate(
        buf, pagesize=A4,
        leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=14 * mm,
        title=f"Digest Laporan Harian {date_from} s/d {date_to}",
        author="ERP UMAR Travel",
    )
    st = _styles()
    story = []

    _header_block(
        story,
        "Digest Laporan Harian Tim",
        f"{date_from} s/d {date_to}  &middot;  Filter: <b>{role_label}</b>  &middot;  "
        f"Digenerate <b>{payload.get('generated_by', '-')}</b> pada "
        f"{payload.get('generated_at', '-')}",
        st,
    )

    users = payload.get("users") or []
    total_users = len(users)
    total_days = payload.get("total_days", 0)

    total_submits = sum((u.get("submitted_days") or 0) for u in users)
    total_possible = total_users * total_days if total_users and total_days else 0
    avg_submit_rate = (
        round(total_submits * 100.0 / total_possible, 1) if total_possible else 0.0
    )
    total_tasks = sum((u.get("total_tasks") or 0) for u in users)
    total_done = sum((u.get("done_tasks") or 0) for u in users)
    avg_done_rate = round(total_done * 100.0 / total_tasks, 1) if total_tasks else 0.0

    rate_col = (STATUS_GREEN if avg_submit_rate >= 80
                else (STATUS_AMBER if avg_submit_rate >= 50 else STATUS_RED)).hexval()
    done_col = (STATUS_GREEN if avg_done_rate >= 80
                else (STATUS_AMBER if avg_done_rate >= 50 else STATUS_RED)).hexval()

    _kpi_cards(story, [
        ("Total Karyawan", str(total_users),
         f"Rentang {total_days} hari"),
        ("Rate Submit Rata-Rata",
         f'<font color="{rate_col}">{avg_submit_rate}%</font>',
         f"{total_submits} laporan submit dari {total_possible} slot"),
        ("Total Task", str(total_tasks),
         f"Diselesaikan {total_done}"),
        ("Rate Task Done",
         f'<font color="{done_col}">{avg_done_rate}%</font>',
         "Diselesaikan / total task"),
    ], st)

    story.append(Paragraph("Detail Per Karyawan", st["section"]))
    if not users:
        story.append(Paragraph(
            "<i>Tidak ada karyawan di rentang / filter ini.</i>", st["body_soft"]))
    else:
        header = [
            Paragraph("Karyawan", st["th"]),
            Paragraph("Role", st["th_c"]),
            Paragraph("Submit / Hari", st["th_r"]),
            Paragraph("Rate %", st["th_r"]),
            Paragraph("Task", st["th_r"]),
            Paragraph("Done %", st["th_r"]),
        ]
        rows = [header]
        # Sort by submit_rate ASC supaya karyawan yg jarang lapor muncul atas
        sorted_users = sorted(
            users, key=lambda u: (u.get("submit_rate_pct") or 0))
        for u in sorted_users:
            submitted = u.get("submitted_days") or 0
            rate_pct = u.get("submit_rate_pct") or 0
            done_pct = u.get("done_pct") or 0
            rate_c = (STATUS_GREEN if rate_pct >= 80
                      else (STATUS_AMBER if rate_pct >= 50 else STATUS_RED)).hexval()
            done_c = (STATUS_GREEN if done_pct >= 80
                      else (STATUS_AMBER if done_pct >= 50 else STATUS_RED)).hexval()
            rows.append([
                Paragraph(f"<b>{u.get('user_name') or u.get('username') or '-'}</b>",
                          st["td_b"]),
                Paragraph(_role_label(u.get("role")), st["td_c"]),
                Paragraph(f"{submitted} / {total_days}", st["td_r"]),
                Paragraph(f'<font color="{rate_c}"><b>{rate_pct}%</b></font>',
                          st["td_r"]),
                Paragraph(f"{u.get('done_tasks') or 0} / {u.get('total_tasks') or 0}",
                          st["td_r"]),
                Paragraph(f'<font color="{done_c}">{done_pct}%</font>', st["td_r"]),
            ])
        t = Table(rows, colWidths=[52 * mm, 24 * mm, 30 * mm, 24 * mm, 26 * mm, 24 * mm])
        t.setStyle(TableStyle(_base_table_style(len(rows))))
        story.append(t)

    _footer(story, payload.get("generated_by", "-"),
            payload.get("generated_at", "-"), st)

    doc.build(story)
    buf.seek(0)
    return buf.getvalue()
