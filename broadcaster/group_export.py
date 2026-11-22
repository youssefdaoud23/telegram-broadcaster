"""On-demand, read-only Telegram group scans and member exports.

Scanning alone never imports contacts: results stay briefly in owner-scoped
memory. A separate CSRF-protected, user-confirmed import action can store
the scanned members as private contacts without changing opt-in or sending.
"""
from __future__ import annotations

import asyncio
import re
import secrets
import time
from dataclasses import dataclass, field

from telethon import types

from . import core, telegram

DIALOG_SCAN_LIMIT = 500
MAX_SCAN_USERS = 25_000
RESULT_TTL_SECONDS = 30 * 60
GLOBAL_MAX_JOBS = 12


@dataclass(frozen=True)
class ExportMember:
    """Only fields visible on Telegram's returned User entity."""

    name: str
    username: str
    phone: str
    user_id: str = ""


def suggested_group_name(title: str) -> str:
    """Keep the Telegram group title when compatible with contact-group names."""
    value = " ".join((title or "").split())
    value = re.sub(r"[;|\\\x00]", "-", value).strip()[:64].strip()
    return value or "Telegram group"


def member_name(member) -> str:
    """Collapse line breaks and controls without inventing unavailable names."""
    parts = (getattr(member, "first_name", None), getattr(member, "last_name", None))
    return " ".join(" ".join(str(part).split()) for part in parts if part).strip()[:160]


def visible_phone(member) -> str:
    """Return only a valid Telegram-provided phone, never guess or look it up."""
    raw = getattr(member, "phone", None)
    if not isinstance(raw, str):
        return ""
    raw = raw.strip()
    if not re.fullmatch(r"\+?[0-9]{5,20}", raw):
        return ""
    return "+" + raw.lstrip("+")


@dataclass
class GroupScan:
    token: str
    owner_id: int
    account_id: str
    peer_id: int
    created_at: float
    state: str = "queued"
    group_title: str = ""
    seen: int = 0
    missing_username: int = 0
    skipped_bots: int = 0
    phone_count: int = 0
    usernames: list[str] = field(default_factory=list)
    members: list[ExportMember] = field(default_factory=list)
    error: str = ""

_scans: dict[str, GroupScan] = {}
_scan_tasks: set[asyncio.Task] = set()


def _cleanup() -> None:
    now = time.monotonic()
    for key, scan in list(_scans.items()):
        if scan.state not in ("queued", "running") and now - scan.created_at > RESULT_TTL_SECONDS:
            del _scans[key]


def get_scan(token: str, owner_id: int) -> GroupScan | None:
    _cleanup()
    scan = _scans.get(token)
    return scan if scan is not None and scan.owner_id == owner_id else None


def _group_entity(entity) -> bool:
    """Regular group or megagroup, not a broadcast channel or a direct chat."""
    if isinstance(entity, types.Chat):
        return not bool(getattr(entity, "deactivated", False))
    return isinstance(entity, types.Channel) and bool(getattr(entity, "megagroup", False))


def _authorized_account(account_id: str, owner_id: int):
    with core.database() as db:
        account = db.execute(
            "SELECT * FROM accounts WHERE id=? AND owner_id=? AND state='connected'",
            (account_id, owner_id),
        ).fetchone()
    if account is None:
        raise telegram.TelegramProblem("Choose your own connected Telegram account.")
    return account


async def _check_identity(client, account) -> None:
    await client.connect()
    if not await client.is_user_authorized():
        telegram._mark_revoked(account["id"])
        raise telegram.TelegramProblem(telegram.SESSION_EXPIRED_MESSAGE)
    me = await client.get_me()
    if me is None:
        telegram._mark_revoked(account["id"])
        raise telegram.TelegramProblem(telegram.SESSION_EXPIRED_MESSAGE)
    username = ("@" + me.username).lower() if me.username else ""
    if (account["expected_username"] and account["expected_username"].lower() != username) or (
        account["username"] and account["username"].lower() != username
    ):
        telegram._mark_revoked(account["id"])
        raise telegram.TelegramProblem("Telegram identity changed. Reconnect this account.")


async def list_groups(account_id: str, owner_id: int) -> list[dict]:
    """List up to the most recent 500 visible chat dialogs as group choices."""
    async with telegram.account_lock(account_id):
        account = _authorized_account(account_id, owner_id)
        client = telegram.new_client(account)
        try:
            await _check_identity(client, account)
            groups = []
            async for dialog in client.iter_dialogs(limit=DIALOG_SCAN_LIMIT):
                if not _group_entity(dialog.entity):
                    continue
                groups.append({
                    "peer_id": int(dialog.id),
                    "name": (dialog.title or "Unnamed group")[:160],
                    "kind": "Supergroup" if isinstance(dialog.entity, types.Channel) else "Group",
                })
            return groups
        except telegram.TelegramProblem:
            raise
        except telegram.REVOKED_ERRORS as exc:
            telegram._mark_revoked(account_id)
            raise telegram.TelegramProblem(telegram.SESSION_EXPIRED_MESSAGE) from exc
        except Exception as exc:
            raise telegram.telegram_error(exc) from exc
        finally:
            await client.disconnect()


def start_scan(owner_id: int, account_id: str, peer_id: int) -> GroupScan:
    """Starts a private background read only after the user submits the scan form."""
    _cleanup()
    _authorized_account(account_id, owner_id)
    if not isinstance(peer_id, int) or peer_id >= 0:
        raise ValueError("Select a Telegram group from the list.")
    if any(s.owner_id == owner_id and s.state in ("queued", "running") for s in _scans.values()):
        raise ValueError("You already have a scan running. Finish it before starting another.")
    if len(_scans) >= GLOBAL_MAX_JOBS:
        raise ValueError("The server has too many recent scans. Try again after earlier exports expire.")
    with core.database() as db:
        busy = db.execute(
            "SELECT 1 FROM campaigns WHERE account_id=? AND owner_id=? "
            "AND state IN ('queued','sending') LIMIT 1", (account_id, owner_id),
        ).fetchone()
    if busy:
        raise ValueError("This sender has an active broadcast. Finish it before scanning a group.")
    scan = GroupScan(
        token=secrets.token_urlsafe(24), owner_id=owner_id,
        account_id=account_id, peer_id=peer_id, created_at=time.monotonic(),
    )
    _scans[scan.token] = scan
    task = asyncio.create_task(_run_scan(scan))
    _scan_tasks.add(task)
    task.add_done_callback(_scan_tasks.discard)
    return scan


async def _run_scan(scan: GroupScan) -> None:
    scan.state = "running"
    try:
        await asyncio.wait_for(_collect(scan), timeout=240)
        if scan.state == "running":
            scan.state = "complete"
    except asyncio.CancelledError:
        scan.state = "partial"
        scan.error = "The scan was interrupted. Try a new scan."
        raise
    except Exception as exc:
        if isinstance(exc, telegram.REVOKED_ERRORS):
            telegram._mark_revoked(scan.account_id)
            error = telegram.SESSION_EXPIRED_MESSAGE
        elif isinstance(exc, telegram.TelegramProblem):
            error = str(exc)
        else:
            error = str(telegram.telegram_error(exc))
        scan.error = error[:250]
        scan.state = "partial" if scan.seen else "failed"


async def _collect(scan: GroupScan) -> None:
    async with telegram.account_lock(scan.account_id):
        account = _authorized_account(scan.account_id, scan.owner_id)
        client = telegram.new_client(account)
        try:
            await _check_identity(client, account)
            # Re-enumerate account-owned dialogs on every scan. Never use
            # arbitrary user-supplied Telegram entities or group URLs.
            chosen = None
            async for dialog in client.iter_dialogs(limit=DIALOG_SCAN_LIMIT):
                if int(dialog.id) == scan.peer_id and _group_entity(dialog.entity):
                    chosen = dialog
                    break
            if chosen is None:
                raise telegram.TelegramProblem(
                    "Group not found among your accessible chats. Refresh the group list."
                )
            scan.group_title = (chosen.title or "Unnamed group")[:160]
            seen_usernames: set[str] = set()
            seen_members: set[tuple[str, int | str]] = set()
            # Explicit cap to protect one server worker and in-memory result
            # storage. This may be lower than Telegram's accessible membership.
            async for member in client.iter_participants(chosen.entity, limit=MAX_SCAN_USERS + 1):
                if scan.seen >= MAX_SCAN_USERS:
                    scan.state = "limited"
                    scan.error = f"Scan reached the {MAX_SCAN_USERS:,}-member safety limit. Results are incomplete."
                    break
                scan.seen += 1
                if getattr(member, "deleted", False) or getattr(member, "bot", False):
                    scan.skipped_bots += 1
                    continue
                username = getattr(member, "username", None) or ""
                if not re.fullmatch(r"[A-Za-z0-9_]{3,32}", username):
                    username = ""
                    scan.missing_username += 1
                display_name = member_name(member)
                phone = visible_phone(member)
                user_id = getattr(member, "id", None)
                numeric_id = (
                    str(user_id)
                    if isinstance(user_id, int) and not isinstance(user_id, bool)
                    and 0 < user_id < 10**20 else ""
                )
                if not (display_name or username or phone or numeric_id):
                    continue
                if numeric_id:
                    key: tuple[str, int | str] = ("id", user_id)
                elif username:
                    key = ("username", username.casefold())
                else:
                    # Real Telegram User entities always have IDs. Retain
                    # otherwise distinct rows from test clients without IDs.
                    key = ("seen", scan.seen)
                if key in seen_members:
                    continue
                seen_members.add(key)
                scan.members.append(ExportMember(display_name, username, phone, numeric_id))
                if phone:
                    scan.phone_count += 1
                if username and username.casefold() not in seen_usernames:
                    seen_usernames.add(username.casefold())
                    scan.usernames.append(username)
        finally:
            await client.disconnect()
