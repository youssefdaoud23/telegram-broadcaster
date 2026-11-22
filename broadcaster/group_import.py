"""Explicit, consent-preserving import from an authorized group scan.

A completed/limited/partial member scan can be turned into a private
contact group without downloading CSV. All imports remain non-opted-in.
No Telegram API calls, contact-list uploads, or messages occur here.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from . import core, group_export

DIRECT_IMPORT_LIMIT = 5000


@dataclass(frozen=True)
class ImportSummary:
    group_id: int
    created: int
    linked_existing: int
    already_in_group: int
    skipped: int
    created_group: bool


def _data_for_member(member: group_export.ExportMember) -> dict[str, str]:
    """Only import Telegram fields that pass the contact schema's validation."""
    user_id = member.user_id if re.fullmatch(r"[1-9][0-9]{2,19}", member.user_id or "") else ""
    username = member.username if re.fullmatch(r"[A-Za-z0-9_]{5,32}", member.username or "") else ""
    phone = member.phone if re.fullmatch(r"\+[1-9][0-9]{6,14}", member.phone or "") else ""
    if not (user_id or username or phone):
        raise ValueError("No valid Telegram identifier was provided for this member")
    name = (member.name.strip() or (f"@{username}" if username else f"Telegram user {user_id or phone}"))[:120]
    return core.normalize_contact(name, username, phone, user_id, "Imported from Telegram group scan")


def import_contact_record(
    db: sqlite3.Connection, data: dict[str, str], owner_id: int,
    group_ids: list[int], *, consent: bool = False, enabled: bool = True,
) -> tuple[bool, int]:
    """Add or safely reuse a contact, attaching only the requested groups.

    Returns (created, new_group_links). A caller-managed savepoint must roll
    back validation failures. Existing flags, notes and other memberships
    are never changed; archived or conflicting records cannot be revived.
    """
    core._allowed_group_ids(db, owner_id, group_ids)
    matches = db.execute("""
        SELECT id,username,phone,user_id,archived FROM contacts
        WHERE owner_id=? AND
        ((?<>'' AND user_id=?) OR
         (?<>'' AND username=? COLLATE NOCASE) OR
         (?<>'' AND phone=?))
    """, (owner_id, data["user_id"], data["user_id"],
          data["username"], data["username"],
          data["phone"], data["phone"])).fetchall()
    if len(matches) > 1:
        raise ValueError("Conflicting contact identifiers")
    if matches:
        existing = matches[0]
        if existing["archived"]:
            raise ValueError("An archived contact must not be resurrected")
        for field in ("user_id", "username", "phone"):
            if (existing[field] and data[field] and
                    existing[field].casefold() != data[field].casefold()):
                raise ValueError(f"Existing contact has a conflicting {field}")
        contact_id = existing["id"]
        # Only fill a missing Telegram numeric ID when another identifier
        # safely matched. Do not overwrite identity, consent or enabled.
        if data["user_id"] and not existing["user_id"]:
            db.execute(
                "UPDATE contacts SET user_id=?,updated_at=? "
                "WHERE id=? AND owner_id=? AND user_id=''",
                (data["user_id"], core.now(), contact_id, owner_id),
            )
    else:
        contact_id = core.insert_contact(
            db, data, owner_id, consent=consent, enabled=enabled,
        )
    added_links = 0
    for group_id in sorted(group_ids):
        result = db.execute(
            "INSERT OR IGNORE INTO contact_group_members(group_id,contact_id,added_at) "
            "VALUES (?,?,?)", (group_id, contact_id, core.now()),
        )
        added_links += result.rowcount
    return not bool(matches), added_links


def import_scan_members(db: sqlite3.Connection, scan: group_export.GroupScan,
                        owner_id: int, group_name: str) -> ImportSummary:
    """Create/reuse a group and link members without altering opt-in flags."""
    if scan.owner_id != owner_id:
        raise ValueError("This member scan does not belong to your workspace")
    if scan.state not in ("complete", "partial", "limited") or not scan.members:
        raise ValueError("Finish a group scan with visible members before importing")
    if len(scan.members) > DIRECT_IMPORT_LIMIT:
        raise ValueError("Direct import supports up to 5,000 members. Export CSV for larger groups.")
    group_name = core.validate_group_name(group_name)

    row = db.execute(
        "SELECT id FROM contact_groups WHERE owner_id=? AND name=? COLLATE NOCASE",
        (owner_id, group_name),
    ).fetchone()
    created_group = row is None
    group_id = core.add_contact_group(db, owner_id, group_name) if row is None else row["id"]

    created = linked_existing = already = skipped = 0
    for member in scan.members:
        db.execute("SAVEPOINT telegram_group_import_member")
        try:
            data = _data_for_member(member)
            is_new, new_links = import_contact_record(
                db, data, owner_id, [group_id], consent=False, enabled=True,
            )
            if is_new:
                created += 1
            elif new_links:
                linked_existing += 1
            else:
                already += 1
            db.execute("RELEASE SAVEPOINT telegram_group_import_member")
        except (ValueError, sqlite3.IntegrityError):
            db.execute("ROLLBACK TO SAVEPOINT telegram_group_import_member")
            db.execute("RELEASE SAVEPOINT telegram_group_import_member")
            skipped += 1
    return ImportSummary(group_id, created, linked_existing, already, skipped, created_group)
