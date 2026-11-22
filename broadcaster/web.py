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
    lambda x: datetime.fromtimestamp(x, tz=timezone.utc).strftime("%d %b %Y · %H:%M UTC") if x else "—"
)


@app.on_event("startup")
async def startup():
    core.encryption()  # Fail closed if encryption is not configured.
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


def render(request: Request, name: str, status_code: int = 200, **data):
    person = None
    with core.database() as db:
        person = core.session(db, request.cookies.get("tb_session"))
    base = {
        "user": person, "csrf": person["csrf"] if person else "",
        "notice": request.query_params.get("notice", ""),
        "send_enabled": os.getenv("SEND_ENABLED", "false").lower() == "true",
        "send_limit": max(1, min(100, int(os.getenv("SEND_MAX_RECIPIENTS", "3")))),
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
        row = db.execute("SELECT * FROM admins WHERE username=?", (name,)).fetchone()
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
    principal(request)
    with core.database() as db:
        stats = {
            "contacts": db.execute("SELECT COUNT(*) c FROM contacts WHERE archived=0").fetchone()["c"],
            "eligible": db.execute("SELECT COUNT(*) c FROM contacts WHERE archived=0 AND enabled=1 AND consent=1").fetchone()["c"],
            "accounts": db.execute("SELECT COUNT(*) c FROM accounts WHERE state='connected'").fetchone()["c"],
            "broadcasts": db.execute("SELECT COUNT(*) c FROM campaigns").fetchone()["c"],
        }
        accounts = db.execute("SELECT id,label,username,state FROM accounts ORDER BY created_at DESC").fetchall()
        recent = db.execute("""
            SELECT c.id,c.state,c.created_at,c.total,a.label
            FROM campaigns c JOIN accounts a ON c.account_id=a.id
            ORDER BY c.created_at DESC LIMIT 8
        """).fetchall()
    return render(request, "dashboard.html", title="Overview", stats=stats, accounts=accounts, recent=recent)


@app.get("/contacts")
def contacts_page(request: Request, q: str = ""):
    principal(request)
    with core.database() as db:
        rows = core.active_contact_rows(db)
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
    authorize(request, csrf)
    try:
        item = core.normalize_contact(name, username, phone, user_id, notes)
        with core.database() as db:
            core.insert_contact(db, item, consent=consent=="yes", enabled=enabled=="yes")
            db.commit()
    except ValueError as exc:
        return redirect("/contacts", str(exc))
    return redirect("/contacts", "Contact added to your private database.")


@app.get("/contacts/{contact_id}/edit")
def edit_contact_page(request: Request, contact_id: int):
    principal(request)
    with core.database() as db:
        record = db.execute("SELECT * FROM contacts WHERE id=? AND archived=0", (contact_id,)).fetchone()
    if record is None:
        raise HTTPException(404, "Contact not found")
    return render(request, "contact_edit.html", title="Edit contact", contact=record)


@app.post("/contacts/{contact_id}/edit")
def edit_contact_action(
    request: Request, contact_id: int, csrf: str = Form(""), name: str = Form(""),
    username: str = Form(""), phone: str = Form(""), user_id: str = Form(""),
    notes: str = Form(""), consent: str = Form(""), enabled: str = Form(""),
):
    authorize(request, csrf)
    try:
        item = core.normalize_contact(name, username, phone, user_id, notes)
        with core.database() as db:
            existing = db.execute("SELECT id FROM contacts WHERE id=? AND archived=0", (contact_id,)).fetchone()
            if not existing:
                raise HTTPException(404, "Contact not found")
            target = item["username"] or item["phone"] or item["user_id"]
            duplicate = db.execute("""
                SELECT id FROM contacts WHERE archived=0 AND id<>? AND
                ((?<>'' AND username=?) OR (?<>'' AND phone=?) OR (?<>'' AND user_id=?))
            """, (contact_id,item["username"],item["username"],item["phone"],item["phone"],item["user_id"],item["user_id"])).fetchone()
            if duplicate:
                raise ValueError("This Telegram identifier belongs to another contact")
            db.execute("""
                UPDATE contacts SET name=?,username=?,phone=?,user_id=?,notes=?,enabled=?,consent=?,updated_at=?
                WHERE id=?
            """, (item["name"],item["username"],item["phone"],item["user_id"],item["notes"],int(enabled=="yes"),int(consent=="yes"),core.now(),contact_id))
            db.commit()
    except ValueError as exc:
        return redirect(f"/contacts/{contact_id}/edit", str(exc))
    return redirect("/contacts", "Contact updated.")


@app.post("/contacts/{contact_id}/remove")
def remove_contact(request: Request, contact_id: int, csrf: str = Form("")):
    authorize(request, csrf)
    with core.database() as db:
        db.execute("UPDATE contacts SET archived=1,enabled=0,updated_at=? WHERE id=?", (core.now(),contact_id))
        db.commit()
    return redirect("/contacts", "Contact removed from active recipients. Existing delivery history was preserved.")


@app.post("/contacts/import")
async def import_contacts(request: Request, csrf: str = Form(""), file: UploadFile = File(...)):
    authorize(request, csrf)
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
                        db,contact,
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
    principal(request)
    with core.database() as db:
        rows = core.active_contact_rows(db)
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
    principal(request)
    with core.database() as db:
        accounts = db.execute("SELECT id,label,username,state,created_at FROM accounts ORDER BY created_at DESC").fetchall()
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
    authorize(request, csrf)
    label, phone, expected_username = label.strip(), phone.strip(), expected_username.strip().lower()
    if not (1 <= len(label) <= 60) or not re.fullmatch(r"[0-9]{5,15}", api_id.strip()) or not re.fullmatch(r"[a-fA-F0-9]{32}",api_hash.strip()) or not re.fullmatch(r"\+[1-9][0-9]{6,14}",phone):
        return redirect("/accounts/new", "Check the label, API ID, API Hash and international phone number.")
    if expected_username:
        if not expected_username.startswith("@"):expected_username="@"+expected_username
        if not re.fullmatch(r"@[a-z0-9_]{5,32}", expected_username):
            return redirect("/accounts/new", "Invalid expected Telegram username.")
    uid=uuid.uuid4().hex
    with core.database() as db:
        db.execute("""INSERT INTO accounts(id,label,api_id,api_hash_enc,phone_enc,expected_username,session_name,created_at,updated_at)
                      VALUES(?,?,?,?,?,?,?,?,?)""",
                   (uid,label,int(api_id),core.encrypt(api_hash.strip()),core.encrypt(phone),expected_username,uid,core.now(),core.now()))
        db.commit()
    return redirect(f"/accounts/{uid}/setup", "Account stored securely. Request a Telegram login code to continue.")


def account_detail(account_id: str):
    if not re.fullmatch(r"[a-f0-9]{32}",account_id):raise HTTPException(404,"Account not found")
    with core.database() as db:
        a=db.execute("SELECT id,label,expected_username,username,state,last_error,created_at FROM accounts WHERE id=?", (account_id,)).fetchone()
    if not a:raise HTTPException(404,"Account not found")
    return a


@app.get("/accounts/{account_id}/setup")
def account_setup(request: Request, account_id: str):
    principal(request)
    return render(request,"account_setup.html",title="Verify Telegram account",account=account_detail(account_id))


@app.post("/accounts/{account_id}/request-code")
async def account_request_code(request: Request, account_id: str, csrf: str = Form("")):
    authorize(request,csrf)
    account_detail(account_id)
    try:await telegram.request_code(account_id)
    except telegram.TelegramProblem as exc:return redirect(f"/accounts/{account_id}/setup",str(exc))
    return redirect(f"/accounts/{account_id}/setup", "Code requested. Check your Telegram app for the verification message.")


@app.post("/accounts/{account_id}/verify-code")
async def account_verify_code(request: Request, account_id: str, csrf: str = Form(""), code: str = Form("")):
    authorize(request,csrf)
    account_detail(account_id)
    try:
        success=await telegram.verify_code(account_id,code)
    except telegram.TelegramProblem as exc:return redirect(f"/accounts/{account_id}/setup",str(exc))
    return redirect(f"/accounts/{account_id}/setup",
                    "Telegram account connected." if success else "Two-step verification enabled. Enter your Telegram password below.")


@app.post("/accounts/{account_id}/verify-password")
async def account_verify_password(request: Request, account_id: str, csrf: str = Form(""), password: str = Form("")):
    authorize(request,csrf)
    account_detail(account_id)
    try:await telegram.verify_password(account_id,password)
    except telegram.TelegramProblem as exc:return redirect(f"/accounts/{account_id}/setup",str(exc))
    return redirect(f"/accounts/{account_id}/setup","Telegram account connected.")


@app.post("/accounts/{account_id}/verify")
async def account_verify(request: Request, account_id: str, csrf: str = Form("")):
    authorize(request,csrf)
    account_detail(account_id)
    try:name=await telegram.verify_connected(account_id)
    except telegram.TelegramProblem as exc:return redirect(f"/accounts/{account_id}/setup",str(exc))
    return redirect(f"/accounts/{account_id}/setup",f"Verified: {name}. No messages sent.")


@app.get("/compose")
def compose(request: Request):
    principal(request)
    with core.database() as db:
        accounts = db.execute("SELECT id,label,username FROM accounts WHERE state='connected' ORDER BY label").fetchall()
        contacts = db.execute("SELECT * FROM contacts WHERE archived=0 AND enabled=1 AND consent=1 ORDER BY name COLLATE NOCASE").fetchall()
    return render(request,"compose.html",title="New broadcast",accounts=accounts,contacts=contacts)


@app.post("/compose/preview")
async def prepare_broadcast(request: Request):
    form=await request.form()
    authorize(request,str(form.get("csrf","")))
    account_id=str(form.get("account_id",""))
    message=str(form.get("message","")).strip()
    supplied=form.getlist("contacts")
    try:
        ids=[int(str(x)) for x in supplied]
        limit=max(1,min(100,int(os.getenv("SEND_MAX_RECIPIENTS","3"))))
        with core.database() as db:
            cid=core.create_campaign(db,account_id,message,ids,limit)
            db.commit()
    except (TypeError,ValueError) as exc:
        return redirect("/compose",str(exc))
    return redirect(f"/campaigns/{cid}", "Review the exact message and recipients before sending.")


@app.get("/campaigns")
def campaigns_page(request: Request):
    principal(request)
    with core.database() as db:
        rows=db.execute("""
            SELECT c.id,c.state,c.created_at,c.total,a.label,
            (SELECT COUNT(*) FROM deliveries d WHERE d.campaign_id=c.id AND d.state='sent') sent
            FROM campaigns c JOIN accounts a ON a.id=c.account_id ORDER BY c.created_at DESC LIMIT 100
        """).fetchall()
    return render(request,"campaigns.html",title="Broadcast history",campaigns=rows)


@app.get("/campaigns/{campaign_id}")
def campaign_page(request: Request, campaign_id: str):
    principal(request)
    with core.database() as db:
        campaign, deliveries=core.campaign_summary(db,campaign_id)
    if campaign is None:raise HTTPException(404,"Campaign not found")
    counts={}
    for d in deliveries:counts[d["state"]]=counts.get(d["state"],0)+1
    return render(request,"campaign_detail.html",title="Broadcast details",campaign=campaign,deliveries=deliveries,counts=counts)


@app.post("/campaigns/{campaign_id}/send")
async def send_campaign(request: Request, campaign_id: str, csrf: str = Form("")):
    authorize(request,csrf)
    if os.getenv("SEND_ENABLED","false").lower() != "true":
        return redirect(f"/campaigns/{campaign_id}","Delivery is disabled in server settings. Preview is safe.")
    with core.database() as db:
        campaign, deliveries=core.campaign_summary(db,campaign_id)
        if not campaign:raise HTTPException(404,"Campaign not found")
        limit=max(1,min(100,int(os.getenv("SEND_MAX_RECIPIENTS","3"))))
        if len(deliveries)>limit:return redirect(f"/campaigns/{campaign_id}","Campaign exceeds the configured safety limit.")
        count=db.execute("UPDATE campaigns SET state='queued' WHERE id=? AND state='draft'",(campaign_id,)).rowcount
        db.commit()
    if count != 1:
        return redirect(f"/campaigns/{campaign_id}","This campaign was already submitted. No duplicate delivery was started.")
    telegram.enqueue(campaign_id)
    return redirect(f"/campaigns/{campaign_id}","Sending started. Check this page for per-recipient results.")


@app.get("/api/campaigns/{campaign_id}")
def campaign_status(request: Request, campaign_id: str):
    principal(request)
    with core.database() as db:
        campaign, deliveries=core.campaign_summary(db,campaign_id)
    if campaign is None:raise HTTPException(404,"Campaign not found")
    return {"id":campaign_id,"state":campaign["state"],"total":len(deliveries),
            "sent":sum(d["state"]=="sent" for d in deliveries),
            "failed":sum(d["state"] in {"failed","blocked"} for d in deliveries)}