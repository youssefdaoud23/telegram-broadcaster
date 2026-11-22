# Legacy n8n-hosted installation migration

[Back to README](../README.md)

**Not for new installations.** This page documents a one-time migration for an older installation originally located at `/root/n8n/telegram-broadcaster`. New users can clone into any directory and follow [Install](INSTALL.md).

The older installation used a Docker Compose bind mount `./data:/data`, a private `.env`, and a single application service with its original Compose project identity.

The repository includes `scripts/migrate_to_opt.sh` for that **specific original path only**. The script checks for active sending campaigns, takes and verifies a timestamped SQLite online backup, verifies the original database row counts, and moves the complete checkout, including `.env`, `data/`, session files and Git history, to `/opt/telegram-broadcaster`. It recreates only the broadcaster service and attempts rollback if an important step fails.

Its host-specific assumptions include GNU `mv -T`, root Docker access, the old directory existing, the new destination being absent, the same filesystem, and the specific original Compose project name. **Never run it on an unrelated installation.**

For the one original server, after independent verification of the script and current service state:

```bash
cd /root/n8n/telegram-broadcaster
git pull --ff-only origin main
bash scripts/migrate_to_opt.sh
```

Avoid attempting this migration if the original directory no longer exists or the service has already been moved. Do not delete the old data or run `docker compose down -v`.

## Older single-user database upgrades

Older releases stored accounts, contacts and campaigns without owner IDs. The current application migrates them to an owner-scoped schema and assigns legacy data to the oldest preexisting administrator. On first startup after an upgrade, other preexisting administrators remain ordinary users with empty workspaces.

**Always make a consistent SQLite backup** before a schema upgrade and verify the owner mapping against the actual existing database. If several people previously used one shared database, there is no safe automated way to reconstruct who owned each historical record.

For ordinary upgrades, follow [Operations](OPERATIONS.md). Preserve private `.env`, SQLite, session files and the existing Compose storage mode.
