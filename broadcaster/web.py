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

from . import core, telegram

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
def home(request: Request):
    user=principal(request)
    owner_id=user["admin_id"]
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
def contacts_page(request: Request, q: str = ""):
    user=principal(request)
    with core.database() as db:
        rows = core.active_contact_rows(db,user["admin_id"])
    if q:
        term = q.lower()[:120]
        rows = [x for x in rows if any(term in str(x[k]).lower() for k in ("name","username","phone","user_id","notes"))]
    return render(request, "contacts.html", title="Contacts", contacts=rows, query=q, edit=None)


@app.post("/contacts")
def add_contact(
    request: Request, csrf: str = Form(""), name: str = Form(""),
    username: str = Form(""), phone: str = Form(""), user_id: str = Form(""),
    notes: str = Form(""), consent: str = Form(""), enabled: str = Form(""),
):
    user=authorize(request, csrf)
    try:
        item = core.normalize_contact(name, username, phone, user_id, notes)
        with core.database() as db:
            core.insert_contact(db, item, user["admin_id"], consent=consent=="yes", enabled=enabled=="yes")
            db.commit()
    except ValueError as exc:
        return redirect("/contacts", str(exc))
    return redirect("/contacts", "Contact added to your private database.")


@app.get("/contacts/{contact_id}/edit")
def edit_contact_page(request: Request, contact_id: int):
    user=principal(request)
    with core.database() as db:
        record = db.execute("SELECT * FROM contacts WHERE id=? AND owner_id=? AND archived=0", (contact_id,user["admin_id"])).fetchone()
    if record is None:
        raise HTTPException(404, "Contact not found")
    return render(request, "contact_edit.html", title="Edit contact", contact=record)


@app.post("/contacts/{contact_id}/edit")
def edit_contact_action(
    request: Request, contact_id: int, csrf: str = Form(""), name: str = Form(""),
    username: str = Form(""), phone: str = Form(""), user_id: str = Form(""),
    notes: str = Form(""), consent: str = Form(""), enabled: str = Form(""),
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
async def import_contacts(request: Request, csrf: str = Form(""), file: UploadFile = File(...)):
    user=authorize(request, csrf)
    raw = await file.read(1024*1024 + 1)
    if len(raw) > 1024*1024:
        return redirect("/contacts", "CSV file too large (1 MB maximum).")
    try:
        contents = raw.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(contents))
        if not reader.fieldnames or not {"Name","name"}.intersection(reader.fieldnames):
            raise ValueError("CSV requires a Name column and a Telegram ID, Username or Phone column")
        good = skipped = 0
        with core.database() as db:
            for index, row in enumerate(reader):
                if index >= 1000:
                    break
                raw_identifier = (row.get("ID") or "").strip()
                username = row.get("Username") or row.get("username") or (raw_identifier if raw_identifier.startswith("@") else "")
                phone = row.get("Phone") or row.get("phone") or (raw_identifier if raw_identifier.startswith("+") else "")
                numeric = row.get("User ID") or (raw_identifier if raw_identifier.isdigit() else "")
                try:
                    contact = core.normalize_contact(
                        row.get("Name") or row.get("name") or "", username, phone, numeric, row.get("Notes") or "",
                    )
                    yes = lambda v: str(v or "").lower().strip() in {"yes","true","1","y"}
                    core.insert_contact(
                        db,contact,user["admin_id"],
                        consent=yes(row.get("Opt In") or row.get("Consent")),
                        enabled=not yes(row.get("Do Not Contact")),
                    )
                    good += 1
                except ValueError:
                    skipped += 1
            db.commit()
    except (UnicodeError, ValueError, csv.Error) as exc:
        return redirect("/contacts", f"Import rejected: {str(exc)[:150]}")
    return redirect("/contacts", f"Imported {good} contacts; skipped {skipped}. Only opted-in contacts can receive messages.")


@app.get("/contacts/export")
def export_contacts(request: Request):
    user=principal(request)
    with core.database() as db:
        rows = core.active_contact_rows(db,user["admin_id"])
    target = io.StringIO()
    writer = csv.writer(target)
    writer.writerow(["Name","Username","Phone","User ID","Notes","Opt In","Enabled"])
    for r in rows:
        writer.writerow([r["name"],r["username"],r["phone"],r["user_id"],r["notes"],
                         "yes" if r["consent"] else "no","yes" if r["enabled"] else "no"])
    return StreamingResponse(
        iter([target.getvalue()]),media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=telegram-contacts.csv","Cache-Control":"no-store"},
    )


@app.get("/accounts")
def accounts_page(request: Request):
    user=principal(request)
    with core.database() as db:
        accounts = db.execute("SELECT id,label,username,state,created_at FROM accounts WHERE owner_id=? ORDER BY created_at DESC",(user["admin_id"],)).fetchall()
    return render(request, "accounts.html", title="Telegram accounts", accounts=accounts)


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
def account_setup(request: Request, account_id: str):
    user=principal(request)
    return render(request,"account_setup.html",title="Verify Telegram account",account=account_detail(account_id,user["admin_id"]))


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


@app.get("/compose")
def compose(request: Request):
    user=principal(request)
    with core.database() as db:
        accounts = db.execute("SELECT id,label,username FROM accounts WHERE owner_id=? AND state='connected' ORDER BY label",(user["admin_id"],)).fetchall()
        contacts = db.execute("SELECT * FROM contacts WHERE owner_id=? AND archived=0 AND enabled=1 AND consent=1 ORDER BY name COLLATE NOCASE",(user["admin_id"],)).fetchall()
    return render(request,"compose.html",title="New broadcast",accounts=accounts,contacts=contacts)


@app.post("/compose/preview")
async def prepare_broadcast(request: Request):
    # Authenticate before parsing a potentially large list of selected contacts.
    user=principal(request)
    form=await request.form(max_fields=20_000)
    authorize(request,str(form.get("csrf","")))
    account_id=str(form.get("account_id",""))
    message=str(form.get("message","")).strip()
    supplied=form.getlist("contacts")
    try:
        ids=[int(str(x)) for x in supplied]
        limit=core.recipient_limit()
        with core.database() as db:
            cid=core.create_campaign(db,account_id,message,ids,limit,user["admin_id"])
            db.commit()
    except (TypeError,ValueError) as exc:
        return redirect("/compose",str(exc))
    return redirect(f"/campaigns/{cid}", "Review the exact message and recipients before sending.")


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
    if campaign is None:raise HTTPException(404,"Campaign not found")
    counts={}
    for d in deliveries:counts[d["state"]]=counts.get(d["state"],0)+1
    return render(request,"campaign_detail.html",title="Broadcast details",
                  campaign=campaign,deliveries=deliveries,counts=counts,
                  retryable=retryable,retry_id=retry_row["retry_campaign_id"] if retry_row else None)


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