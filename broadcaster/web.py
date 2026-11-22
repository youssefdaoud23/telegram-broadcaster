"""Authenticated website for private, consent-based Telegram broadcasts."""
from __future__ import annotations

import csv
import io
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import core, telegram, group_export, group_import

ROOT = Path(__file__).parent
app = FastAPI(title="Telegram Broadcaster", docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=str(ROOT / "static")), name="static")
templates = Jinja2Templates(directory=str(ROOT / "templates"))
templates.env.filters["moment"] = (
    lambda x: datetime.fromtimestamp(x, tz=timezone.utc).strftime("%d %b %Y · %H:%M UTC") if x else "-"
)


@app.on_event("startup")
async def startup():
    core.encryption()  # Fail closed if encryption is not configured.
    core.recipient_limit()  # Validate optional operator limit before serving.
    core.init_db(recover_inflight=True)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cache-Control"] = "no-store"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; form-action 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'"
    )
    return response


def redirect(path: str, notice: str = "") -> RedirectResponse:
    if notice:
        path += ("&" if "?" in path else "?") + "notice=" + quote(notice[:230])
    return RedirectResponse(path, status_code=303)


def principal(request: Request):
    with core.database() as db:
        person = core.session(db, request.cookies.get("tb_session"))
    if person is None:
        raise HTTPException(401, "Sign in required")
    return person


def authorize(request: Request, csrf: str):
    person = principal(request)
    if not core.check_csrf(person["csrf"], csrf):
        raise HTTPException(403, "Invalid form token. Refresh the page and try again.")
    return person


def require_owner(user):
    if user["role"] != "owner":
        raise HTTPException(403, "Only the installation owner can manage users")
    return user


async def refresh_owner_sessions(owner_id: int, *, force: bool = False) -> list[str]:
    """Read-only Telegram checks; store disconnected only on confirmed revocation."""
    with core.database() as db:
        ids = [r["id"] for r in db.execute(
            "SELECT id FROM accounts WHERE owner_id=? AND state='connected'",
            (owner_id,),
        ).fetchall()]
    return await telegram.refresh_connected_accounts(ids, force=force)


def render(request: Request, name: str, status_code: int = 200, **data):
    person = None
    with core.database() as db:
        person = core.session(db, request.cookies.get("tb_session"))
    base = {
        "user": person, "csrf": person["csrf"] if person else "",
        "notice": request.query_params.get("notice", ""),
        "send_enabled": os.getenv("SEND_ENABLED", "false").lower() == "true",
        "send_limit": core.recipient_limit(),
        "current_path": request.url.path,
        "site_template": (
            person["site_template"]
            if person is not None and person["site_template"] in core.SITE_TEMPLATES
            else "telegram"
        ),
    }
    return templates.TemplateResponse(request, name, {**base, **data}, status_code=status_code)


def problem(request: Request, message: str, status: int = 400):
    return render(request, "error.html", status_code=status, title="Something needs attention", message=message)


@app.exception_handler(HTTPException)
async def handle_http_error(request: Request, exc: HTTPException):
    if exc.status_code == 401:
        if request.url.path.startswith("/api/"):
            return JSONResponse({"error": "Sign in required"}, status_code=401)
        return redirect("/login")
    return problem(request, str(exc.detail), exc.status_code)


@app.get("/settings/appearance")
def appearance_page(request: Request):
    principal(request)
    return render(request, "appearance.html", title="Appearance")


@app.post("/settings/appearance")
def appearance_save(request: Request, csrf: str = Form(""),
                    site_template: str = Form(""),
                    return_to: str = Form("/settings/appearance")):
    person = authorize(request, csrf)
    if site_template not in core.SITE_TEMPLATES:
        raise HTTPException(400, "Unknown website template")
    with core.database() as db:
        core.set_site_template(db, person["admin_id"], site_template)
        db.commit()
    # Paths are strictly local and must not become open redirects or
    # unexpected query-string injections.
    if not re.fullmatch(r"/[A-Za-z0-9/_-]*", return_to) or return_to.startswith("//"):
        return_to = "/settings/appearance"
    return redirect(return_to, "Website template updated.")


@app.get("/settings/password")
def own_password_page(request: Request):
    principal(request)
    return render(request, "change_password.html", title="Change password")


@app.post("/settings/password")
def own_password_change(request: Request, csrf: str = Form(""),
                        current_password: str = Form(""),
                        new_password: str = Form(""),
                        confirm_password: str = Form("")):
    user=authorize(request, csrf)
    if new_password != confirm_password:
        return redirect("/settings/password", "New passwords do not match")
    try:
        with core.database() as db:
            core.change_password(db, user["admin_id"], new_password,
                                 current_password=current_password)
            db.commit()
    except ValueError as exc:
        return redirect("/settings/password", str(exc))
    response=redirect("/login", "Password changed. Sign in with your new password.")
    response.delete_cookie("tb_session", path="/")
    return response


@app.get("/users")
def users_page(request: Request):
    require_owner(principal(request))
    with core.database() as db:
        users=db.execute(
            "SELECT id,username,role,active,created_at FROM admins ORDER BY created_at,id"
        ).fetchall()
    return render(request, "users.html", title="User management", users=users)


@app.post("/users")
def create_user_action(request: Request, csrf: str = Form(""),
                       username: str = Form(""), password: str = Form(""),
                       confirm_password: str = Form("")):
    require_owner(authorize(request, csrf))
    if password != confirm_password:
        return redirect("/users", "Passwords do not match")
    try:
        with core.database() as db:
            core.create_user(db, username, password)
            db.commit()
    except ValueError as exc:
        return redirect("/users", str(exc))
    return redirect("/users", "User created with an isolated, empty workspace.")


@app.post("/users/{user_id}/password")
def owner_reset_password(request: Request, user_id: int, csrf: str = Form(""),
                         owner_password: str = Form(""),
                         new_password: str = Form(""),
                         confirm_password: str = Form("")):
    owner=require_owner(authorize(request,csrf))
    if new_password != confirm_password:
        return redirect("/users", "Passwords do not match")
    with core.database() as db:
        admin=db.execute("SELECT * FROM admins WHERE id=?", (owner["admin_id"],)).fetchone()
        target=db.execute("SELECT id,role,active FROM admins WHERE id=?", (user_id,)).fetchone()
        if not target:
            raise HTTPException(404, "User not found")
        if target["role"] == "owner":
            raise HTTPException(403, "Use Change password to update your own password")
        if not core.validate_password(admin, owner_password):
            return redirect("/users", "Owner password is incorrect")
        try:
            core.change_password(db, user_id, new_password)
        except ValueError as exc:
            return redirect("/users", str(exc))
        db.commit()
    return redirect("/users", "Password reset. The user's existing sessions were signed out.")


@app.post("/users/{user_id}/status")
def owner_user_status(request: Request, user_id: int, csrf: str = Form(""),
                      action: str = Form("")):
    require_owner(authorize(request,csrf))
    if action not in ("enable", "disable"):
        raise HTTPException(400, "Unknown user status action")
    try:
        with core.database() as db:
            core.set_user_active(db, user_id, action=="enable")
            db.commit()
    except ValueError as exc:
        return redirect("/users", str(exc))
    return redirect("/users", "User access updated.")


@app.get("/health")
def health():
    return {"service": "telegram-broadcaster", "status": "ok"}


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request):
    with core.database() as db:
        setup = db.execute("SELECT COUNT(*) AS c FROM admins").fetchone()["c"] == 0
    return render(request, "login.html", title="Sign in", setup=setup)


@app.post("/login")
def login_action(request: Request, username: str = Form(""), password: str = Form("")):
    name = username.strip().lower()[:80]
    if not name or not password:
        return redirect("/login", "Check your username and password.")
    with core.database() as db:
        if core.login_locked(db, name):
            return redirect("/login", "Too many attempts. Try again in 15 minutes.")
        row = db.execute("SELECT * FROM admins WHERE username=? AND active=1", (name,)).fetchone()
        if not core.validate_password(row, password):
            core.failed_login(db, name)
            db.commit()
            return redirect("/login", "Incorrect username or password.")
        db.execute("DELETE FROM login_attempts WHERE username=?", (name,))
        raw, _ = core.new_session(db, row["id"])
        db.commit()
    response = redirect("/")
    response.set_cookie(
        "tb_session", raw, max_age=86400 * max(1, min(30, int(os.getenv("SESSION_DAYS", "7")))),
        httponly=True, secure=os.getenv("COOKIE_SECURE", "true").lower() == "true",
        samesite="strict", path="/",
    )
    return response


@app.post("/logout")
def logout(request: Request, csrf: str = Form("")):
    authorize(request, csrf)
    with core.database() as db:
        core.revoke_session(db, request.cookies.get("tb_session"))
        db.commit()
    response = redirect("/login")
    response.delete_cookie("tb_session", path="/")
    return response


@app.get("/")
async def home(request: Request):
    user=principal(request)
    owner_id=user["admin_id"]
    await refresh_owner_sessions(owner_id)
    with core.database() as db:
        stats = {
            "contacts": db.execute("SELECT COUNT(*) c FROM contacts WHERE owner_id=? AND archived=0",(owner_id,)).fetchone()["c"],
            "eligible": db.execute("SELECT COUNT(*) c FROM contacts WHERE owner_id=? AND archived=0 AND enabled=1 AND consent=1",(owner_id,)).fetchone()["c"],
            "accounts": db.execute("SELECT COUNT(*) c FROM accounts WHERE owner_id=? AND state='connected'",(owner_id,)).fetchone()["c"],
            "broadcasts": db.execute("SELECT COUNT(*) c FROM campaigns WHERE owner_id=?",(owner_id,)).fetchone()["c"],
        }
        accounts = db.execute("SELECT id,label,username,state FROM accounts WHERE owner_id=? ORDER BY created_at DESC",(owner_id,)).fetchall()
        recent = db.execute("""
            SELECT c.id,c.state,c.created_at,c.total,a.label
            FROM campaigns c JOIN accounts a ON c.account_id=a.id AND a.owner_id=c.owner_id
            WHERE c.owner_id=?
            ORDER BY c.created_at DESC LIMIT 8
        """,(owner_id,)).fetchall()
    return render(request, "dashboard.html", title="Overview", stats=stats, accounts=accounts, recent=recent)


@app.get("/contacts")
def contacts_page(request: Request, q: str = "", group_id: int = 0):
    user=principal(request)
    with core.database() as db:
        rows = core.active_contact_rows(db,user["admin_id"])
        groups=core.contact_groups(db,user["admin_id"])
        membership=core.contact_group_map(db,user["admin_id"])
    if group_id:
        if not any(g["id"]==group_id for g in groups):
            raise HTTPException(404,"Contact group not found")
        rows=[c for c in rows if group_id in membership.get(c["id"],set())]
    if q:
        term = q.lower()[:120]
        rows = [x for x in rows if any(term in str(x[k]).lower() for k in ("name","username","phone","user_id","notes"))]
    return render(request, "contacts.html", title="Contacts", contacts=rows, query=q,
                  group_id=group_id, groups=groups, membership=membership)


@app.post("/contacts")
def add_contact(
    request: Request, csrf: str = Form(""), name: str = Form(""),
    username: str = Form(""), phone: str = Form(""), user_id: str = Form(""),
    notes: str = Form(""), consent: str = Form(""), enabled: str = Form(""),
    group_ids: list[int] = Form([]),
):
    user=authorize(request, csrf)
    try:
        item = core.normalize_contact(name, username, phone, user_id, notes)
        with core.database() as db:
            contact_id=core.insert_contact(db, item, user["admin_id"],
                                           consent=consent=="yes", enabled=enabled=="yes")
            core.set_contact_groups(db,user["admin_id"],contact_id,group_ids)
            db.commit()
    except ValueError as exc:
        return redirect("/contacts", str(exc))
    return redirect("/contacts", "Contact added to your private database.")


@app.post("/contacts/bulk-group")
async def bulk_contact_group_action(request: Request):
    person = principal(request)
    form = await request.form(max_fields=6000)
    authorize(request, str(form.get("csrf", "")))
    try:
        group_id = int(str(form.get("group_id", "")))
        selected = [int(str(item)) for item in form.getlist("contacts")]
        action = str(form.get("action", ""))
        if action not in ("add", "remove"):
            raise ValueError("Choose an allowed group action")
        with core.database() as db:
            updated = core.bulk_update_group_members(
                db, person["admin_id"], group_id, selected,
                remove=(action == "remove"),
            )
            db.commit()
    except (ValueError, TypeError) as exc:
        return redirect("/contacts", str(exc))
    verb = "Removed from" if action == "remove" else "Added to"
    return redirect("/contacts", f"{verb} group: {updated} membership(s) changed. "
                    "Existing consent and other group assignments are preserved.")


@app.post("/contacts/bulk-consent")
async def bulk_contact_consent_action(request: Request):
    person=principal(request)
    form=await request.form(max_fields=6000)
    authorize(request,str(form.get("csrf","")))
    scope=str(form.get("scope","selected"))
    grant=str(form.get("action",""))=="grant"
    action=str(form.get("action",""))
    if action not in ("grant","withdraw"):
        raise HTTPException(400,"Choose a valid consent action")
    group_id=None
    try:
        if scope not in ("selected","group"):
            raise ValueError("Choose a valid consent scope")
        group_id=int(str(form.get("group_id","0"))) if scope=="group" else None
        selected=[int(str(value)) for value in form.getlist("contacts")] if scope=="selected" else None
        verified=str(form.get("verified",""))=="yes"
        evidence=str(form.get("evidence",""))
        with core.database() as db:
            updated,total=core.bulk_update_contact_consent(
                db,person["admin_id"],person["admin_id"],
                grant=grant,evidence=evidence,verified=verified,
                contact_ids=selected,group_id=group_id,
            )
            db.commit()
    except (ValueError,TypeError) as exc:
        target=f"/contacts/groups/{group_id}" if scope=="group" and group_id else "/contacts"
        return redirect(target,str(exc))
    target=f"/contacts/groups/{group_id}" if scope=="group" else "/contacts"
    operation="Opt-in recorded" if grant else "Consent withdrawn"
    return redirect(
        target,f"{operation} for {updated} of {total} selected contacts. "
        "Other groups and contact records were preserved. Change recorded in consent history.",
    )


@app.post("/contacts/bulk-remove")
async def bulk_remove_contacts_action(request: Request):
    person=principal(request)
    form=await request.form(max_fields=6000)
    authorize(request,str(form.get("csrf","")))
    try:
        selected=[int(str(value)) for value in form.getlist("contacts")]
        with core.database() as db:
            archived=core.bulk_archive_contacts(db,person["admin_id"],selected)
            db.commit()
    except (ValueError,TypeError) as exc:
        return redirect("/contacts",str(exc))
    return redirect(
        "/contacts",
        f"{archived} contacts removed from active recipients. "
        "Broadcast history was preserved; no messages were sent.",
    )


@app.get("/contacts/consent-history")
def consent_history_page(request: Request):
    person=principal(request)
    with core.database() as db:
        rows=db.execute("""
            SELECT h.id,h.previous_consent,h.new_consent,h.evidence,h.source,
                   h.created_at,c.name AS contact_name,a.username AS actor_name
            FROM contact_consent_events h
            JOIN contacts c ON c.id=h.contact_id AND c.owner_id=h.owner_id
            JOIN admins a ON a.id=h.actor_id
            WHERE h.owner_id=?
            ORDER BY h.id DESC LIMIT 150
        """,(person["admin_id"],)).fetchall()
    return render(request,"consent_history.html",title="Consent history",events=rows)


@app.get("/contacts/groups")
def contact_group_page(request: Request):
    user=principal(request)
    with core.database() as db:
        groups=core.contact_groups(db,user["admin_id"])
    return render(request,"contact_groups.html",title="Contact groups",groups=groups)


@app.post("/contacts/groups")
def create_contact_group_action(request: Request, csrf: str = Form(""),
                                name: str = Form("")):
    user=authorize(request,csrf)
    try:
        with core.database() as db:
            group_id=core.add_contact_group(db,user["admin_id"],name)
            db.commit()
    except ValueError as exc:
        return redirect("/contacts/groups",str(exc))
    return redirect(f"/contacts/groups/{group_id}","Group created. Assign contacts below.")


@app.get("/contacts/groups/{group_id}")
def edit_group_page(request: Request, group_id: int):
    user=principal(request)
    with core.database() as db:
        group=db.execute(
            "SELECT id,name FROM contact_groups WHERE id=? AND owner_id=?",
            (group_id,user["admin_id"]),
        ).fetchone()
        rows=core.active_contact_rows(db,user["admin_id"])
        membership=core.contact_group_map(db,user["admin_id"])
    if group is None:
        raise HTTPException(404,"Contact group not found")
    return render(request,"contact_group_edit.html",title="Manage group",
                  group=group,contacts=rows,members={
                      row["id"] for row in rows if group_id in membership.get(row["id"],set())
                  })


@app.post("/contacts/groups/{group_id}/rename")
def rename_group_action(request: Request, group_id: int,
                        csrf: str = Form(""), name: str = Form("")):
    user=authorize(request,csrf)
    try:
        with core.database() as db:
            core.update_contact_group(db,user["admin_id"],group_id,name)
            db.commit()
    except ValueError as exc:
        return redirect("/contacts/groups",str(exc))
    return redirect(f"/contacts/groups/{group_id}","Group renamed.")


@app.post("/contacts/groups/{group_id}/members")
async def edit_group_members(request: Request, group_id: int):
    user=principal(request)
    form=await request.form(max_fields=20_000)
    authorize(request,str(form.get("csrf","")))
    try:
        contact_ids=[int(str(x)) for x in form.getlist("contacts")]
        with core.database() as db:
            if not db.execute(
                "SELECT 1 FROM contact_groups WHERE id=? AND owner_id=?",
                (group_id,user["admin_id"]),
            ).fetchone():
                raise HTTPException(404,"Contact group not found")
            core.set_group_members(db,user["admin_id"],group_id,contact_ids)
            db.commit()
    except (ValueError,TypeError) as exc:
        return redirect(f"/contacts/groups/{group_id}",str(exc))
    return redirect(f"/contacts/groups/{group_id}","Group membership saved.")


@app.post("/contacts/groups/{group_id}/delete")
def delete_group_action(request: Request, group_id: int,csrf: str = Form("")):
    user=authorize(request,csrf)
    with core.database() as db:
        removed=db.execute(
            "DELETE FROM contact_groups WHERE id=? AND owner_id=?",
            (group_id,user["admin_id"]),
        )
        if not removed.rowcount:
            raise HTTPException(404,"Contact group not found")
        db.commit()
    return redirect("/contacts/groups","Group deleted. Contacts and broadcast history remain intact.")


@app.get("/contacts/{contact_id}/edit")
def edit_contact_page(request: Request, contact_id: int):
    user=principal(request)
    with core.database() as db:
        record = db.execute("SELECT * FROM contacts WHERE id=? AND owner_id=? AND archived=0", (contact_id,user["admin_id"])).fetchone()
        groups=core.contact_groups(db,user["admin_id"])
        membership=core.contact_group_map(db,user["admin_id"])
    if record is None:
        raise HTTPException(404, "Contact not found")
    return render(request, "contact_edit.html", title="Edit contact", contact=record,
                  groups=groups, assigned_groups=membership.get(contact_id,set()))


@app.post("/contacts/{contact_id}/edit")
def edit_contact_action(
    request: Request, contact_id: int, csrf: str = Form(""), name: str = Form(""),
    username: str = Form(""), phone: str = Form(""), user_id: str = Form(""),
    notes: str = Form(""), consent: str = Form(""), enabled: str = Form(""),
    group_ids: list[int] = Form([]),
):
    user=authorize(request, csrf)
    try:
        item = core.normalize_contact(name, username, phone, user_id, notes)
        with core.database() as db:
            existing = db.execute("SELECT id FROM contacts WHERE id=? AND owner_id=? AND archived=0", (contact_id,user["admin_id"])).fetchone()
            if not existing:
                raise HTTPException(404, "Contact not found")
            target = item["username"] or item["phone"] or item["user_id"]
            duplicate = db.execute("""
                SELECT id FROM contacts WHERE owner_id=? AND archived=0 AND id<>? AND
                ((?<>'' AND username=?) OR (?<>'' AND phone=?) OR (?<>'' AND user_id=?))
            """, (user["admin_id"],contact_id,item["username"],item["username"],item["phone"],item["phone"],item["user_id"],item["user_id"])).fetchone()
            if duplicate:
                raise ValueError("This Telegram identifier belongs to another contact")
            db.execute("""
                UPDATE contacts SET name=?,username=?,phone=?,user_id=?,notes=?,enabled=?,consent=?,updated_at=?
                WHERE id=? AND owner_id=?
            """, (item["name"],item["username"],item["phone"],item["user_id"],item["notes"],int(enabled=="yes"),int(consent=="yes"),core.now(),contact_id,user["admin_id"]))
            core.set_contact_groups(db,user["admin_id"],contact_id,group_ids)
            db.commit()
    except ValueError as exc:
        return redirect(f"/contacts/{contact_id}/edit", str(exc))
    return redirect("/contacts", "Contact updated.")


@app.post("/contacts/{contact_id}/remove")
def remove_contact(request: Request, contact_id: int, csrf: str = Form("")):
    user=authorize(request, csrf)
    with core.database() as db:
        updated=db.execute("UPDATE contacts SET archived=1,enabled=0,updated_at=? WHERE id=? AND owner_id=?", (core.now(),contact_id,user["admin_id"]))
        if not updated.rowcount:
            raise HTTPException(404, "Contact not found")
        db.commit()
    return redirect("/contacts", "Contact removed from active recipients. Existing delivery history was preserved.")


@app.post("/contacts/import")
async def import_contacts(request: Request, csrf: str = Form(""),
                          file: UploadFile = File(...),
                          group_name: str = Form("")):
    user = authorize(request, csrf)
    raw = await file.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        return redirect("/contacts", "CSV file too large (1 MB maximum).")
    try:
        contents = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(contents, newline=""), strict=True)
        if not reader.fieldnames or not {"Name", "name"}.intersection(reader.fieldnames):
            raise ValueError("CSV requires a Name column and Telegram ID, Username or Phone")
        records = list(reader)
        if len(records) > 5000:
            raise ValueError("CSV has more than 5,000 rows; split it into smaller files.")
        if not records:
            raise ValueError("CSV has no contact rows")
        requested_group = core.validate_group_name(group_name) if group_name.strip() else ""
        yes = lambda value: str(value or "").strip().casefold() in {"yes", "true", "1", "y"}
        created = linked = unchanged = skipped = new_groups = 0
        skipped_reasons: dict[str, int] = {}
        imported_group_ids: set[int] = set()
        with core.database() as db:
            known_groups = {
                group["name"].casefold(): group["id"]
                for group in core.contact_groups(db, user["admin_id"])
            }
            for row in records:
                db.execute("SAVEPOINT import_contact")
                pending_groups: dict[str, int] = {}
                try:
                    if None in row:
                        raise ValueError("Row has more columns than the CSV header")
                    raw_identifier = (row.get("ID") or "").strip()
                    username = (row.get("Username") or row.get("username") or
                                (raw_identifier if raw_identifier.startswith("@") else ""))
                    phone = (row.get("Phone") or row.get("Phone Number") or row.get("phone") or
                             (raw_identifier if raw_identifier.startswith("+") else ""))
                    numeric = (row.get("User ID") or row.get("Telegram User ID") or
                               (raw_identifier if raw_identifier.isdigit() else ""))
                    data = core.normalize_contact(
                        row.get("Name") or row.get("name") or "",
                        username, phone, numeric, row.get("Notes") or "",
                    )
                    explicit_names = [
                        core.validate_group_name(name.strip())
                        for name in (row.get("Groups") or "").split(";") if name.strip()
                    ]
                    keys = [name.casefold() for name in explicit_names]
                    if len(keys) != len(set(keys)):
                        raise ValueError("Duplicate group name in CSV row")
                    selected_groups = []
                    for key in keys:
                        if key not in known_groups:
                            raise ValueError("Unknown contact group in CSV; create it before importing.")
                        selected_groups.append(known_groups[key])

                    # A manually chosen group takes precedence over the
                    # Telegram Group metadata in scan-generated CSV files.
                    automatic_name = requested_group
                    if not automatic_name and (row.get("Telegram Group") or "").strip():
                        raw_group_name = row["Telegram Group"].strip()
                        # Undo the spreadsheet-formula escape added by our
                        # own group CSV exporter before validating its name.
                        if (raw_group_name.startswith("'") and
                                raw_group_name[1:].lstrip().startswith(("=", "+", "-", "@"))):
                            raw_group_name = raw_group_name[1:]
                        automatic_name = group_export.suggested_group_name(
                            raw_group_name
                        )
                    if automatic_name:
                        key = automatic_name.casefold()
                        group_id = known_groups.get(key) or pending_groups.get(key)
                        if group_id is None:
                            if new_groups + len(pending_groups) >= 100:
                                raise ValueError("Import may create at most 100 new groups")
                            group_id = core.add_contact_group(
                                db, user["admin_id"], automatic_name,
                            )
                            pending_groups[key] = group_id
                        selected_groups.append(group_id)
                    selected_groups = sorted(set(selected_groups))
                    active = (yes(row.get("Enabled")) if
                              str(row.get("Enabled") or "").strip() else
                              not yes(row.get("Do Not Contact")))
                    is_new, added_links = group_import.import_contact_record(
                        db, data, user["admin_id"], selected_groups,
                        consent=yes(row.get("Opt In") or row.get("Consent")),
                        enabled=active,
                    )
                    db.execute("RELEASE SAVEPOINT import_contact")
                    known_groups.update(pending_groups)
                    new_groups += len(pending_groups)
                    imported_group_ids.update(selected_groups)
                    if is_new:
                        created += 1
                    elif added_links:
                        linked += 1
                    else:
                        unchanged += 1
                except (ValueError, sqlite3.IntegrityError, TypeError) as exc:
                    db.execute("ROLLBACK TO SAVEPOINT import_contact")
                    db.execute("RELEASE SAVEPOINT import_contact")
                    skipped += 1
                    reason = str(exc).splitlines()[0][:100] or "Invalid contact"
                    skipped_reasons[reason] = skipped_reasons.get(reason, 0) + 1
            db.commit()
        message = (
            f"Imported {created} contacts; linked {linked} existing contacts; "
            f"{unchanged} already present; {skipped} skipped; "
            f"{new_groups} groups created."
        )
        if skipped:
            reason = max(skipped_reasons, key=skipped_reasons.get)
            message += f" Main skip reason: {reason[:85]}."
        message += " Existing consent and disabled settings were preserved."
        target = "/contacts"
        if requested_group:
            group_id = known_groups.get(requested_group.casefold())
            if group_id is not None:
                target = f"/contacts/groups/{group_id}"
        elif len(imported_group_ids) == 1:
            target = f"/contacts/groups/{next(iter(imported_group_ids))}"
        return redirect(target, message)
    except (UnicodeError, ValueError, csv.Error) as exc:
        return redirect("/contacts", f"Import rejected: {str(exc)[:150]}")


@app.get("/contacts/export")
def export_contacts(request: Request):
    user=principal(request)
    with core.database() as db:
        rows = core.active_contact_rows(db,user["admin_id"])
        groups=core.contact_groups(db,user["admin_id"])
        membership=core.contact_group_map(db,user["admin_id"])
    labels={group["id"]:group["name"] for group in groups}
    target = io.StringIO()
    writer = csv.writer(target)
    writer.writerow(["Name","Username","Phone","User ID","Notes","Opt In","Enabled","Groups"])
    for r in rows:
        group_names="; ".join(sorted(
            (labels[gid] for gid in membership.get(r["id"],set()) if gid in labels),
            key=str.casefold,
        ))
        writer.writerow([_csv_safe_name(r["name"]),r["username"],r["phone"],r["user_id"],
                         _csv_safe_name(r["notes"]),
                         "yes" if r["consent"] else "no","yes" if r["enabled"] else "no",
                         _csv_safe_name(group_names)])
    return StreamingResponse(
        iter([target.getvalue()]),media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=telegram-contacts.csv","Cache-Control":"no-store"},
    )


@app.get("/groups")
async def group_export_page(request: Request, account_id: str = ""):
    user = principal(request)
    with core.database() as db:
        accounts = db.execute(
            "SELECT id,label,username FROM accounts WHERE owner_id=? AND state='connected' "
            "ORDER BY label", (user["admin_id"],),
        ).fetchall()
    groups = []
    error = ""
    if account_id:
        if not any(row["id"] == account_id for row in accounts):
            raise HTTPException(404, "Connected Telegram account not found")
        try:
            groups = await group_export.list_groups(account_id, user["admin_id"])
        except telegram.TelegramProblem as exc:
            error = str(exc)
    return render(
        request, "groups.html", title="Group member export",
        accounts=accounts, account_id=account_id, groups=groups, error=error,
        dialog_limit=group_export.DIALOG_SCAN_LIMIT,
    )


@app.post("/groups/scans")
async def group_scan_start(request: Request, csrf: str = Form(""),
                           account_id: str = Form(""), peer_id: str = Form("")):
    user = authorize(request, csrf)
    try:
        scan = group_export.start_scan(
            user["admin_id"], account_id, int(peer_id),
        )
    except (TypeError, ValueError, telegram.TelegramProblem) as exc:
        return redirect("/groups" + ("?account_id=" + quote(account_id) if account_id else ""),
                        str(exc))
    return redirect(f"/groups/scans/{scan.token}")


@app.get("/groups/scans/{scan_token}")
def group_scan_page(request: Request, scan_token: str):
    user = principal(request)
    scan = group_export.get_scan(scan_token, user["admin_id"])
    if scan is None:
        raise HTTPException(404, "Group scan not found or expired")
    return render(
        request, "group_scan.html", title="Group scan results",
        scan=scan, sample=scan.members[:40],
        suggested_group_name=group_export.suggested_group_name(scan.group_title),
        direct_import_limit=group_import.DIRECT_IMPORT_LIMIT,
    )


def _csv_safe_name(value: str) -> str:
    """Prevent spreadsheet formula execution in a user-supplied display name."""
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


@app.get("/groups/scans/{scan_token}/export")
def group_scan_csv(request: Request, scan_token: str):
    user = principal(request)
    scan = group_export.get_scan(scan_token, user["admin_id"])
    if scan is None:
        raise HTTPException(404, "Group scan not found or expired")
    if scan.state not in ("complete", "partial", "limited") or not scan.members:
        raise HTTPException(409, "No group member details are ready for export")
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    writer.writerow(["Name", "Username", "Phone Number", "Telegram User ID", "Telegram Group"])
    for member in sorted(scan.members, key=lambda m: (m.name.casefold(), m.username.casefold())):
        # Missing usernames/phones stay blank. An ID may resolve only through
        # the Telegram session that originally saw that group member.
        # Neutralize formula-like names when opened in spreadsheet apps.
        writer.writerow([_csv_safe_name(member.name), member.username,
                         member.phone, member.user_id,
                         _csv_safe_name(group_export.suggested_group_name(scan.group_title))])
    return StreamingResponse(
        iter([output.getvalue().encode("utf-8-sig")]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="telegram-group-members.csv"',
                 "X-Group-Scan-State": scan.state,
                 "Cache-Control": "no-store", "Pragma": "no-cache"},
    )


@app.post("/groups/scans/{scan_token}/import")
def import_group_scan_action(request: Request, scan_token: str,
                             csrf: str = Form(""), group_name: str = Form("")):
    person = authorize(request, csrf)
    scan = group_export.get_scan(scan_token, person["admin_id"])
    if scan is None:
        raise HTTPException(404, "Group scan not found or expired")
    try:
        with core.database() as db:
            summary = group_import.import_scan_members(
                db, scan, person["admin_id"], group_name,
            )
            db.commit()
    except ValueError as exc:
        return redirect(f"/groups/scans/{scan_token}", str(exc))
    return redirect(
        f"/contacts/groups/{summary.group_id}",
        f"Group {'created' if summary.created_group else 'reused'}: "
        f"{summary.created} contacts added, {summary.linked_existing} existing contacts linked, "
        f"{summary.already_in_group} already in group, {summary.skipped} skipped. "
        "New contacts are not opted in. No messages were sent.",
    )


@app.get("/accounts")
async def accounts_page(request: Request):
    user=principal(request)
    warnings=await refresh_owner_sessions(user["admin_id"])
    with core.database() as db:
        accounts = db.execute(
            "SELECT id,label,username,state,last_error,created_at FROM accounts "
            "WHERE owner_id=? ORDER BY created_at DESC",
            (user["admin_id"],),
        ).fetchall()
    return render(request, "accounts.html", title="Telegram accounts",
                  accounts=accounts, session_warnings=warnings)


@app.post("/accounts/refresh")
async def refresh_accounts_action(request: Request, csrf: str = Form("")):
    user=authorize(request,csrf)
    warnings=await refresh_owner_sessions(user["admin_id"],force=True)
    return redirect("/accounts",warnings[0] if warnings else "Telegram session statuses refreshed. No messages sent.")


@app.get("/accounts/new")
def new_account_page(request: Request):
    principal(request)
    return render(request, "account_new.html", title="Connect Telegram")


@app.post("/accounts/new")
def new_account(
    request: Request, csrf: str = Form(""), label: str = Form(""), api_id: str = Form(""),
    api_hash: str = Form(""), phone: str = Form(""), expected_username: str = Form(""),
):
    user=authorize(request, csrf)
    label, phone, expected_username = label.strip(), phone.strip(), expected_username.strip().lower()
    if not (1 <= len(label) <= 60) or not re.fullmatch(r"[0-9]{5,15}", api_id.strip()) or not re.fullmatch(r"[a-fA-F0-9]{32}",api_hash.strip()) or not re.fullmatch(r"\+[1-9][0-9]{6,14}",phone):
        return redirect("/accounts/new", "Check the label, API ID, API Hash and international phone number.")
    if expected_username:
        if not expected_username.startswith("@"):expected_username="@"+expected_username
        if not re.fullmatch(r"@[a-z0-9_]{5,32}", expected_username):
            return redirect("/accounts/new", "Invalid expected Telegram username.")
    uid=uuid.uuid4().hex
    with core.database() as db:
        db.execute("""INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,expected_username,session_name,created_at,updated_at,owner_id)
                      VALUES(?,?,?,?,?,?,?,?,?,?)""",
                   (uid,label,int(api_id),core.encrypt(api_hash.strip()),core.encrypt(phone),expected_username,uid,core.now(),core.now(),user["admin_id"]))
        db.commit()
    return redirect(f"/accounts/{uid}/setup", "Account stored securely. Request a Telegram login code to continue.")


def account_detail(account_id: str, owner_id: int):
    if not re.fullmatch(r"[a-f0-9]{32}",account_id):raise HTTPException(404,"Account not found")
    with core.database() as db:
        a=db.execute("SELECT id,label,expected_username,username,state,last_error,created_at FROM accounts WHERE id=? AND owner_id=?", (account_id,owner_id)).fetchone()
    if not a:raise HTTPException(404,"Account not found")
    return a


@app.get("/accounts/{account_id}/setup")
async def account_setup(request: Request, account_id: str):
    user=principal(request)
    account=account_detail(account_id,user["admin_id"])
    if account["state"] == "connected":
        await telegram.refresh_connected_accounts([account_id])
        account=account_detail(account_id,user["admin_id"])
    return render(request,"account_setup.html",title="Verify Telegram account",account=account)


@app.post("/accounts/{account_id}/request-code")
async def account_request_code(request: Request, account_id: str, csrf: str = Form("")):
    user=authorize(request,csrf)
    account_detail(account_id,user["admin_id"])
    try:await telegram.request_code(account_id)
    except telegram.TelegramProblem as exc:return redirect(f"/accounts/{account_id}/setup",str(exc))
    return redirect(f"/accounts/{account_id}/setup", "Code requested. Check your Telegram app for the verification message.")


@app.post("/accounts/{account_id}/verify-code")
async def account_verify_code(request: Request, account_id: str, csrf: str = Form(""), code: str = Form("")):
    user=authorize(request,csrf)
    account_detail(account_id,user["admin_id"])
    try:
        success=await telegram.verify_code(account_id,code)
    except telegram.TelegramProblem as exc:return redirect(f"/accounts/{account_id}/setup",str(exc))
    return redirect(f"/accounts/{account_id}/setup",
                    "Telegram account connected." if success else "Two-step verification enabled. Enter your Telegram password below.")


@app.post("/accounts/{account_id}/verify-password")
async def account_verify_password(request: Request, account_id: str, csrf: str = Form(""), password: str = Form("")):
    user=authorize(request,csrf)
    account_detail(account_id,user["admin_id"])
    try:await telegram.verify_password(account_id,password)
    except telegram.TelegramProblem as exc:return redirect(f"/accounts/{account_id}/setup",str(exc))
    return redirect(f"/accounts/{account_id}/setup","Telegram account connected.")


@app.post("/accounts/{account_id}/verify")
async def account_verify(request: Request, account_id: str, csrf: str = Form("")):
    user=authorize(request,csrf)
    account_detail(account_id,user["admin_id"])
    try:name=await telegram.verify_connected(account_id)
    except telegram.TelegramProblem as exc:return redirect(f"/accounts/{account_id}/setup",str(exc))
    return redirect(f"/accounts/{account_id}/setup",f"Verified: {name}. No messages sent.")


async def composer_context(owner_id: int, *, source_id: str = "",
                           draft_id: str = "", group_id: int = 0) -> dict:
    """Prepare an editable form without ever sending Telegram messages."""
    await refresh_owner_sessions(owner_id)
    prefill={"message":"","account_id":"","contact_ids":set(),
             "from_campaign":"","edit_campaign":"","source":None,
             "prior_sent":0,"prior_uncertain":0,"delivery_states":{},
             "selected_group_ids":set()}
    with core.database() as db:
        accounts=db.execute(
            "SELECT id,label,username FROM accounts WHERE owner_id=? "
            "AND state='connected' ORDER BY label", (owner_id,),
        ).fetchall()
        contacts=db.execute(
            "SELECT * FROM contacts WHERE owner_id=? AND archived=0 AND enabled=1 "
            "AND consent=1 ORDER BY name COLLATE NOCASE", (owner_id,),
        ).fetchall()
        groups=core.contact_groups(db,owner_id)
        membership=core.contact_group_map(db,owner_id)
        if group_id and not (source_id or draft_id):
            if not any(g["id"]==group_id for g in groups):
                raise HTTPException(404,"Contact group not found")
            prefill["selected_group_ids"]={group_id}
            prefill["contact_ids"]={
                contact["id"] for contact in contacts
                if group_id in membership.get(contact["id"],set())
            }
        if source_id or draft_id:
            campaign_id=draft_id or source_id
            campaign, deliveries=core.campaign_summary(db,campaign_id,owner_id)
            if campaign is None:
                raise HTTPException(404, "Broadcast not found")
            if draft_id and campaign["state"] != "draft":
                raise HTTPException(409, "Only unsent drafts can be edited")
            allowed={row["id"] for row in contacts}
            prefill.update(
                message=campaign["message"],
                account_id=campaign["account_id"] if any(
                    a["id"]==campaign["account_id"] for a in accounts
                ) else "",
                contact_ids={d["contact_id"] for d in deliveries if d["contact_id"] in allowed},
                source=campaign,
                from_campaign=source_id,
                edit_campaign=draft_id,
                prior_sent=sum(d["state"]=="sent" for d in deliveries),
                prior_uncertain=sum(d["state"] in ("unknown","sending","failed","blocked")
                                    for d in deliveries),
                delivery_states={d["contact_id"]:d["state"] for d in deliveries},
            )
    return {"accounts":accounts,"contacts":contacts,"groups":groups,
            "membership":membership,**prefill}


@app.get("/compose")
async def compose(request: Request, from_campaign: str = "", group_id: int = 0):
    user=principal(request)
    context=await composer_context(user["admin_id"],source_id=from_campaign,group_id=group_id)
    return render(request,"compose.html",title="Reuse broadcast" if from_campaign else "New broadcast",**context)


@app.post("/compose/preview")
async def prepare_broadcast(request: Request):
    # Authenticate before parsing a potentially large list of selected contacts.
    user=principal(request)
    form=await request.form(max_fields=20_000)
    authorize(request,str(form.get("csrf","")))
    account_id=str(form.get("account_id",""))
    message=str(form.get("message","")).strip()
    supplied=form.getlist("contacts")
    source_id=str(form.get("reuse_source_id","")).strip()
    try:
        ids=[int(str(x)) for x in supplied]
        limit=core.recipient_limit()
        with core.database() as db:
            if source_id and not db.execute(
                "SELECT 1 FROM campaigns WHERE id=? AND owner_id=?",
                (source_id,user["admin_id"]),
            ).fetchone():
                raise HTTPException(404, "Original broadcast not found")
            cid=core.create_campaign(db,account_id,message,ids,limit,user["admin_id"])
            if source_id:
                db.execute(
                    "INSERT INTO campaign_reuses(source_campaign_id,new_campaign_id,created_at) "
                    "VALUES (?,?,?)", (source_id,cid,core.now()),
                )
            db.commit()
    except (TypeError,ValueError) as exc:
        return redirect(
            "/compose" + ("?from_campaign="+quote(source_id) if source_id else ""),
            str(exc),
        )
    return redirect(f"/campaigns/{cid}", "New draft ready. Verify recipients before sending.")


@app.get("/campaigns/{campaign_id}/edit")
async def edit_campaign_page(request: Request, campaign_id: str):
    user=principal(request)
    context=await composer_context(user["admin_id"],draft_id=campaign_id)
    return render(request,"compose.html",title="Edit draft",**context)


@app.post("/campaigns/{campaign_id}/edit")
async def edit_campaign_action(request: Request, campaign_id: str):
    user=principal(request)
    form=await request.form(max_fields=20_000)
    authorize(request,str(form.get("csrf","")))
    account_id=str(form.get("account_id",""))
    message=str(form.get("message","")).strip()
    try:
        ids=[int(str(x)) for x in form.getlist("contacts")]
        with core.database() as db:
            source,_=core.campaign_summary(db,campaign_id,user["admin_id"])
            if source is None:
                raise HTTPException(404,"Draft not found")
            core.update_campaign_draft(
                db,campaign_id,user["admin_id"],account_id,message,ids,
                core.recipient_limit(),
            )
            db.commit()
    except (ValueError,TypeError) as exc:
        return redirect(f"/campaigns/{campaign_id}/edit",str(exc))
    return redirect(f"/campaigns/{campaign_id}","Draft updated. Review before sending.")


@app.get("/campaigns")
def campaigns_page(request: Request):
    user=principal(request)
    with core.database() as db:
        rows=db.execute("""
            SELECT c.id,c.state,c.created_at,c.total,a.label,
            (SELECT COUNT(*) FROM deliveries d WHERE d.campaign_id=c.id AND d.state='sent') sent,
            (SELECT COUNT(*) FROM deliveries d JOIN contacts ct ON ct.id=d.contact_id AND ct.owner_id=c.owner_id
             WHERE d.campaign_id=c.id AND d.state='pending'
               AND ct.archived=0 AND ct.enabled=1 AND ct.consent=1) retryable,
            (SELECT retry_campaign_id FROM campaign_retries r WHERE r.source_campaign_id=c.id) retry_id
            FROM campaigns c JOIN accounts a ON a.id=c.account_id AND a.owner_id=c.owner_id
            WHERE c.owner_id=? ORDER BY c.created_at DESC LIMIT 100
        """,(user["admin_id"],)).fetchall()
    return render(request,"campaigns.html",title="Broadcast history",campaigns=rows)


@app.get("/campaigns/{campaign_id}")
def campaign_page(request: Request, campaign_id: str):
    user=principal(request)
    with core.database() as db:
        campaign, deliveries=core.campaign_summary(db,campaign_id,user["admin_id"])
        retryable=core.retryable_count(db,campaign_id,user["admin_id"]) if campaign else 0
        retry_row=(db.execute(
            "SELECT retry_campaign_id FROM campaign_retries WHERE source_campaign_id=?",
            (campaign_id,),
        ).fetchone() if campaign else None)
        reused_from=(db.execute(
            "SELECT r.source_campaign_id FROM campaign_reuses r "
            "JOIN campaigns c ON c.id=r.source_campaign_id AND c.owner_id=? "
            "WHERE r.new_campaign_id=?",
            (user["admin_id"],campaign_id),
        ).fetchone() if campaign else None)
    if campaign is None:raise HTTPException(404,"Campaign not found")
    counts={}
    for d in deliveries:counts[d["state"]]=counts.get(d["state"],0)+1
    return render(request,"campaign_detail.html",title="Broadcast details",
                  campaign=campaign,deliveries=deliveries,counts=counts,
                  retryable=retryable,retry_id=retry_row["retry_campaign_id"] if retry_row else None,
                  reused_from=reused_from["source_campaign_id"] if reused_from else None)


@app.post("/campaigns/{campaign_id}/retry")
def retry_campaign(request: Request, campaign_id: str, csrf: str = Form("")):
    user=authorize(request,csrf)
    limit=core.recipient_limit()
    try:
        with core.database() as db:
            new_id,created=core.create_retry_draft(db,campaign_id,limit,user["admin_id"])
            db.commit()
    except ValueError as exc:
        return redirect(f"/campaigns/{campaign_id}",str(exc))
    return redirect(
        f"/campaigns/{new_id}",
        "New draft created from pending opted-in recipients. Review before sending."
        if created else "A retry draft already exists. Review it before sending.",
    )


@app.post("/campaigns/{campaign_id}/send")
async def send_campaign(request: Request, campaign_id: str, csrf: str = Form("")):
    user=authorize(request,csrf)
    if os.getenv("SEND_ENABLED","false").lower() != "true":
        return redirect(f"/campaigns/{campaign_id}","Delivery is disabled in server settings. Preview is safe.")
    with core.database() as db:
        campaign, deliveries=core.campaign_summary(db,campaign_id,user["admin_id"])
        if not campaign:raise HTTPException(404,"Campaign not found")
        if campaign["account_state"] != "connected":
            return redirect(f"/campaigns/{campaign_id}","Sender session is disconnected. Reconnect Telegram before sending.")
        limit=core.recipient_limit()
        if limit is not None and len(deliveries)>limit:
            return redirect(f"/campaigns/{campaign_id}","Campaign exceeds the configured recipient limit.")
        count=db.execute("UPDATE campaigns SET state='queued' WHERE id=? AND owner_id=? AND state='draft'",(campaign_id,user["admin_id"])).rowcount
        db.commit()
    if count != 1:
        return redirect(f"/campaigns/{campaign_id}","This campaign was already submitted. No duplicate delivery was started.")
    telegram.enqueue(campaign_id)
    return redirect(f"/campaigns/{campaign_id}","Sending started. Check this page for per-recipient results.")


@app.get("/api/campaigns/{campaign_id}")
def campaign_status(request: Request, campaign_id: str):
    user=principal(request)
    with core.database() as db:
        campaign, deliveries=core.campaign_summary(db,campaign_id,user["admin_id"])
    if campaign is None:raise HTTPException(404,"Campaign not found")
    return {"id":campaign_id,"state":campaign["state"],"total":len(deliveries),
            "sent":sum(d["state"]=="sent" for d in deliveries),
            "failed":sum(d["state"] in {"failed","blocked"} for d in deliveries)}
