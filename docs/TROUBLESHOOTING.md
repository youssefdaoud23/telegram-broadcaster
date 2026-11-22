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

## How do I consent to or remove many contacts at once?

On **Contacts**, tick individual checkboxes or **Select all visible**,
including after filtering to a contact group. You can then:

- Add or remove selected people from an application contact group.
- **Record verified opt-in** with the evidence/source and a confirmation
  that all selected contacts independently gave permission.
- **Withdraw consent** without an opt-in attestation.
- **Remove selected contacts** (archive/disable, retaining history).

For **all members of a group**, visit **Contact groups > Manage** and
use the whole-group consent section. This affects the contact's
permission everywhere they appear, including other groups. Changing
membership does not change consent. Check **Consent history** to review
recent bulk changes and their reasons.

Imported Telegram members remain non-consenting until actual opt-in
is confirmed. Do not use group membership itself as a consent source.

## A group member has neither a username nor a phone number

This is normal: Telegram lets users hide phone numbers and omit public
usernames. Scanned Telegram numeric user IDs are retained and can be
imported into Contacts. However, Telegram also requires an access hash
known to the specific connected account. The account that scanned the
group is the best choice when preparing a legitimate, opted-in message
to an ID-only contact; using a different account may fail entity resolution.
Telegram can restrict private messages regardless of identifier.

Directly imported group members are **not opted in** automatically.
Review and record each person's consent before considering a broadcast.
A successful group scan, a visible ID or a valid username never guarantees
that a private message can be delivered.

## Import a group without downloading and uploading CSV

On a completed group scan, choose **Import members directly**. The
proposed contact group name matches the Telegram group when allowed by
the app's 64-character naming rules. Existing groups are reused and
matching contacts linked without duplicates. Imports are limited to 5,000
members per scan. Group membership changes do not affect consent.
Contacts also supports bulk Add/Remove selected from a group.

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
