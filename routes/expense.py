"""
Router Expense Reports (klaim biaya proyek karyawan):
- Expense Projects: master data label proyek + list approver kandidat.
- Expense Reports: Draft -> Submitted -> (Approved|Rejected) -> Paid.
  Header/lines dengan pajak per-baris + upload struk (base64), ref auto
  EXP/YYYY/NNNN, note bisa 'private' (visibility hanya owner+approver+finance),
  clone, hard delete Draft, PDF via authenticate_file_token.

Semua endpoint dulunya duduk di app.py. Pemindahan tidak mengubah kontrak API.
"""
import asyncio
import base64
import datetime
import os

from fastapi import APIRouter
from fastapi.responses import Response

import db
from auth import authenticate_file_token
from deps import (
    Depends,
    HTTPException,
    PUBLIC_DIR,
    UPLOAD_DIR,
    authenticate_token,
    get_setting,
    json_body,
    log_action,
    notify,
    parse_int,
    require_role,
)
from expense_pdf import build_expense_pdf
from deps.notifications import notify_role, notify_user  # Phase 8c-3

router = APIRouter(tags=["expense"])


# ===========================================================================
# HELPERS: totals + ref generator + akses check
# Draft -> Submitted -> (Approved oleh admin/management | Rejected) -> Paid oleh admin/finance
# ===========================================================================
def _line_totals(line):
    net = (line["unit_price_net"] or 0) * (line["qty"] or 0)
    tax = round(net * (line["tax_percent"] or 0) / 100.0)
    return net, tax, net + tax


def _report_totals(lines):
    net = tax = 0
    for ln in lines:
        n, t, _ = _line_totals(ln)
        net += n
        tax += t
    return net, tax, net + tax


def _generate_expense_ref():
    year = datetime.datetime.now().year
    row = db.query_one("SELECT COUNT(*) as c FROM expense_reports WHERE ref LIKE ?", (f"EXP/{year}/%",))
    seq = (row["c"] if row else 0) + 1
    return f"EXP/{year}/{seq:04d}"


def _mask_private_note(report, user):
    """Note 'private' hanya boleh dibaca pemilik, approver-nya, atau admin/management/finance."""
    if report.get("note_visibility") != "private":
        return report
    allowed = user["role"] in ("admin", "management", "finance") or user["id"] in (
        report.get("user_id"), report.get("approver_id"),
    )
    if not allowed:
        report = dict(report)
        report["note"] = None
    return report


def _assert_report_access(report, user, owner_only=False):
    is_owner = report["user_id"] == user["id"]
    is_reviewer = user["role"] in ("admin", "management", "finance")
    if owner_only and not (is_owner or user["role"] == "admin"):
        raise HTTPException(status_code=403, detail="Hanya pemilik laporan atau admin yang dapat mengubahnya.")
    if not (is_owner or is_reviewer):
        raise HTTPException(status_code=403, detail="Akses Ditolak")


@router.get("/api/expense-projects")
async def expense_projects_list(user=Depends(authenticate_token)):
    return db.query_all("SELECT * FROM expense_projects WHERE is_active = 1 ORDER BY name ASC", ())


@router.get("/api/expense-approvers")
async def expense_approvers_list(user=Depends(authenticate_token)):
    """Daftar minimal (id, name, role) untuk dropdown 'User responsible for approval' --
    dibuka untuk semua role karena semua karyawan perlu memilih approver saat membuat
    Expense Report, tanpa perlu akses penuh ke /api/users (admin-only, ada data gaji)."""
    return db.query_all(
        "SELECT id, name, role FROM users WHERE role IN ('admin', 'management') ORDER BY name ASC", ()
    )


@router.post("/api/expense-projects")
async def expense_projects_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nama project wajib diisi.")
    try:
        db.execute(
            "INSERT INTO expense_projects (name, created_by) VALUES (?, ?)", (name, user["name"])
        )
    except Exception as e:  # noqa: BLE001
        if "UNIQUE constraint failed" in str(e):
            raise HTTPException(status_code=400, detail="Project dengan nama ini sudah ada.")
        raise HTTPException(status_code=500, detail=str(e))
    log_action(user, "CREATE_EXPENSE_PROJECT", f"Menambah project expense: {name}")
    notify("data_updated", "expense_project")
    return {"message": "Project berhasil ditambahkan."}


@router.put("/api/expense-projects/{pid}")
async def expense_projects_update(pid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Nama project wajib diisi.")
    db.execute("UPDATE expense_projects SET name = ? WHERE id = ?", (name, pid))
    log_action(user, "UPDATE_EXPENSE_PROJECT", f"Mengubah nama project expense ID {pid} menjadi: {name}")
    notify("data_updated", "expense_project")
    return {"message": "Project berhasil diperbarui."}


@router.delete("/api/expense-projects/{pid}")
async def expense_projects_deactivate(pid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "finance", "management")
    # Nonaktifkan (bukan hard delete) agar Expense Report lama yang mereferensikannya tetap utuh.
    db.execute("UPDATE expense_projects SET is_active = 0 WHERE id = ?", (pid,))
    log_action(user, "DEACTIVATE_EXPENSE_PROJECT", f"Menonaktifkan project expense ID {pid}")
    notify("data_updated", "expense_project")
    return {"message": "Project berhasil dinonaktifkan."}


@router.get("/api/expense-reports")
async def expense_reports_list(user=Depends(authenticate_token)):
    if user["role"] in ("admin", "management", "finance"):
        rows = db.query_all(
            "SELECT r.*, p.name as project_name FROM expense_reports r "
            "LEFT JOIN expense_projects p ON r.project_id = p.id ORDER BY r.created_at DESC", ()
        )
    else:
        rows = db.query_all(
            "SELECT r.*, p.name as project_name FROM expense_reports r "
            "LEFT JOIN expense_projects p ON r.project_id = p.id WHERE r.user_id = ? ORDER BY r.created_at DESC",
            (user["id"],),
        )
    result = []
    for r in rows:
        lines = db.query_all("SELECT * FROM expense_lines WHERE report_id = ?", (r["id"],))
        net, tax, gross = _report_totals(lines)
        r = dict(_mask_private_note(r, user))
        r.update({"amount_net": net, "amount_tax": tax, "amount_gross": gross, "line_count": len(lines)})
        result.append(r)
    return result


@router.post("/api/expense-reports")
async def expense_reports_create(body: dict = Depends(json_body), user=Depends(authenticate_token)):
    """Semua role bisa membuat Expense Report baru (status awal: Draft)."""
    g = body.get
    project_id = g("project_id")
    if not project_id:
        raise HTTPException(status_code=400, detail="Project wajib dipilih.")
    project = db.query_one("SELECT * FROM expense_projects WHERE id = ?", (project_id,))
    if not project:
        raise HTTPException(status_code=404, detail="Project tidak ditemukan.")
    approver_id = g("approver_id")
    approver = db.query_one("SELECT name FROM users WHERE id = ?", (approver_id,)) if approver_id else None

    ref = _generate_expense_ref()
    last_id, _ = db.execute(
        "INSERT INTO expense_reports (ref, project_id, user_id, user_name, period_from, period_to, "
        "approver_id, approver_name, note, note_visibility) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ref, project_id, user["id"], user["name"], g("period_from"), g("period_to") or g("period_from"),
         approver_id, approver["name"] if approver else None, g("note") or "",
         g("note_visibility") if g("note_visibility") in ("public", "private") else "public"),
    )
    log_action(user, "CREATE_EXPENSE_REPORT", f"Membuat Expense Report {ref} untuk project {project['name']}")
    notify("data_updated", "expense_report")
    return {"message": "Expense Report berhasil dibuat.", "id": last_id, "ref": ref}


@router.get("/api/expense-reports/{rid}")
async def expense_reports_get(rid: int, user=Depends(authenticate_token)):
    r = db.query_one(
        "SELECT r.*, p.name as project_name FROM expense_reports r "
        "LEFT JOIN expense_projects p ON r.project_id = p.id WHERE r.id = ?", (rid,)
    )
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user)
    lines = db.query_all("SELECT * FROM expense_lines WHERE report_id = ? ORDER BY date ASC, id ASC", (rid,))
    net, tax, gross = _report_totals(lines)
    r = dict(_mask_private_note(r, user))
    r["lines"] = lines
    r["amount_net"] = net
    r["amount_tax"] = tax
    r["amount_gross"] = gross
    return r


@router.put("/api/expense-reports/{rid}")
async def expense_reports_update(rid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)
    if r["status"] != "Draft":
        raise HTTPException(status_code=400, detail="Hanya Expense Report berstatus Draft yang bisa diubah.")

    g = body.get
    approver_id = g("approver_id")
    approver = db.query_one("SELECT name FROM users WHERE id = ?", (approver_id,)) if approver_id else None
    db.execute(
        "UPDATE expense_reports SET period_from = ?, period_to = ?, approver_id = ?, approver_name = ?, "
        "note = ?, note_visibility = ? WHERE id = ?",
        (g("period_from") or r["period_from"], g("period_to") or r["period_to"],
         approver_id or r["approver_id"], approver["name"] if approver else r["approver_name"],
         g("note") if g("note") is not None else r["note"],
         g("note_visibility") if g("note_visibility") in ("public", "private") else r["note_visibility"], rid),
    )
    log_action(user, "UPDATE_EXPENSE_REPORT", f"Mengubah header Expense Report {r['ref']}")
    notify("data_updated", "expense_report")
    return {"message": "Expense Report berhasil diperbarui."}


@router.post("/api/expense-reports/{rid}/lines")
async def expense_lines_create(rid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)
    if r["status"] != "Draft":
        raise HTTPException(status_code=400, detail="Baris hanya bisa ditambahkan saat status Draft.")

    g = body.get
    unit_price = parse_int(g("unit_price_net"), "harga satuan")
    qty = parse_int(g("qty") or 1, "qty")
    tax_percent = float(g("tax_percent")) if g("tax_percent") not in (None, "") else 11.0
    if not g("category") or unit_price <= 0 or qty <= 0:
        raise HTTPException(status_code=400, detail="Kategori, harga satuan, dan qty wajib diisi dengan benar.")

    receipt_url = None
    file_base64 = g("receiptBase64")
    if file_base64:
        ext = (g("ext") or "jpg").lower().lstrip(".")
        # SECURITY: allowlist ext -- .lstrip('.') sendirinya tidak stop 'jpg/../evil'
        # (path traversal). Batasi ke ekstensi struk yang wajar.
        if ext not in ("png", "jpg", "jpeg", "webp", "pdf"):
            raise HTTPException(status_code=400, detail="Format struk harus png/jpg/webp/pdf.")
        file_name = f"expline_{rid}_{int(asyncio.get_event_loop().time()*1000)}.{ext}"
        os.makedirs(UPLOAD_DIR, exist_ok=True)
        b64 = file_base64.split(",")[1] if "," in file_base64 else file_base64
        with open(os.path.join(UPLOAD_DIR, file_name), "wb") as f:
            f.write(base64.b64decode(b64))
        receipt_url = f"/uploads/{file_name}"

    db.execute(
        "INSERT INTO expense_lines (report_id, date, category, description, unit_price_net, tax_percent, "
        "qty, receipt_url) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (rid, g("date") or datetime.date.today().isoformat(), g("category"), g("description") or "",
         unit_price, tax_percent, qty, receipt_url),
    )
    notify("data_updated", "expense_report")
    return {"message": "Item pengeluaran berhasil ditambahkan."}


@router.delete("/api/expense-reports/{rid}/lines/{lid}")
async def expense_lines_delete(rid: int, lid: int, user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)
    if r["status"] != "Draft":
        raise HTTPException(status_code=400, detail="Baris hanya bisa dihapus saat status Draft.")
    db.execute("DELETE FROM expense_lines WHERE id = ? AND report_id = ?", (lid, rid))
    notify("data_updated", "expense_report")
    return {"message": "Item pengeluaran berhasil dihapus."}


@router.post("/api/expense-reports/{rid}/submit")
async def expense_reports_submit(rid: int, user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)
    if r["status"] != "Draft":
        raise HTTPException(status_code=400, detail="Hanya Expense Report berstatus Draft yang bisa diajukan.")
    lines = db.query_all("SELECT id FROM expense_lines WHERE report_id = ?", (rid,))
    if not lines:
        raise HTTPException(status_code=400, detail="Tambahkan minimal 1 item pengeluaran sebelum mengajukan.")
    if not r["approver_id"]:
        raise HTTPException(status_code=400, detail="User responsible for approval wajib dipilih.")

    db.execute("UPDATE expense_reports SET status = 'Submitted' WHERE id = ?", (rid,))
    log_action(user, "SUBMIT_EXPENSE_REPORT", f"Mengajukan Expense Report {r['ref']} untuk approval")
    notify("data_updated", "expense_report")
    # Phase 8c-3: Notif ke approver -- yg dipilih sbg reviewer.
    if r["approver_id"]:
        notify_user(
            r["approver_id"], "expense_pending",
            f"Expense Report {r['ref']} menunggu review",
            body=f"Diajukan oleh {user['name']}. Buka Expense Report untuk approve/tolak.",
            link=f"#page-expense?rid={rid}",
        )
    return {"message": "Expense Report berhasil diajukan, menunggu persetujuan."}


@router.put("/api/expense-reports/{rid}/review")
async def expense_reports_review(rid: int, body: dict = Depends(json_body), user=Depends(authenticate_token)):
    require_role(user, "admin", "management")
    action = body.get("action")
    if action not in ("approve", "reject"):
        raise HTTPException(status_code=400, detail="Aksi harus 'approve' atau 'reject'.")

    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    if r["status"] != "Submitted":
        raise HTTPException(status_code=400, detail=f"Laporan ini berstatus '{r['status']}', tidak bisa direview ulang.")

    new_status = "Approved" if action == "approve" else "Rejected"
    note = body.get("note") or ""
    if action == "reject" and not note:
        raise HTTPException(status_code=400, detail="Alasan penolakan wajib diisi.")

    db.execute(
        "UPDATE expense_reports SET status = ?, reviewed_by = ?, review_note = ?, validation_date = CURRENT_TIMESTAMP "
        "WHERE id = ?",
        (new_status, user["name"], note, rid),
    )
    log_action(
        user, "APPROVE_EXPENSE_REPORT" if action == "approve" else "REJECT_EXPENSE_REPORT",
        f"{new_status} Expense Report {r['ref']} ({r['user_name']})",
    )
    notify("data_updated", "expense_report")
    # Phase 8c-3: Notif ke owner -- diapprove atau ditolak.
    if r["user_id"]:
        if action == "approve":
            notify_user(
                r["user_id"], "expense_approved",
                f"Expense Report {r['ref']} disetujui",
                body="Menunggu Finance untuk dibayar.",
                link=f"#page-expense?rid={rid}",
            )
        else:
            notify_user(
                r["user_id"], "expense_rejected",
                f"Expense Report {r['ref']} ditolak",
                body=f"Alasan: {note}",
                link=f"#page-expense?rid={rid}",
            )
    # Setelah approve, finance perlu tahu ada yg siap dibayar.
    if action == "approve":
        notify_role(
            "finance", "expense_ready_pay",
            f"Expense Report {r['ref']} siap dibayar",
            body=f"Diapprove {user['name']}. Buka Expense untuk pencairan.",
            link=f"#page-expense?rid={rid}",
        )
    return {"message": f"Expense Report berhasil di-{'setujui' if action == 'approve' else 'tolak'}."}


@router.put("/api/expense-reports/{rid}/pay")
async def expense_reports_pay(rid: int, user=Depends(authenticate_token)):
    require_role(user, "admin", "finance")

    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    if r["status"] != "Approved":
        raise HTTPException(
            status_code=400,
            detail="Hanya laporan berstatus 'Approved' (sudah disetujui) yang bisa dibayar.",
        )
    lines = db.query_all("SELECT * FROM expense_lines WHERE report_id = ?", (rid,))
    _, _, gross = _report_totals(lines)

    last_id, _ = db.execute(
        "INSERT INTO transactions (type, category, amount, description, reference_id) VALUES (?, ?, ?, ?, ?)",
        ("expense", "expense_report", gross, f"Expense Report {r['ref']}: {r['user_name']}", rid),
    )
    db.execute(
        "UPDATE expense_reports SET status = 'Paid', paid_by = ?, paid_at = CURRENT_TIMESTAMP, "
        "transaction_id = ? WHERE id = ?",
        (user["name"], last_id, rid),
    )
    log_action(user, "PAY_EXPENSE_REPORT", f"Membayar Expense Report {r['ref']} ({r['user_name']}, Rp {gross})")
    notify("data_updated", "expense_report")
    notify("data_updated", "transaction")
    return {"message": "Expense Report berhasil dibayar dan tercatat di Buku Kas."}


@router.post("/api/expense-reports/{rid}/clone")
async def expense_reports_clone(rid: int, user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)

    ref = _generate_expense_ref()
    new_id, _ = db.execute(
        "INSERT INTO expense_reports (ref, project_id, user_id, user_name, period_from, period_to, "
        "approver_id, approver_name, note, note_visibility) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (ref, r["project_id"], r["user_id"], r["user_name"], r["period_from"], r["period_to"],
         r["approver_id"], r["approver_name"], r["note"], r["note_visibility"]),
    )
    lines = db.query_all("SELECT * FROM expense_lines WHERE report_id = ?", (rid,))
    for ln in lines:
        db.execute(
            "INSERT INTO expense_lines (report_id, date, category, description, unit_price_net, tax_percent, qty) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (new_id, ln["date"], ln["category"], ln["description"], ln["unit_price_net"], ln["tax_percent"], ln["qty"]),
        )
    log_action(user, "CLONE_EXPENSE_REPORT", f"Menduplikasi Expense Report {r['ref']} menjadi {ref}")
    notify("data_updated", "expense_report")
    return {"message": "Expense Report berhasil diduplikasi.", "id": new_id, "ref": ref}


@router.delete("/api/expense-reports/{rid}")
async def expense_reports_delete(rid: int, user=Depends(authenticate_token)):
    r = db.query_one("SELECT * FROM expense_reports WHERE id = ?", (rid,))
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user, owner_only=True)
    if r["status"] != "Draft":
        raise HTTPException(status_code=400, detail="Hanya Expense Report berstatus Draft yang bisa dihapus.")
    db.execute("DELETE FROM expense_lines WHERE report_id = ?", (rid,))
    db.execute("DELETE FROM expense_reports WHERE id = ?", (rid,))
    log_action(user, "DELETE_EXPENSE_REPORT", f"Menghapus Expense Report {r['ref']}")
    notify("data_updated", "expense_report")
    return {"message": "Expense Report berhasil dihapus."}


@router.get("/api/expense-reports/{rid}/pdf")
async def expense_reports_pdf(rid: int, user=Depends(authenticate_file_token)):
    r = db.query_one(
        "SELECT r.*, p.name as project_name FROM expense_reports r "
        "LEFT JOIN expense_projects p ON r.project_id = p.id WHERE r.id = ?", (rid,)
    )
    if not r:
        raise HTTPException(status_code=404, detail="Expense Report tidak ditemukan")
    _assert_report_access(r, user)
    lines = db.query_all("SELECT * FROM expense_lines WHERE report_id = ? ORDER BY date ASC, id ASC", (rid,))
    company = {
        "legal_name": get_setting("company_legal_name", "Umar Travel"),
        "address": get_setting("company_address", ""),
        "email": get_setting("company_email", ""),
        "website": get_setting("company_website", ""),
    }
    logo_path = None
    logo_url = get_setting("logo_url")
    if logo_url:
        candidate = os.path.join(PUBLIC_DIR, logo_url.lstrip("/"))
        if os.path.isfile(candidate):
            logo_path = candidate
    pdf_bytes = build_expense_pdf(r, lines, company=company, logo_path=logo_path)
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f"inline; filename={r['ref'].replace('/', '-')}.pdf"},
    )
