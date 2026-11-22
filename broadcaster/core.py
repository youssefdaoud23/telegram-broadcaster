"""SQLite persistence and security primitives. No Telegram secrets in Git."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import time
from pathlib import Path

from cryptography.fernet import Fernet


def now() -> int:
    return int(time.time())


def db_path() -> Path:
    return Path(os.getenv("DATABASE_PATH", "/data/broadcaster.sqlite3"))


def database() -> sqlite3.Connection:
    location = db_path()
    location.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    db = sqlite3.connect(str(location), timeout=20)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA busy_timeout=10000")
    return db


SCHEMA = """
CREATE TABLE IF NOT EXISTS admins (
 id INTEGER PRIMARY KEY,
 username TEXT NOT NULL UNIQUE COLLATE NOCASE,
 password_salt TEXT NOT NULL,
 password_digest TEXT NOT NULL,
 created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
 token_hash TEXT PRIMARY KEY,
 admin_id INTEGER NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
 csrf TEXT NOT NULL,
 expires_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
 username TEXT PRIMARY KEY,
 attempts INTEGER NOT NULL,
 locked_until INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS accounts (
 id TEXT PRIMARY KEY,
 label TEXT NOT NULL,
 api_id INTEGER NOT NULL,
 api_hash_enc TEXT NOT NULL,
 phone_enc TEXT NOT NULL,
 expected_username TEXT NOT NULL DEFAULT '',
 username TEXT NOT NULL DEFAULT '',
 session_name TEXT NOT NULL UNIQUE,
 state TEXT NOT NULL DEFAULT 'pending',
 code_hash_enc TEXT,
 last_error TEXT NOT NULL DEFAULT '',
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS contacts (
 id INTEGER PRIMARY KEY,
 name TEXT NOT NULL,
 username TEXT NOT NULL DEFAULT '',
 phone TEXT NOT NULL DEFAULT '',
 user_id TEXT NOT NULL DEFAULT '',
 notes TEXT NOT NULL DEFAULT '',
 enabled INTEGER NOT NULL DEFAULT 1,
 consent INTEGER NOT NULL DEFAULT 0,
 archived INTEGER NOT NULL DEFAULT 0,
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_contacts_archived ON contacts(archived,enabled,consent);
CREATE TABLE IF NOT EXISTS campaigns (
 id TEXT PRIMARY KEY,
 account_id TEXT NOT NULL REFERENCES accounts(id),
 message TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'draft',
 created_at INTEGER NOT NULL,
 started_at INTEGER,
 completed_at INTEGER,
 total INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS deliveries (
 id INTEGER PRIMARY KEY,
 campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 contact_id INTEGER,
 name TEXT NOT NULL,
 destination TEXT NOT NULL,
 state TEXT NOT NULL DEFAULT 'pending',
 message_id INTEGER,
 error TEXT NOT NULL DEFAULT '',
 updated_at INTEGER NOT NULL,
 UNIQUE(campaign_id,contact_id)
);
CREATE INDEX IF NOT EXISTS idx_deliveries_campaign ON deliveries(campaign_id);
"""


def init_db() -> None:
    with database() as db:
        db.executescript(SCHEMA)
        # Never retry a possibly partially sent broadcast after a process restart.
        db.execute("UPDATE campaigns SET state='interrupted', completed_at=? WHERE state IN ('sending','queued')", (now(),))
        db.execute("""
            UPDATE deliveries SET state='unknown', error='Interrupted before delivery status was confirmed', updated_at=?
            WHERE state='sending'
        """, (now(),))
        db.commit()
    try:
        os.chmod(db_path(), 0o600)
    except OSError:
        pass


def encryption() -> Fernet:
    value = os.getenv("APP_ENCRYPTION_KEY", "").strip()
    if not value:
        raise RuntimeError("APP_ENCRYPTION_KEY is required; configure it in the protected .env")
    try:
        return Fernet(value.encode("ascii"))
    except Exception as exc:
        raise RuntimeError("APP_ENCRYPTION_KEY must be a valid Fernet key") from exc


def encrypt(value: str) -> str:
    return encryption().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt(value: str) -> str:
    return encryption().decrypt(value.encode("ascii")).decode("utf-8")


def password_digest(password: str, salt: bytes) -> str:
    return hashlib.scrypt(password.encode("utf8"), salt=salt, n=2**15, r=8, p=1, maxmem=64*1024*1024).hex()


def create_admin(username: str, password: str) -> None:
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,39}", username):
        raise ValueError("Administrator username must be 3–40 characters")
    if len(password) < 14 or len(password) > 256:
        raise ValueError("Administrator password must be between 14 and 256 characters")
    init_db()
    salt = secrets.token_bytes(16)
    with database() as db:
        db.execute(
            "INSERT INTO admins(username,password_salt,password_digest,created_at) VALUES (?,?,?,?)",
            (username, salt.hex(), password_digest(password, salt), now()),
        )
        db.commit()


def validate_password(row: sqlite3.Row | None, password: str) -> bool:
    if row is None:
        # Spend the same work factor for nonexistent accounts.
        password_digest(password, bytes(16))
        return False
    actual = password_digest(password, bytes.fromhex(row["password_salt"]))
    return hmac.compare_digest(actual, row["password_digest"])


def login_locked(db: sqlite3.Connection, username: str) -> bool:
    row = db.execute("SELECT attempts,locked_until FROM login_attempts WHERE username=?", (username.lower(),)).fetchone()
    return bool(row and row["attempts"] >= 5 and row["locked_until"] > now())


def failed_login(db: sqlite3.Connection, username: str) -> None:
    username = username.lower()
    old = db.execute("SELECT attempts,locked_until FROM login_attempts WHERE username=?", (username,)).fetchone()
    if old and old["locked_until"] > now():
        count = old["attempts"] + 1
    else:
        count = 1
    db.execute(
        "INSERT INTO login_attempts(username,attempts,locked_until) VALUES (?,?,?) "
        "ON CONFLICT(username) DO UPDATE SET attempts=excluded.attempts,locked_until=excluded.locked_until",
        (username, count, now() + 900),
    )


def new_session(db: sqlite3.Connection, admin_id: int) -> tuple[str,str]:
    token = secrets.token_urlsafe(40)
    csrf = secrets.token_urlsafe(32)
    db.execute(
        "INSERT INTO sessions(token_hash,admin_id,csrf,expires_at) VALUES (?,?,?,?)",
        (hashlib.sha256(token.encode()).hexdigest(), admin_id, csrf,
         now() + max(1, min(30, int(os.getenv("SESSION_DAYS", "7")))) * 86400),
    )
    return token, csrf


def session(db: sqlite3.Connection, raw: str | None) -> sqlite3.Row | None:
    if not raw or len(raw) > 200:
        return None
    digest = hashlib.sha256(raw.encode()).hexdigest()
    return db.execute("""
        SELECT s.token_hash,s.admin_id,s.csrf,s.expires_at,a.username
        FROM sessions s JOIN admins a ON a.id=s.admin_id
        WHERE s.token_hash=? AND s.expires_at>?
    """, (digest, now())).fetchone()


def revoke_session(db: sqlite3.Connection, raw: str | None) -> None:
    if raw:
        db.execute("DELETE FROM sessions WHERE token_hash=?", (hashlib.sha256(raw.encode()).hexdigest(),))


def check_csrf(expected: str, supplied: str) -> bool:
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def normalize_contact(name: str, username: str, phone: str, user_id: str, notes: str) -> dict[str,str]:
    name, username, phone, user_id, notes = (
        str(v).strip() for v in [name,username,phone,user_id,notes]
    )
    if not 1 <= len(name) <= 120:
        raise ValueError("Name must be between 1 and 120 characters")
    if username and not username.startswith("@"):
        username = "@" + username
    if username and not re.fullmatch(r"@[A-Za-z0-9_]{5,32}", username):
        raise ValueError("Use a valid Telegram username (for example @example123)")
    username = username.lower()
    if phone and not re.fullmatch(r"\+[1-9][0-9]{6,14}", phone):
        raise ValueError("Use a full international phone number, e.g. +420123456789")
    if user_id and not re.fullmatch(r"[1-9][0-9]{5,19}", user_id):
        raise ValueError("Telegram numeric user ID must be 6–20 digits")
    if not (username or phone or user_id):
        raise ValueError("Provide at least a Telegram username, phone number, or user ID")
    if len(notes) > 1000:
        raise ValueError("Notes cannot exceed 1000 characters")
    return dict(name=name, username=username, phone=phone, user_id=user_id, notes=notes)


def destination(row: sqlite3.Row | dict) -> str:
    return row["username"] or row["phone"] or row["user_id"]


def active_contact_rows(db: sqlite3.Connection) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM contacts WHERE archived=0 ORDER BY name COLLATE NOCASE,id"
    ).fetchall()


def insert_contact(db: sqlite3.Connection, data: dict[str,str], *, consent: bool=False, enabled: bool=True) -> int:
    dest = data["username"] or data["phone"] or data["user_id"]
    # De-duplicate by any supplied identifier, case insensitive for usernames.
    dup = db.execute("""
        SELECT id FROM contacts WHERE archived=0 AND
        ((?<>'' AND username=?) OR (?<>'' AND phone=?) OR (?<>'' AND user_id=?))
    """, (data["username"],data["username"],data["phone"],data["phone"],data["user_id"],data["user_id"])).fetchone()
    if dup:
        raise ValueError("This Telegram identifier is already present in your contacts")
    cursor = db.execute("""
        INSERT INTO contacts(name,username,phone,user_id,notes,enabled,consent,created_at,updated_at)
        VALUES (?,?,?,?,?,?,?,?,?)
    """, (data["name"],data["username"],data["phone"],data["user_id"],data["notes"],
          int(enabled), int(consent), now(), now()))
    return int(cursor.lastrowid)


def create_campaign(db: sqlite3.Connection, account_id: str, message: str, contact_ids: list[int], limit: int) -> str:
    if len(message.strip()) < 1 or len(message) > 4096:
        raise ValueError("Message must contain 1–4096 characters")
    if not 1 <= len(contact_ids) <= limit:
        raise ValueError(f"Select between 1 and {limit} opted-in recipients")
    if len(contact_ids) != len(set(contact_ids)):
        raise ValueError("Recipients cannot be duplicated")
    account = db.execute("SELECT * FROM accounts WHERE id=? AND state='connected'", (account_id,)).fetchone()
    if not account:
        raise ValueError("Please connect a Telegram sender first")
    eligible=[]
    for cid in contact_ids:
        c=db.execute("SELECT * FROM contacts WHERE id=? AND archived=0 AND enabled=1 AND consent=1", (cid,)).fetchone()
        if not c:
            raise ValueError("A selected contact is disabled or has not opted in")
        eligible.append(c)
    campaign_id=secrets.token_urlsafe(18)
    db.execute(
        "INSERT INTO campaigns(id,account_id,message,state,created_at,total) VALUES (?,?,?,'draft',?,?)",
        (campaign_id, account_id, message, now(), len(eligible)),
    )
    for c in eligible:
        db.execute("""INSERT INTO deliveries(campaign_id,contact_id,name,destination,state,updated_at)
                      VALUES (?,?,?,?, 'pending',?)""",
                   (campaign_id,c["id"],c["name"],destination(c),now()))
    return campaign_id


def campaign_summary(db: sqlite3.Connection, campaign_id: str) -> tuple[sqlite3.Row | None,list[sqlite3.Row]]:
    c=db.execute("""
        SELECT c.*,a.label AS account_label,a.username AS account_username
        FROM campaigns c JOIN accounts a ON a.id=c.account_id WHERE c.id=?
    """, (campaign_id,)).fetchone()
    d=db.execute("SELECT * FROM deliveries WHERE campaign_id=? ORDER BY id", (campaign_id,)).fetchall()
    return c,d
