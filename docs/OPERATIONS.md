# Operate, update, back up and recover Telegram Broadcaster

[Back to README](../README.md) | [Install](INSTALL.md)

The following examples use the default `./data` storage. If you installed with a Docker-managed named volume, add `-f compose.yaml -f compose.volume.yaml` after `docker compose` on **every** command. Keep the same project name and folder.

## Check health without changing anything

```bash
docker compose ps
docker compose logs --tail=100 broadcaster
curl -fsS http://127.0.0.1:8789/health
```

Adjust the port if your `.env` uses another `BROADCASTER_PORT`. The health endpoint checks the web process, not Telegram authorization. Use the UI to check account status.

## Back up before updating

**Do not restart/rebuild while any campaign is queued or sending.** Restarting the service loses the in-memory sending task. Startup deliberately marks orphaned campaigns interrupted rather than automatically retrying them.

Make a consistent SQLite online backup while the container is running:

```bash
docker compose exec -T broadcaster python - <<'PY'
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

source = Path("/data/broadcaster.sqlite3")
backup = source.with_name(
    "broadcaster.backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".sqlite3"
)
if backup.exists():
    raise SystemExit("Refusing to overwrite an existing backup")
connection = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
destination = sqlite3.connect(str(backup))
try:
    connection.backup(destination)
    if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("Backup integrity check failed")
finally:
    destination.close()
    connection.close()
os.chmod(backup, 0o600)
print("Verified SQLite backup:", backup)
PY
```

The backup above stays **inside the same data directory or volume**. It protects against accidental database migrations but not loss of the host disk. For disaster recovery, also save an encrypted copy **off the server** containing:

1. The SQLite online backup, as a file.
2. The complete `telegram_sessions/` directory, copied when the application is idle.
3. The private `.env` containing the original `APP_ENCRYPTION_KEY` and deployment settings.
4. The Git commit or release used, plus the Compose files.

For default host-directory mode, all persistent data is in `./data/` and private configuration is `.env`.

For Docker-managed volume mode, retrieve the **verified online backup**
created above rather than copying the live SQLite database file:

~~~bash
docker compose -f compose.yaml -f compose.volume.yaml cp \
  broadcaster:/data/broadcaster.backup-YYYYMMDDTHHMMSSZ.sqlite3 \
  ./broadcaster.backup.sqlite3
~~~

Replace the example timestamp with the actual verified backup filename.
When the application is idle, also export the session directory:

~~~bash
docker compose -f compose.yaml -f compose.volume.yaml cp \
  broadcaster:/data/telegram_sessions ./telegram_sessions
~~~

Secure those copies and move them to protected off-host storage. Do not use
a raw copy of the active SQLite database file as a substitute for the
consistent online backup.

## Update an existing installation

Verify all campaigns have finished, take and verify a backup, then:

```bash
git status --short --branch
git pull --ff-only origin main
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

Use the two-`-f` prefix for named-volume installs. Do not run `docker compose down -v` or delete `data/` or `.env`. Database schema migrations run automatically on startup, but an automatic rollback of the database is **not** provided.

If you already customized the source, do not override or discard local changes. Review them or use a separate branch before pulling.

## Restore carefully

1. Verify there is no in-progress send and note the last confirmed delivery states. A restore can change the apparent state of messages that were already sent.
2. Stop **only** the broadcaster container.
3. Preserve the current database and session data as an additional recovery copy; do not overwrite the only working backup.
4. Restore the matching database backup and full `telegram_sessions/` directory to the exact same `/data` storage location for the installation.
5. Restore the exact `.env` with its original `APP_ENCRYPTION_KEY` and Compose storage mode. Use restrictive permissions and ensure UID 10001 can write `/data`.
6. Start the broadcaster and verify the user accounts, contacts, connected sessions and history in the UI. **Do not send test messages unless explicitly authorized.**

A database-only restore may show saved accounts while their Telethon sessions are unavailable, and a session-only restore can mismatch encrypted credentials. Treat the encrypted database, Telegram session files and Fernet key as a coherent set.

## Reset an owner or user password

For your own logged-in account, use **Change password** in the UI. The installation owner can reset another user's website password using **User management** after reauthentication.

For a forgotten website password, an authorized server operator can run:

```bash
docker compose run --rm -it --no-deps broadcaster python -m broadcaster.cli reset-admin-password
```

This resets the broadcaster website password, not the Telegram account's 2FA password. It revokes the old website sessions without deleting contacts or message history.

## Service restarts

```bash
docker compose restart broadcaster
```

Restart only after all sending is finished. The `restart` command does **not** pick up changes to the `.env` or image. For configuration changes, use `docker compose up -d --force-recreate broadcaster`; for code changes, add `--build`.

## Troubleshooting

See [Troubleshooting](TROUBLESHOOTING.md), [Configuration](CONFIGURATION.md) and [Security](SECURITY.md).
