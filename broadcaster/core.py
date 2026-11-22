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


def recipient_limit() -> int | None:
    """Optional operator limit; missing, blank, 0, or 'unlimited' means no cap.

    Telegram's own anti-spam restrictions still apply. This controls how many
    opted-in recipients one reviewed campaign may contain, not contact storage.
    """
    raw = os.getenv("SEND_MAX_RECIPIENTS", "").strip()
    if not raw or raw.lower() in {"0", "none", "unlimited"}:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError("SEND_MAX_RECIPIENTS must be a positive integer, 0, or unlimited") from exc
    if value < 1:
        raise ValueError("SEND_MAX_RECIPIENTS must be a positive integer, 0, or unlimited")
    return value


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
CREATE TABLE IF NOT EXISTS contact_groups (
 id INTEGER PRIMARY KEY,
 owner_id INTEGER NOT NULL REFERENCES admins(id),
 name TEXT NOT NULL COLLATE NOCASE,
 created_at INTEGER NOT NULL,
 updated_at INTEGER NOT NULL,
 UNIQUE(owner_id,name)
);
CREATE INDEX IF NOT EXISTS idx_contact_groups_owner ON contact_groups(owner_id);
CREATE TABLE IF NOT EXISTS contact_group_members (
 group_id INTEGER NOT NULL REFERENCES contact_groups(id) ON DELETE CASCADE,
 contact_id INTEGER NOT NULL REFERENCES contacts(id) ON DELETE CASCADE,
 added_at INTEGER NOT NULL,
 PRIMARY KEY(group_id,contact_id)
);
CREATE INDEX IF NOT EXISTS idx_contact_group_members_contact ON contact_group_members(contact_id);
CREATE TRIGGER IF NOT EXISTS trg_group_member_owner_insert
BEFORE INSERT ON contact_group_members
BEGIN
 SELECT CASE WHEN NOT EXISTS (
   SELECT 1 FROM contact_groups g JOIN contacts c
   ON c.owner_id=g.owner_id
   WHERE g.id=NEW.group_id AND c.id=NEW.contact_id
 ) THEN RAISE(ABORT,'Contact and group owners must match') END;
END;
CREATE TRIGGER IF NOT EXISTS trg_group_member_owner_update
BEFORE UPDATE OF group_id,contact_id ON contact_group_members
BEGIN
 SELECT CASE WHEN NOT EXISTS (
   SELECT 1 FROM contact_groups g JOIN contacts c
   ON c.owner_id=g.owner_id
   WHERE g.id=NEW.group_id AND c.id=NEW.contact_id
 ) THEN RAISE(ABORT,'Contact and group owners must match') END;
END;
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
CREATE TABLE IF NOT EXISTS campaign_retries (
 source_campaign_id TEXT PRIMARY KEY REFERENCES campaigns(id),
 retry_campaign_id TEXT NOT NULL UNIQUE REFERENCES campaigns(id),
 created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS campaign_reuses (
 source_campaign_id TEXT NOT NULL REFERENCES campaigns(id),
 new_campaign_id TEXT NOT NULL UNIQUE REFERENCES campaigns(id),
 created_at INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_campaign_reuses_source ON campaign_reuses(source_campaign_id);
"""


def init_db(*, recover_inflight: bool = False) -> None:
    """Initialize and migrate owner-scoped data; only server startup recovers sends.

    Existing Telegram accounts, contacts, and campaigns belong to the oldest
    administrator. The migration is additive and repeatable. A second admin
    never inherits the original owner's records.
    """
    with database() as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        admin_columns = {row["name"] for row in db.execute("PRAGMA table_info(admins)")}
        if "role" not in admin_columns:
            db.execute("ALTER TABLE admins ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        if "active" not in admin_columns:
            db.execute("ALTER TABLE admins ADD COLUMN active INTEGER NOT NULL DEFAULT 1")
        owners = db.execute("SELECT id FROM admins WHERE role='owner' ORDER BY id").fetchall()
        if not owners:
            first = db.execute(
                "SELECT id FROM admins ORDER BY created_at,id LIMIT 1"
            ).fetchone()
            if first:
                db.execute("UPDATE admins SET role='owner' WHERE id=?", (first["id"],))
        owner = db.execute(
            "SELECT id FROM admins WHERE role='owner' ORDER BY id LIMIT 1"
        ).fetchone()
        for table in ("accounts", "contacts", "campaigns"):
            columns = {row["name"] for row in db.execute(f"PRAGMA table_info({table})")}
            legacy_table = "owner_id" not in columns
            if legacy_table:
                db.execute(f"ALTER TABLE {table} ADD COLUMN owner_id INTEGER REFERENCES admins(id)")
                if owner:
                    # Only the original single-user rows are assigned to the owner.
                    db.execute(
                        f"UPDATE {table} SET owner_id=? WHERE owner_id IS NULL",
                        (owner["id"],),
                    )
            # Never silently assign newly ownerless records to the first user
            # on later restarts: that could expose another person's data.
            if db.execute(f"SELECT 1 FROM {table} WHERE owner_id IS NULL LIMIT 1").fetchone():
                raise RuntimeError(f"{table} contains ownerless records; migration stopped safely")
            db.execute(
                f"CREATE INDEX IF NOT EXISTS idx_{table}_owner ON {table}(owner_id)"
            )
        if recover_inflight:
            # On a single-worker service startup, previous delivery tasks are gone.
            # Mark them interrupted to prevent duplicate sends on restart.
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
    """Create first owner or subsequent isolated user (CLI compatibility)."""
    init_db()
    with database() as db:
        create_user(db, username, password, initial_owner=True)
        db.commit()


def create_user(db: sqlite3.Connection, username: str, password: str,
                *, initial_owner: bool = False) -> int:
    username = username.strip()
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_.-]{2,39}", username):
        raise ValueError("Username must be 3–40 characters, starting with a letter")
    if not password:
        raise ValueError("Password cannot be empty")
    has_users = db.execute("SELECT 1 FROM admins LIMIT 1").fetchone() is not None
    if not has_users and not initial_owner:
        raise ValueError("Create the installation owner before adding users")
    role = "owner" if initial_owner and not has_users else "user"
    salt = secrets.token_bytes(16)
    try:
        cursor = db.execute(
            "INSERT INTO admins(username,password_salt,password_digest,created_at,role,active) "
            "VALUES (?,?,?,?,?,1)",
            (username, salt.hex(), password_digest(password, salt), now(), role),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("Username already exists. Sign in or reset the password.") from exc
    return int(cursor.lastrowid)


def change_password(db: sqlite3.Connection, user_id: int, new_password: str,
                    *, current_password: str | None = None) -> None:
    if not new_password:
        raise ValueError("Password cannot be empty")
    user = db.execute("SELECT * FROM admins WHERE id=? AND active=1", (user_id,)).fetchone()
    if not user:
        raise ValueError("User not found or disabled")
    if current_password is not None and not validate_password(user, current_password):
        raise ValueError("Current password is incorrect")
    salt = secrets.token_bytes(16)
    db.execute(
        "UPDATE admins SET password_salt=?,password_digest=? WHERE id=?",
        (salt.hex(), password_digest(new_password, salt), user_id),
    )
    db.execute("DELETE FROM sessions WHERE admin_id=?", (user_id,))
    db.execute("DELETE FROM login_attempts WHERE username=?", (user["username"].lower(),))


def set_user_active(db: sqlite3.Connection, user_id: int, active: bool) -> None:
    user=db.execute("SELECT role FROM admins WHERE id=?", (user_id,)).fetchone()
    if not user:
        raise ValueError("User not found")
    if user["role"]=="owner":
        raise ValueError("Owner account cannot be disabled")
    db.execute("UPDATE admins SET active=? WHERE id=?", (int(active),user_id))
    if not active:
        db.execute("DELETE FROM sessions WHERE admin_id=?", (user_id,))


def reset_admin_password(username: str, password: str) -> None:
    """Reset one existing admin, revoke sessions and clear failed-login lockouts."""
    username = username.strip()
    if not username:
        raise ValueError("Administrator username cannot be empty")
    if not password:
        raise ValueError("Administrator password cannot be empty")
    salt = secrets.token_bytes(16)
    digest = password_digest(password, salt)
    with database() as db:
        row = db.execute("SELECT id FROM admins WHERE username=?", (username,)).fetchone()
        if row is None:
            raise ValueError("Administrator not found. Use create-admin instead.")
        db.execute(
            "UPDATE admins SET password_salt=?, password_digest=? WHERE id=?",
            (salt.hex(), digest, row["id"]),
        )
        db.execute("DELETE FROM sessions WHERE admin_id=?", (row["id"],))
        db.execute("DELETE FROM login_attempts WHERE username=?", (username.lower(),))
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
        SELECT s.token_hash,s.admin_id,s.csrf,s.expires_at,a.username,a.role,a.active
        FROM sessions s JOIN admins a ON a.id=s.admin_id
        WHERE s.token_hash=? AND s.expires_at>? AND a.active=1
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


def active_contact_rows(db: sqlite3.Connection, owner_id: int) -> list[sqlite3.Row]:
    return db.execute(
        "SELECT * FROM contacts WHERE owner_id=? AND archived=0 ORDER BY name COLLATE NOCASE,id",
        (owner_id,),
    ).fetchall()


def validate_group_name(name: str) -> str:
    name = " ".join(str(name).strip().split())
    if not 1 <= len(name) <= 64:
        raise ValueError("Group name must be between 1 and 64 characters")
    if any(c in name for c in (";", "|", "\\", "\0")):
        raise ValueError("Group names cannot contain semicolons or vertical bars")
    return name


def contact_groups(db: sqlite3.Connection, owner_id: int) -> list[sqlite3.Row]:
    return db.execute("""
        SELECT g.id,g.name,g.created_at,
          SUM(CASE WHEN c.archived=0 THEN 1 ELSE 0 END) AS total,
          SUM(CASE WHEN c.archived=0 AND c.enabled=1 AND c.consent=1 THEN 1 ELSE 0 END) AS eligible
        FROM contact_groups g
        LEFT JOIN contact_group_members m ON m.group_id=g.id
        LEFT JOIN contacts c ON c.id=m.contact_id AND c.owner_id=g.owner_id
        WHERE g.owner_id=?
        GROUP BY g.id
        ORDER BY g.name COLLATE NOCASE,g.id
    """, (owner_id,)).fetchall()


def contact_group_map(db: sqlite3.Connection, owner_id: int) -> dict[int,set[int]]:
    result: dict[int,set[int]] = {}
    for row in db.execute("""
        SELECT m.contact_id,m.group_id
        FROM contact_group_members m
        JOIN contact_groups g ON g.id=m.group_id
        JOIN contacts c ON c.id=m.contact_id AND c.owner_id=g.owner_id
        WHERE g.owner_id=?
    """, (owner_id,)):
        result.setdefault(row["contact_id"],set()).add(row["group_id"])
    return result


def add_contact_group(db: sqlite3.Connection, owner_id: int, name: str) -> int:
    name=validate_group_name(name)
    try:
        row=db.execute(
            "INSERT INTO contact_groups(owner_id,name,created_at,updated_at) VALUES (?,?,?,?)",
            (owner_id,name,now(),now()),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("A group with this name already exists in your workspace") from exc
    return int(row.lastrowid)


def update_contact_group(db: sqlite3.Connection, owner_id: int,
                         group_id: int, name: str) -> None:
    name=validate_group_name(name)
    try:
        updated=db.execute(
            "UPDATE contact_groups SET name=?,updated_at=? WHERE id=? AND owner_id=?",
            (name,now(),group_id,owner_id),
        )
    except sqlite3.IntegrityError as exc:
        raise ValueError("A group with this name already exists in your workspace") from exc
    if not updated.rowcount:
        raise ValueError("Group not found in your workspace")


def _allowed_group_ids(db: sqlite3.Connection, owner_id: int,
                       group_ids: list[int]) -> set[int]:
    if len(group_ids)>100:
        raise ValueError("Select no more than 100 groups at once")
    if len(group_ids)!=len(set(group_ids)):
        raise ValueError("Duplicate group selection")
    if any(not isinstance(g,int) or g<=0 for g in group_ids):
        raise ValueError("Invalid group selected")
    if not group_ids:
        return set()
    placeholders=",".join("?" for _ in group_ids)
    allowed={
        row["id"] for row in db.execute(
            f"SELECT id FROM contact_groups WHERE owner_id=? AND id IN ({placeholders})",
            (owner_id,*group_ids),
        )
    }
    if allowed!=set(group_ids):
        raise ValueError("A selected group does not exist in your workspace")
    return allowed


def set_contact_groups(db: sqlite3.Connection, owner_id: int,
                       contact_id: int, group_ids: list[int]) -> None:
    contact=db.execute(
        "SELECT 1 FROM contacts WHERE id=? AND owner_id=? AND archived=0",
        (contact_id,owner_id),
    ).fetchone()
    if contact is None:
        raise ValueError("Contact not found in your workspace")
    chosen=_allowed_group_ids(db,owner_id,group_ids)
    db.execute("DELETE FROM contact_group_members WHERE contact_id=?", (contact_id,))
    for group_id in sorted(chosen):
        db.execute(
            "INSERT INTO contact_group_members(group_id,contact_id,added_at) VALUES (?,?,?)",
            (group_id,contact_id,now()),
        )


def set_group_members(db: sqlite3.Connection, owner_id: int,
                      group_id: int, contact_ids: list[int]) -> None:
    group=db.execute(
        "SELECT 1 FROM contact_groups WHERE id=? AND owner_id=?",
        (group_id,owner_id),
    ).fetchone()
    if not group:
        raise ValueError("Group not found in your workspace")
    if len(contact_ids)!=len(set(contact_ids)) or len(contact_ids)>20000:
        raise ValueError("Invalid or duplicated contact selection")
    if any(not isinstance(cid,int) or cid<=0 for cid in contact_ids):
        raise ValueError("Invalid contact selection")
    if contact_ids:
        unique_ids=set(contact_ids)
        for start in range(0,len(contact_ids),500):
            chunk=contact_ids[start:start+500]
            placeholders=",".join("?" for _ in chunk)
            results=db.execute(
                f"SELECT id FROM contacts WHERE owner_id=? AND archived=0 AND id IN ({placeholders})",
                (owner_id,*chunk),
            ).fetchall()
            for item in results:unique_ids.discard(item["id"])
        if unique_ids:
            raise ValueError("A selected contact does not exist in your workspace")
    db.execute("DELETE FROM contact_group_members WHERE group_id=?", (group_id,))
    for contact_id in sorted(contact_ids):
        db.execute(
            "INSERT INTO contact_group_members(group_id,contact_id,added_at) VALUES (?,?,?)",
            (group_id,contact_id,now()),
        )


def insert_contact(db: sqlite3.Connection, data: dict[str,str], owner_id: int, *, consent: bool=False, enabled: bool=True) -> int:
    dest = data["username"] or data["phone"] or data["user_id"]
    # De-duplicate by any supplied identifier, case insensitive for usernames.
    dup = db.execute("""
        SELECT id FROM contacts WHERE owner_id=? AND archived=0 AND
        ((?<>'' AND username=?) OR (?<>'' AND phone=?) OR (?<>'' AND user_id=?))
    """, (owner_id,data["username"],data["username"],data["phone"],data["phone"],data["user_id"],data["user_id"])).fetchone()
    if dup:
        raise ValueError("This Telegram identifier is already present in your contacts")
    cursor = db.execute("""
        INSERT INTO contacts(name,username,phone,user_id,notes,enabled,consent,created_at,updated_at,owner_id)
        VALUES (?,?,?,?,?,?,?,?,?,?)
    """, (data["name"],data["username"],data["phone"],data["user_id"],data["notes"],
          int(enabled), int(consent), now(), now(), owner_id))
    return int(cursor.lastrowid)


def campaign_targets(db: sqlite3.Connection, account_id: str, message: str,
                     contact_ids: list[int], limit: int | None,
                     owner_id: int) -> list[sqlite3.Row]:
    """Validate an editable broadcast against current sender and contact permissions."""
    if len(message.strip()) < 1 or len(message) > 4096:
        raise ValueError("Message must contain 1-4096 characters")
    if not contact_ids:
        raise ValueError("Select at least one opted-in recipient")
    if limit is not None and len(contact_ids) > limit:
        raise ValueError(f"Select no more than {limit} opted-in recipients")
    if len(contact_ids) != len(set(contact_ids)):
        raise ValueError("Recipients cannot be duplicated")
    sender = db.execute(
        "SELECT id FROM accounts WHERE id=? AND state='connected' AND owner_id=?",
        (account_id, owner_id),
    ).fetchone()
    if not sender:
        raise ValueError("Choose a connected Telegram sender from your own workspace")
    targets=[]
    for cid in contact_ids:
        contact=db.execute(
            "SELECT * FROM contacts WHERE id=? AND owner_id=? "
            "AND archived=0 AND enabled=1 AND consent=1",
            (cid, owner_id),
        ).fetchone()
        if contact is None:
            raise ValueError("A selected contact is disabled, missing, or has not opted in")
        targets.append(contact)
    return targets


def _insert_campaign_deliveries(db: sqlite3.Connection, campaign_id: str,
                                targets: list[sqlite3.Row]) -> None:
    for c in targets:
        db.execute(
            "INSERT INTO deliveries(campaign_id,contact_id,name,destination,state,updated_at) "
            "VALUES (?,?,?,?, 'pending',?)",
            (campaign_id, c["id"], c["name"], destination(c), now()),
        )


def create_campaign(db: sqlite3.Connection, account_id: str, message: str,
                    contact_ids: list[int], limit: int | None, owner_id: int) -> str:
    targets = campaign_targets(db, account_id, message, contact_ids, limit, owner_id)
    campaign_id=secrets.token_urlsafe(18)
    db.execute(
        "INSERT INTO campaigns(id,account_id,message,state,created_at,total,owner_id) "
        "VALUES (?,?,?,'draft',?,?,?)",
        (campaign_id, account_id, message, now(), len(targets), owner_id),
    )
    _insert_campaign_deliveries(db, campaign_id, targets)
    return campaign_id


def update_campaign_draft(db: sqlite3.Connection, campaign_id: str, owner_id: int,
                          account_id: str, message: str, contact_ids: list[int],
                          limit: int | None) -> None:
    """Atomically modify a private draft, never a submitted or historic campaign."""
    db.execute("BEGIN IMMEDIATE")
    draft = db.execute(
        "SELECT state FROM campaigns WHERE id=? AND owner_id=?",
        (campaign_id, owner_id),
    ).fetchone()
    if draft is None:
        raise ValueError("Draft not found")
    if draft["state"] != "draft":
        raise ValueError("This broadcast has already been submitted and is read-only")
    targets = campaign_targets(db, account_id, message, contact_ids, limit, owner_id)
    changed = db.execute(
        "UPDATE campaigns SET account_id=?,message=?,total=? "
        "WHERE id=? AND owner_id=? AND state='draft'",
        (account_id, message, len(targets), campaign_id, owner_id),
    ).rowcount
    if changed != 1:
        raise ValueError("Draft changed while you were editing. Refresh and try again.")
    db.execute("DELETE FROM deliveries WHERE campaign_id=?", (campaign_id,))
    _insert_campaign_deliveries(db, campaign_id, targets)


def campaign_summary(db: sqlite3.Connection, campaign_id: str, owner_id: int) -> tuple[sqlite3.Row | None,list[sqlite3.Row]]:
    c=db.execute("""
        SELECT c.*,a.label AS account_label,a.username AS account_username,
               a.state AS account_state
        FROM campaigns c JOIN accounts a ON a.id=c.account_id AND a.owner_id=c.owner_id
        WHERE c.id=? AND c.owner_id=?
    """, (campaign_id, owner_id)).fetchone()
    d=(db.execute("SELECT * FROM deliveries WHERE campaign_id=? ORDER BY id", (campaign_id,)).fetchall()
       if c is not None else [])
    return c,d

def retryable_count(db: sqlite3.Connection, source_id: str, owner_id: int) -> int:
    """Only never-started, currently opted-in recipients can be retried."""
    row = db.execute("""
        SELECT COUNT(*) AS count
        FROM deliveries d
        JOIN campaigns p ON p.id=d.campaign_id
        JOIN contacts c ON c.id=d.contact_id AND c.owner_id=p.owner_id
        WHERE d.campaign_id=? AND p.owner_id=? AND d.state='pending'
          AND c.archived=0 AND c.enabled=1 AND c.consent=1
    """, (source_id, owner_id)).fetchone()
    return int(row["count"])


def create_retry_draft(db: sqlite3.Connection, source_id: str, limit: int | None, owner_id: int) -> tuple[str, bool]:
    """Create at most one reviewed draft for an interrupted campaign.

    Never retry sent, unknown, sending, failed or blocked results because their
    actual Telegram outcome might be ambiguous. No Telegram API calls here.
    Caller commits. BEGIN IMMEDIATE serializes simultaneous retry requests.
    """
    db.execute("BEGIN IMMEDIATE")
    source = db.execute(
        "SELECT id,account_id,message,state FROM campaigns WHERE id=? AND owner_id=?", (source_id,owner_id)
    ).fetchone()
    if source is None:
        raise ValueError("Original broadcast not found")
    if source["state"] != "interrupted":
        raise ValueError("Only interrupted broadcasts can be retried this way")
    earlier = db.execute(
        "SELECT retry_campaign_id FROM campaign_retries WHERE source_campaign_id=?",
        (source_id,),
    ).fetchone()
    if earlier:
        return earlier["retry_campaign_id"], False

    eligible = db.execute("""
        SELECT d.contact_id FROM deliveries d
        JOIN contacts c ON c.id=d.contact_id AND c.owner_id=?
        WHERE d.campaign_id=? AND d.state='pending'
          AND c.archived=0 AND c.enabled=1 AND c.consent=1
        ORDER BY d.id
    """, (owner_id,source_id)).fetchall()
    ids = [int(d["contact_id"]) for d in eligible]
    if not ids:
        raise ValueError(
            "No safely retryable recipients remain. Only pending, opted-in recipients "
            "can be copied; sent or uncertain results require manual review."
        )
    if limit is not None and len(ids) > limit:
        raise ValueError(
            f"This broadcast has {len(ids)} pending recipients, but the current "
            f"per-broadcast limit is {limit}. Increase SEND_MAX_RECIPIENTS or "
            "create a smaller broadcast manually."
        )
    retry_id = create_campaign(db, source["account_id"], source["message"], ids, limit, owner_id)
    db.execute(
        "INSERT INTO campaign_retries(source_campaign_id,retry_campaign_id,created_at) VALUES (?,?,?)",
        (source_id, retry_id, now()),
    )
    return retry_id, True
