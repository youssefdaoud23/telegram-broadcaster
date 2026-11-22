# Troubleshooting

[Back to README](../README.md) | [Install](INSTALL.md)

## Container will not start

1. Run `docker compose config --quiet` (add both Compose files if you chose named-volume storage).
2. Run `docker compose ps` and `docker compose logs --tail=100 broadcaster`.
3. Verify the `.env` file exists and includes a non-empty valid `APP_ENCRYPTION_KEY`.
4. Check that the persistent `/data` mount is writable to UID/GID 10001.
5. Ensure the chosen host port is free. Change `BROADCASTER_PORT` in your private `.env` if required.
6. Ensure Docker Engine is running and Docker Compose v2 is installed.

The Dockerfile has a health check at `/health`. A healthy container does not prove Telegram credentials are connected; account verification is separate.

## Permission denied on SQLite database or Telegram sessions

In default `./data` bind mode, run the one-time data ownership command as the Docker project operator:

```bash
docker compose run --rm --no-deps --user root --cap-add CHOWN broadcaster sh -c 'chown 10001:10001 /data'
```

On restrictive file shares, use the Docker-managed volume option **only for a new install**. Do not switch an existing installation without migrating all data. See [Install](INSTALL.md#b-choose-data-storage).

## I cannot open the page remotely

New installs bind to `127.0.0.1`. This is intentional. Use an SSH tunnel or an HTTPS reverse proxy. For a public site, validate TLS, origin routing, proxy authentication, `COOKIE_SECURE=true`, and firewall rules. `PUBLIC_BASE_URL` alone does not configure networking.

## Login fails or admin password forgotten

The first admin is created by:

```bash
docker compose run --rm -it --no-deps broadcaster python -m broadcaster.cli create-admin
```

For an existing admin use:

```bash
docker compose run --rm -it --no-deps broadcaster python -m broadcaster.cli reset-admin-password
```

Do not delete the database to reset a password. Use both `-f` flags if deploying with a named volume.

## Connected Telegram account says disconnected

Use **Telegram accounts > Check all sessions**. Telegram may revoke old sessions, or an identity mismatch may be detected. A revoked account must be reauthenticated with a Telegram login code and possible 2FA. Transient network errors should not be treated as confirmed revocation.

## Group export is incomplete

Telegram may hide members or phone numbers and can rate-limit enumeration. The export reports partial/limited scans. A "Complete" result only means that the accessible API iteration ended. Do not attempt to bypass account or group permissions.

## Broadcast is interrupted / unknown

Never automatically retry a delivery whose status is unknown. An interrupted campaign can have sent some messages before shutdown. Inspect campaign history and Telegram conversations. "Try again" is restricted to pending recipients; "Reuse and edit" creates a new deliberate draft. See [Security](SECURITY.md).

## Update appears to erase contacts

Do **not** create new users or accounts to compensate. Verify you are using the same Compose project, host directory or named volume, and original `.env`. Swapping storage modes creates an apparently empty new database. Restore only after confirming the actual data locations and taking backups.

## The site is working but no messages can be sent

`SEND_ENABLED` defaults to `false`. Check whether an operator intentionally enabled sending. The site also requires a connected Telegram sender, eligible consenting contacts, and an explicit confirmation of the draft. Telegram itself enforces additional restrictions.

## More help

- [Configuration reference](CONFIGURATION.md)
- [Backup and restore](OPERATIONS.md)
- [Security checklist](SECURITY.md)
