# Security, account safety and privacy

[Back to README](../README.md)

This application holds personal contact data, Telegram credentials and session files. Deploy it only in an environment you control and understand.

## Before you invite remote users

- Publish the website behind **HTTPS** with a trusted reverse proxy and appropriate access control. Do not publish the local HTTP port directly to the Internet.
- Set `COOKIE_SECURE=true` when browsers use HTTPS. Do not assume `PUBLIC_BASE_URL` configures a proxy or certificate.
- Keep `APP_ENCRYPTION_KEY`, `.env`, SQLite database, backups and `telegram_sessions/` private. The Fernet key and Telegram session files are both sensitive.
- Use a unique strong owner password, provision separate user accounts, and disable accounts that should no longer have access.
- Patch the host, Docker and reverse proxy. Configure firewall and origin rules so the reverse proxy cannot be bypassed.
- Limit which people have Docker daemon access. Docker control on the host is effectively high privilege.
- Use a trusted hosting provider and disk protection, and secure off-host backups. Prefer a dedicated host account and restricted project ownership.
- Consult applicable privacy and messaging rules. Group membership alone **does not** mean a person consented to direct marketing.

## Data isolation

Each broadcaster user gets an application workspace with owner-scoped Telegram accounts, contacts, contact groups, campaigns and scan results. The installation owner sees account-management information, not others' contact records through the UI.

This is **not** full operating-system isolation. All users share the same application process, SQLite database and encryption key. Anyone with host root/Docker or direct filesystem access can potentially access all workspaces.

## Telegram authorization

Telegram login codes and optional 2FA passwords are not stored. The application stores the API ID/hash and Telegram session files required for subsequent access. Ending a Telegram session from the Telegram app can revoke that session; the broadcaster checks authorization and marks revoked sessions disconnected.

Telegram can reject membership scans, hide phone numbers, rate-limit operations or restrict sending. The broadcaster does not bypass these controls. A group CSV contains only values exposed to the connected account. Exported phone numbers and contact names remain personal data even when they are visible.

## Sending and consent

`SEND_ENABLED=false` is the safest default. Sending is a separate, user-confirmed action after reviewing recipients. Only eligible opted-in contacts can be selected. Contacts in multiple groups are deduplicated per campaign.

Do not use the system to send unsolicited bulk private messages. When a campaign is interrupted, an unknown delivery state is **not automatically retried**; review history and the Telegram conversation before deliberately reusing a previous broadcast.

## Operational cautions

- Never commit or share `.env`, `data/`, session files, database backups, or recipient exports to GitHub.
- Never regenerate the encryption key for an existing account database.
- Before applying updates, take an online SQLite backup and preserve Telegram sessions and the encryption key.
- Never recreate a container during an active broadcast.
- Do not change the Docker volume mode or project name without planning a full data migration.
- If a real security issue is found, avoid sharing credentials or private records in a public GitHub issue. Contact the repository owner privately.

## License

This repository has no software license file at present. A public repository is not the same as permission to redistribute or modify the software. The owner should select and publish an explicit license before offering broad third-party use.
