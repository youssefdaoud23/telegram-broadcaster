#!/usr/bin/env bash
# Controlled, one-time move of the standalone Telegram Broadcaster.
# Run from the server as root, only when no broadcasts are actively sending.
set -Eeuo pipefail

OLD=/root/n8n/telegram-broadcaster
NEW=/opt/telegram-broadcaster
STOPPED=0
MOVED=0
BACKUP_NAME=""

say() { printf '[telegram-broadcaster] %s\n' "$*"; }
fail() { say "ERROR: $*" >&2; exit 1; }

rollback() {
  local status=$?
  if [[ "$status" -eq 0 ]]; then
    return
  fi
  trap - EXIT
  if [[ "$STOPPED" == 0 && "$MOVED" == 0 ]]; then
    say "Migration aborted before service shutdown. Original service remains running." >&2
    exit "$status"
  fi
  say "Migration failed (exit $status). Attempting to restore the original directory and service." >&2
  if [[ "$MOVED" == 1 && -d "$NEW" && ! -e "$OLD" ]]; then
    docker compose -f "$NEW/compose.yaml" stop broadcaster >/dev/null 2>&1 || true
    mv -T -- "$NEW" "$OLD" || say "CRITICAL: Move back failed; investigate the directories before retrying." >&2
  fi
  if [[ "$STOPPED" == 1 && -f "$OLD/compose.yaml" ]]; then
    docker compose -f "$OLD/compose.yaml" up -d --no-build --force-recreate broadcaster \
      || say "CRITICAL: Automatic service recovery failed; inspect Docker status." >&2
  fi
  if [[ -n "$BACKUP_NAME" ]]; then
    say "A verified pre-migration database backup remains in data/: $BACKUP_NAME" >&2
  fi
  exit "$status"
}
trap rollback EXIT

[[ "$EUID" -eq 0 ]] || fail "Run as root to preserve the private data directory and Docker bind mount."
command -v docker >/dev/null || fail "Docker is not installed."
command -v curl >/dev/null || fail "curl is required for the health check."
docker compose version >/dev/null || fail "Docker Compose plugin is required."

if [[ ! -d "$OLD" ]]; then
  if [[ -f "$NEW/compose.yaml" ]]; then
    say "Already relocated to $NEW. No changes made."
    docker compose -f "$NEW/compose.yaml" ps broadcaster
    curl --fail --silent --show-error --max-time 4 http://127.0.0.1:8789/health
    exit 0
  fi
  fail "Original directory $OLD is missing."
fi
[[ -f "$OLD/compose.yaml" && -f "$OLD/.env" && -d "$OLD/data" ]] \
  || fail "Compose file, private .env, or persistent data directory is missing."
[[ ! -L "$OLD" && ! -L "$OLD/data" ]] || fail "Unexpected symbolic link in the source."
[[ ! -e "$NEW" && ! -L "$NEW" ]] || fail "$NEW already exists. Refusing to overwrite or merge directories."
mkdir -p /opt
[[ "$(stat -c %d "$OLD")" == "$(stat -c %d /opt)" ]] \
  || fail "The source and /opt are on different filesystems; refusing an unsafe cross-device move."

old_project="$(docker compose -f "$OLD/compose.yaml" config --format json | python3 -c 'import sys,json; print(json.load(sys.stdin)["name"])')"
[[ "$old_project" == "telegram-broadcaster" ]] \
  || fail "Unexpected Docker Compose project '$old_project'; inspect it before migrating."

running="$(docker compose -f "$OLD/compose.yaml" ps --status running -q broadcaster)"
[[ -n "$running" ]] || fail "The original broadcaster is not running. Cannot verify an as-is migration."
say "Checking for unfinished broadcasts without altering the database."
read_counts() {
  local root="$1"
  docker compose -f "$root/compose.yaml" exec -T broadcaster python - <<'PY'
import json
import sqlite3
import sys
from pathlib import Path

path = Path("/data/broadcaster.sqlite3")
if not path.is_file():
    sys.exit("Database is missing; migration aborted")
db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
    busy = db.execute(
        "SELECT COUNT(*) FROM campaigns WHERE state IN ('queued', 'sending')"
    ).fetchone()[0]
    if busy:
        sys.exit(f"{busy} campaigns are queued or sending. Migration aborted.")
    tables = ("admins", "accounts", "contacts", "campaigns", "deliveries")
    counts = {table: db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
              for table in tables}
    print(json.dumps(counts, sort_keys=True, separators=(",", ":")))
finally:
    db.close()
PY
}

before_counts="$(read_counts "$OLD")"
env_checksum="$(sha256sum "$OLD/.env" | cut -d' ' -f1)"
data_inode="$(stat -c %i "$OLD/data")"
say "Record counts at initial preflight: $before_counts"
say "Building the updated broadcaster image while the existing container is still running."
docker compose -f "$OLD/compose.yaml" build broadcaster
# Recheck after image build; it may have taken time and users could have
# created drafts or started a campaign while the image was compiling.
before_counts="$(read_counts "$OLD")"
say "Record counts immediately before backup: $before_counts"

say "Creating a timestamped online SQLite backup in the persistent data directory."
backup_name="$(docker compose -f "$OLD/compose.yaml" exec -T broadcaster python - <<'PY'
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

source = Path("/data/broadcaster.sqlite3")
backup = source.with_name(
    "broadcaster.pre-opt-migration-" +
    datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".sqlite3"
)
if backup.exists():
    raise SystemExit("Backup target exists; refusing overwrite")
db = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
copy = sqlite3.connect(str(backup))
try:
    db.backup(copy)
    if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("Backup integrity verification failed")
finally:
    copy.close()
    db.close()
os.chmod(backup, 0o600)
print(backup.name)
PY
)"
[[ "$backup_name" == broadcaster.pre-opt-migration-*.sqlite3 ]] \
  || fail "Unexpected backup filename returned; stopping."
[[ -f "$OLD/data/$backup_name" ]] || fail "Backup file missing after copy."
BACKUP_NAME="$backup_name"
say "Verified database backup: data/$backup_name"

say "Stopping only the broadcaster service."
# Record that shutdown may have started before invoking Docker. If the stop
# command itself fails after partially stopping the container, recover it.
STOPPED=1
docker compose -f "$OLD/compose.yaml" stop broadcaster

# The image build and graceful shutdown may have taken time. Check the exact
# stopped database before moving; a new queued/sending campaign or changed
# record counts will abort the move and restart the original service.
stopped_counts="$(python3 - "$OLD/data/broadcaster.sqlite3" <<'PY'
import json
import sqlite3
import sys

path = sys.argv[1]
db = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
try:
    busy = db.execute(
        "SELECT COUNT(*) FROM campaigns WHERE state IN ('queued', 'sending')"
    ).fetchone()[0]
    if busy:
        sys.exit(f"{busy} campaigns were active during shutdown. Do not move.")
    tables = ("admins", "accounts", "contacts", "campaigns", "deliveries")
    counts = {name: db.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
              for name in tables}
    print(json.dumps(counts, sort_keys=True, separators=(",", ":")))
finally:
    db.close()
PY
)"
[[ "$stopped_counts" == "$before_counts" ]]   || fail "Records changed between the backup and shutdown; review before moving."

say "Moving the complete project directory (including .env, data and Git history)."
mv -T -- "$OLD" "$NEW"
MOVED=1

[[ "$(sha256sum "$NEW/.env" | cut -d' ' -f1)" == "$env_checksum" ]] \
  || fail "Private .env checksum changed during the move."
[[ "$(stat -c %i "$NEW/data")" == "$data_inode" ]] \
  || fail "Persistent data directory was not preserved."
[[ -f "$NEW/data/$backup_name" ]] || fail "Verified database backup is missing at target."

say "Starting only broadcaster from its new location."
docker compose -f "$NEW/compose.yaml" up -d --no-build --force-recreate broadcaster

say "Checking the public-service origin health."
healthy=0
for attempt in $(seq 1 25); do
  if curl --fail --silent --max-time 4 http://127.0.0.1:8789/health >/dev/null; then
    healthy=1
    break
  fi
  sleep 2
done
[[ "$healthy" == 1 ]] || fail "Health endpoint did not recover."
after_counts="$(read_counts "$NEW")"
[[ "$before_counts" == "$after_counts" ]] \
  || fail "Database record counts changed across migration: before=$before_counts after=$after_counts"

say "Migration PASS: /opt/telegram-broadcaster"
say "Record counts unchanged: $after_counts"
say "Private configuration and original data directory preserved."
say "Verified backup: $NEW/data/$backup_name"
docker compose -f "$NEW/compose.yaml" ps broadcaster
trap - EXIT
STOPPED=0
MOVED=0