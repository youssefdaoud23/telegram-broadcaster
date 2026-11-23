"""Contact import from an uploaded CSV (including Google Sheets exports).

Each row is imported inside its own savepoint, so a bad row is skipped
without affecting the others. New and restored contacts start Inactive.
"""
from __future__ import annotations

import csv
import io
import sqlite3
from dataclasses import dataclass, field

from . import core, group_export, group_import

MAX_BYTES = 1024 * 1024
MAX_ROWS = 5000
MAX_NEW_GROUPS = 100
_YES = {"yes", "true", "1", "y"}


@dataclass
class CsvImportResult:
    created: int = 0
    restored: int = 0
    linked: int = 0
    unchanged: int = 0
    skipped: int = 0
    new_groups: int = 0
    group_ids: set[int] = field(default_factory=set)
    skip_reasons: dict[str, int] = field(default_factory=dict)
    # Group id for the name chosen on the form, when one was given.
    requested_group_id: int | None = None

    def main_skip_reason(self) -> str:
        return max(self.skip_reasons, key=self.skip_reasons.get) if self.skip_reasons else ""


def read_rows(contents: str) -> list[dict]:
    """Parse and sanity-check the CSV text; raises ValueError or csv.Error."""
    reader = csv.DictReader(io.StringIO(contents, newline=""), strict=True)
    if not reader.fieldnames or not {"Name", "name"}.intersection(reader.fieldnames):
        raise ValueError("CSV requires a Name column and Telegram ID, Username or Phone")
    records = list(reader)
    if len(records) > MAX_ROWS:
        raise ValueError("CSV has more than 5,000 rows; split it into smaller files.")
    if not records:
        raise ValueError("CSV has no contact rows")
    return records


def _contact_data(row: dict) -> dict:
    """Name, username, phone, user ID and notes from one CSV row."""
    if None in row:
        raise ValueError("Row has more columns than the CSV header")
    raw_identifier = (row.get("ID") or "").strip()
    username = (row.get("Username") or row.get("username") or
                (raw_identifier if raw_identifier.startswith("@") else ""))
    phone = (row.get("Phone") or row.get("Phone Number") or row.get("phone") or
             (raw_identifier if raw_identifier.startswith("+") else ""))
    numeric = (row.get("User ID") or row.get("Telegram User ID") or
               (raw_identifier if raw_identifier.isdigit() else ""))
    return core.normalize_contact(
        row.get("Name") or row.get("name") or "",
        username, phone, numeric, row.get("Notes") or "",
    )


def _automatic_group_name(row: dict, requested_group: str) -> str:
    """The form's group name, else the Telegram Group column of a scan export."""
    # A manually chosen group takes precedence over the Telegram Group
    # metadata in scan-generated CSV files.
    if requested_group:
        return requested_group
    raw_group_name = (row.get("Telegram Group") or "").strip()
    if not raw_group_name:
        return ""
    # Undo the spreadsheet-formula escape added by our own group CSV
    # exporter before validating its name.
    if (raw_group_name.startswith("'") and
            raw_group_name[1:].lstrip().startswith(("=", "+", "-", "@"))):
        raw_group_name = raw_group_name[1:]
    return group_export.suggested_group_name(raw_group_name)


def import_rows(db: sqlite3.Connection, owner_id: int, records: list[dict],
                requested_group: str = "") -> CsvImportResult:
    """Import parsed rows for one owner. The caller commits."""
    result = CsvImportResult()
    known_groups = {
        group["name"].casefold(): group["id"]
        for group in core.contact_groups(db, owner_id)
    }
    for row in records:
        db.execute("SAVEPOINT import_contact")
        pending_groups: dict[str, int] = {}
        try:
            data = _contact_data(row)
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

            automatic_name = _automatic_group_name(row, requested_group)
            if automatic_name:
                key = automatic_name.casefold()
                group_id = known_groups.get(key) or pending_groups.get(key)
                if group_id is None:
                    if result.new_groups + len(pending_groups) >= MAX_NEW_GROUPS:
                        raise ValueError("Import may create at most 100 new groups")
                    group_id = core.add_contact_group(db, owner_id, automatic_name)
                    pending_groups[key] = group_id
                selected_groups.append(group_id)
            selected_groups = sorted(set(selected_groups))
            opted_in = str(row.get("Opt In") or row.get("Consent") or "").strip().casefold() in _YES
            is_new, added_links, was_restored = group_import.import_contact_record(
                db, data, owner_id, selected_groups,
                consent=opted_in, restore_archived=True,
            )
            db.execute("RELEASE SAVEPOINT import_contact")
            known_groups.update(pending_groups)
            result.new_groups += len(pending_groups)
            result.group_ids.update(selected_groups)
            if is_new:
                result.created += 1
            elif was_restored:
                result.restored += 1
            elif added_links:
                result.linked += 1
            else:
                result.unchanged += 1
        except (ValueError, sqlite3.IntegrityError, TypeError) as exc:
            db.execute("ROLLBACK TO SAVEPOINT import_contact")
            db.execute("RELEASE SAVEPOINT import_contact")
            result.skipped += 1
            reason = str(exc).splitlines()[0][:100] or "Invalid contact"
            result.skip_reasons[reason] = result.skip_reasons.get(reason, 0) + 1
    if requested_group:
        result.requested_group_id = known_groups.get(requested_group.casefold())
    return result
