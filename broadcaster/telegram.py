"""Telegram personal-account onboarding and controlled one-to-one delivery."""
from __future__ import annotations

import asyncio
import logging
import os
import re
import time
from pathlib import Path

from telethon import TelegramClient, errors, types

from .core import database, decrypt, encrypt, now

log = logging.getLogger(__name__)
_locks: dict[str, asyncio.Lock] = {}
_tasks: set[asyncio.Task] = set()
# Avoid repeated Telegram authorization calls on every navigation.
_status_checks: dict[str, float] = {}
STATUS_TTL_SECONDS = 90
REVOKED_ERRORS = (
    errors.AuthKeyUnregisteredError, errors.AuthKeyDuplicatedError,
    errors.AuthKeyInvalidError, errors.SessionRevokedError,
    errors.SessionExpiredError, errors.UserDeactivatedError,
    errors.UserDeactivatedBanError,
)
SESSION_EXPIRED_MESSAGE = "Telegram session has expired or been revoked. Reconnect this account."


class TelegramProblem(Exception):
    """User-safe Telegram failure without revealing credentials or phone numbers."""


def account_lock(account_id: str) -> asyncio.Lock:
    return _locks.setdefault(account_id, asyncio.Lock())


def session_path(row) -> str:
    root = Path(os.getenv("TELEGRAM_SESSION_DIR", "/data/telegram_sessions"))
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Account-generated 32-character hex IDs only; no paths supplied by users.
    name = row["session_name"]
    if not re.fullmatch(r"[a-f0-9]{32}", name):
        raise TelegramProblem("Invalid stored Telegram session identifier")
    return str(root / name)


def new_client(row) -> TelegramClient:
    return TelegramClient(session_path(row), int(row["api_id"]), decrypt(row["api_hash_enc"]))


def telegram_error(exc: Exception) -> TelegramProblem:
    if isinstance(exc, errors.FloodWaitError):
        return TelegramProblem("Telegram requested a cooldown. Do not retry until the restriction expires.")
    if isinstance(exc, errors.PhoneNumberInvalidError):
        return TelegramProblem("Telegram rejected the phone number. Check the full international format.")
    if isinstance(exc, errors.PhoneCodeInvalidError):
        return TelegramProblem("The Telegram verification code is incorrect.")
    if isinstance(exc, errors.PhoneCodeExpiredError):
        return TelegramProblem("The Telegram code expired. Request a new code.")
    if isinstance(exc, errors.PasswordHashInvalidError):
        return TelegramProblem("The Telegram two-step verification password is incorrect.")
    if isinstance(exc, errors.RPCError):
        return TelegramProblem("Telegram rejected the request (" + type(exc).__name__ + ").")
    log.warning("Telegram operation failed: %s", type(exc).__name__)
    return TelegramProblem("Telegram connection failed (" + type(exc).__name__ + ").")


def _fetch_account(account_id: str):
    with database() as db:
        row = db.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
    if row is None:
        raise TelegramProblem("Telegram account not found")
    return row


def _save_state(account_id: str, state: str, username: str = "", code_hash: str | None = None,
                last_error: str = ""):
    with database() as db:
        db.execute("""
            UPDATE accounts SET state=?, username=CASE WHEN ?<>'' THEN ? ELSE username END,
            code_hash_enc=?, last_error=?,updated_at=? WHERE id=?
        """, (state, username, username, code_hash, last_error, now(), account_id))
        db.commit()
    _status_checks.pop(account_id, None)


def _mark_revoked(account_id: str) -> None:
    """Mark only confirmed session revocation as disconnected, never a network outage."""
    _save_state(account_id, "disconnected", last_error=SESSION_EXPIRED_MESSAGE)


async def request_code(account_id: str) -> None:
    async with account_lock(account_id):
        row = _fetch_account(account_id)
        try:
            client = new_client(row)
            await client.connect()
            try:
                if await client.is_user_authorized():
                    me = await client.get_me()
                    _finish_login(account_id, row, me)
                    return
                sent = await client.send_code_request(decrypt(row["phone_enc"]))
                _save_state(account_id, "code_sent", code_hash=encrypt(sent.phone_code_hash))
            finally:
                await client.disconnect()
        except TelegramProblem:
            raise
        except Exception as exc:
            raise telegram_error(exc) from exc


def _finish_login(account_id: str, row, me) -> None:
    actual = ("@" + me.username).lower() if me.username else ""
    expected = row["expected_username"].lower()
    if expected and expected != actual:
        raise TelegramProblem("Signed-in account does not match the expected Telegram username")
    _save_state(account_id, "connected", username=actual)


async def verify_code(account_id: str, code: str) -> bool:
    """Return True when connected, False when Telegram requires a 2FA password."""
    if not re.fullmatch(r"[0-9]{4,8}", code.strip()):
        raise TelegramProblem("Enter the numeric Telegram verification code")
    async with account_lock(account_id):
        row = _fetch_account(account_id)
        if row["state"] != "code_sent" or not row["code_hash_enc"]:
            raise TelegramProblem("Request a fresh verification code first")
        try:
            client = new_client(row)
            await client.connect()
            try:
                try:
                    me = await client.sign_in(
                        phone=decrypt(row["phone_enc"]),
                        code=code.strip(),
                        phone_code_hash=decrypt(row["code_hash_enc"]),
                    )
                except errors.SessionPasswordNeededError:
                    _save_state(account_id, "password_required")
                    return False
                _finish_login(account_id, row, me)
                return True
            finally:
                await client.disconnect()
        except TelegramProblem:
            raise
        except Exception as exc:
            raise telegram_error(exc) from exc


async def verify_password(account_id: str, password: str) -> None:
    async with account_lock(account_id):
        row = _fetch_account(account_id)
        if row["state"] != "password_required":
            raise TelegramProblem("A Telegram two-step verification password was not requested")
        try:
            client = new_client(row)
            await client.connect()
            try:
                me = await client.sign_in(password=password)
                _finish_login(account_id, row, me)
            finally:
                await client.disconnect()
        except TelegramProblem:
            raise
        except Exception as exc:
            raise telegram_error(exc) from exc


async def verify_connected(account_id: str, *, cache_seconds: int = 0) -> str:
    """Verify the actual Telegram session and persist confirmed disconnections.

    Never sends a message or automatically signs in. A transient network
    failure does not mean that the Telegram session was revoked.
    """
    async with account_lock(account_id):
        row = _fetch_account(account_id)
        if row["state"] != "connected":
            raise TelegramProblem("Telegram account is not connected. Request a login code.")
        checked = _status_checks.get(account_id)
        if cache_seconds and checked is not None and time.monotonic() - checked < cache_seconds:
            return row["username"] or row["label"]
        client = None
        try:
            client = new_client(row)

            async def check() -> str:
                await client.connect()
                me = await confirm_identity(client, row)
                _status_checks[account_id] = time.monotonic()
                return ("@" + me.username) if me.username else (me.first_name or "Telegram user")

            return await asyncio.wait_for(check(), timeout=12)
        except TelegramProblem:
            raise
        except REVOKED_ERRORS as exc:
            _mark_revoked(account_id)
            raise TelegramProblem(SESSION_EXPIRED_MESSAGE) from exc
        except Exception as exc:
            # Do not mark disconnected on DNS, timeouts or transient RPC errors.
            raise telegram_error(exc) from exc
        finally:
            if client is not None:
                try:
                    await asyncio.wait_for(client.disconnect(), timeout=3)
                except Exception:
                    log.warning("Session status check cleanup failed: %s", account_id)


async def refresh_connected_accounts(account_ids: list[str], *, force: bool = False) -> list[str]:
    """Refresh visible account statuses without sending, with bounded concurrency."""
    semaphore = asyncio.Semaphore(3)

    async def refresh(account_id: str) -> str | None:
        async with semaphore:
            try:
                await verify_connected(account_id, cache_seconds=0 if force else STATUS_TTL_SECONDS)
            except TelegramProblem as exc:
                # Only confirmed revocations change state. Report transient errors.
                if "connection failed" in str(exc).lower() or "rejected the request" in str(exc).lower():
                    return "Some Telegram sessions could not be checked. Try again later."
            return None

    results = await asyncio.gather(*(refresh(account_id) for account_id in account_ids))
    return sorted(set(message for message in results if message))


async def confirm_identity(client, row, *, expired: str = SESSION_EXPIRED_MESSAGE,
                           mismatch: str = "The Telegram session identity changed. Reconnect this account.",
                           revoke_on_mismatch: bool = True):
    """Return ``get_me()`` once the session is live and still the stored account.

    A dead session is marked revoked. A different Telegram user than the one
    stored is never accepted, so a swapped session cannot send or scan.
    """
    if not await client.is_user_authorized():
        _mark_revoked(row["id"])
        raise TelegramProblem(expired)
    me = await client.get_me()
    if me is None:
        _mark_revoked(row["id"])
        raise TelegramProblem(expired)
    actual = ("@" + me.username).lower() if me.username else ""
    if (row["expected_username"] and row["expected_username"].lower() != actual) or (
        row["username"] and row["username"].lower() != actual
    ):
        if revoke_on_mismatch:
            _mark_revoked(row["id"])
        raise TelegramProblem(mismatch)
    return me


def _definitely_not_sent(exc: BaseException) -> bool:
    """True only when Telegram itself answered and refused the message.

    Any RPC error is a reply from Telegram, so nothing was delivered, except
    its internal-error (500) and timeout (503) replies, which can arrive after
    the message went through.
    """
    return isinstance(exc, errors.RPCError) and not isinstance(
        exc, (errors.ServerError, errors.TimedOutError))


async def dispatch(campaign_id: str):
    """Run once; no recovery/resend on restart to avoid duplicate messages."""
    with database() as db:
        campaign = db.execute("SELECT * FROM campaigns WHERE id=?", (campaign_id,)).fetchone()
    if not campaign or campaign["state"] != "queued":
        return
    account_id = campaign["account_id"]
    async with account_lock(account_id):
        if os.getenv("SEND_ENABLED", "false").lower() != "true":
            with database() as db:
                db.execute("UPDATE campaigns SET state='blocked',completed_at=? WHERE id=? AND state='queued'", (now(),campaign_id))
                db.commit()
            return
        try:
            row = _fetch_account(account_id)
            if row["owner_id"] != campaign["owner_id"]:
                raise TelegramProblem("Sender does not belong to this broadcast's workspace")
            with database() as db:
                owner=db.execute("SELECT active FROM admins WHERE id=?", (campaign["owner_id"],)).fetchone()
            if not owner or not owner["active"]:
                raise TelegramProblem("Workspace user is disabled")
            if row["state"] != "connected":
                raise TelegramProblem("Sender has not completed Telegram authentication")
            with database() as db:
                db.execute("UPDATE campaigns SET state='sending', started_at=? WHERE id=? AND state='queued'", (now(),campaign_id))
                db.commit()
            client = new_client(row)
            await client.connect()
            try:
                await confirm_identity(client, row, expired="Telegram sender session expired",
                                       mismatch="Telegram sender identity mismatch. Nothing sent.",
                                       revoke_on_mismatch=False)
                with database() as db:
                    items = db.execute("SELECT * FROM deliveries WHERE campaign_id=? ORDER BY id", (campaign_id,)).fetchall()
                paused = False
                for i, delivery in enumerate(items):
                    # A contact can opt out after campaign drafting. Recheck before each send.
                    with database() as db:
                        owner=db.execute("SELECT active FROM admins WHERE id=?", (campaign["owner_id"],)).fetchone()
                        if not owner or not owner["active"]:
                            paused = True
                            break  # Disabled users cannot continue broadcasting.
                        contact = db.execute(
                            "SELECT enabled,consent,archived FROM contacts WHERE id=? AND owner_id=?",
                            (delivery["contact_id"],campaign["owner_id"]),
                        ).fetchone()
                        if contact is None or not contact["enabled"] or not contact["consent"] or contact["archived"]:
                            db.execute("UPDATE deliveries SET state='skipped',error='Contact opted out or disabled',updated_at=? WHERE id=?", (now(),delivery["id"]))
                            db.commit()
                            continue
                        db.execute("UPDATE deliveries SET state='sending',updated_at=? WHERE id=? AND state='pending'", (now(),delivery["id"]))
                        db.commit()
                    attempted = False  # True once the message may have left this server
                    try:
                        target = delivery["destination"]
                        lookup = int(target) if target.isdigit() else target
                        entity = await client.get_entity(lookup)
                        if not isinstance(entity, types.User) or entity.deleted:
                            raise TelegramProblem("Telegram user is not accessible")
                        attempted = True
                        msg = await client.send_message(entity, campaign["message"])
                        with database() as db:
                            db.execute("UPDATE deliveries SET state='sent',message_id=?,error='',updated_at=? WHERE id=?", (msg.id,now(),delivery["id"]))
                            db.commit()
                    except (errors.FloodError, errors.PeerFloodError) as exc:
                        # FloodWait: a cooldown. PeerFlood: Telegram has flagged
                        # this account for spam. Either way, sending more makes it
                        # worse (and risks a ban), so stop the whole broadcast.
                        reason = ("Telegram spam restriction (PEER_FLOOD); broadcast paused"
                                  if isinstance(exc, errors.PeerFloodError)
                                  else "Telegram flood wait; no automatic retry")
                        with database() as db:
                            db.execute("UPDATE deliveries SET state='blocked',error=?,updated_at=? WHERE id=?", (reason,now(),delivery["id"]))
                            db.commit()
                        paused = True
                        break
                    except Exception as exc:
                        # Do not include PII or remote exception text in persistent logs.
                        if attempted and not _definitely_not_sent(exc):
                            # Connection drop or timeout mid-send: Telegram may already
                            # have delivered it. Never call that "failed" (it would be
                            # offered for resending), and stop so the rest stay pending.
                            with database() as db:
                                db.execute("UPDATE deliveries SET state='unknown',error=?,updated_at=? WHERE id=?",
                                           (f"Outcome unknown ({type(exc).__name__}); check the chat before sending again",
                                            now(),delivery["id"]))
                                db.commit()
                            paused = True
                            break
                        with database() as db:
                            db.execute("UPDATE deliveries SET state='failed',error=?,updated_at=? WHERE id=?", (type(exc).__name__,now(),delivery["id"]))
                            db.commit()
                    if i < len(items)-1:
                        await asyncio.sleep(2)
                with database() as db:
                    db.execute("UPDATE campaigns SET state=?, completed_at=? WHERE id=?", ("paused" if paused else "finished",now(),campaign_id))
                    db.commit()
            finally:
                await client.disconnect()
        except Exception as exc:
            if isinstance(exc, REVOKED_ERRORS):
                _mark_revoked(account_id)
            log.warning("Campaign %s ended with %s", campaign_id, type(exc).__name__)
            with database() as db:
                db.execute("UPDATE campaigns SET state='failed',completed_at=? WHERE id=?", (now(),campaign_id))
                db.execute("UPDATE deliveries SET state='failed',error='Sender unavailable',updated_at=? WHERE campaign_id=? AND state='pending'", (now(),campaign_id))
                # A send cut off by this error may or may not have reached Telegram.
                db.execute("UPDATE deliveries SET state='unknown',error='Interrupted before delivery status was confirmed',updated_at=? WHERE campaign_id=? AND state='sending'", (now(),campaign_id))
                db.commit()


def enqueue(campaign_id: str):
    task = asyncio.create_task(dispatch(campaign_id))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)