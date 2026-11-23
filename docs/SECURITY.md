# Security, account safety and privacy

[Back to README](../README.md)

This application holds personal contact data, Telegram credentials and session files. Deploy it only in an environment you control and understand.

## Before you invite remote users

- Publish the website behind **HTTPS** with a trusted reverse proxy and appropriate access control. Do not publish the local HTTP port directly to the Internet.
- Set `COOKIE_SECURE=true` when browsers use HTTPS. Do not assume `PUBLIC_BASE_URL` configures a proxy or certificate.
- Keep `APP_ENCRYPTION_KEY`, `.env`, SQLite database, backups and `telegram_sessions/` private. The Fernet key and Telegram session files are both sensitive.
- Use a unique strong owner password, provision separate user accounts, and disable accounts that should no longer have access.
- Failed sign-ins are throttled per username **and** visitor address: 5 failures for one username from one address, or 20 failures from one address across any usernames, block that address for 15 minutes. Guesses from elsewhere do not lock the real user out. Behind a proxy or tunnel, set `CLIENT_IP_HEADER` (see the configuration reference) so visitors are told apart; otherwise every visitor shares the proxy's address.
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

## Importing members from a Telegram group

A member scan may include public names, usernames and numeric user IDs,
plus phone numbers only when visible to the scanning account. An
authenticated user can explicitly import the scanned member list into
their own private contact group. Scans do not automatically import,
send, join groups, or grant contact consent. IDs and names can still be
personal data and must be handled accordingly.

Importing does not prove a member is addressable from another Telegram
account. Telegram session entity caches and per-account access hashes
may differ. Users must not rely on this feature to bypass Telegram
permissions or privacy restrictions. No phone lookup is performed.

## Sending and consent

`SEND_ENABLED=false` is the safest default. Sending is a separate, user-confirmed action after reviewing recipients. Only eligible opted-in contacts can be selected. Contacts in multiple groups are deduplicated per campaign.

Do not use the system to send unsolicited bulk private messages. If Telegram answers a send with a flood wait or a spam restriction (`PEER_FLOOD`), the broadcast is paused immediately and the remaining recipients stay pending; it never keeps sending to the rest of the list. If the connection drops or times out while a message is being sent, that recipient is marked unknown (Telegram may have delivered it), never failed, and the broadcast pauses the same way; failed always means nothing was delivered. When a campaign is interrupted, an unknown delivery state is **not automatically retried**; review history and the Telegram conversation before deliberately reusing a previous broadcast.

Ticking or unticking consent on a single contact's add or edit form is also
written to the consent history (no reason is required there).

## Bulk permission changes

Operators may record verified opt-in for multiple contacts or an entire
private contact group, but **only after confirming every person individually
gave permission to receive private messages**. An evidence/source note is
required. Membership of any Telegram group, including an imported scan,
does not establish consent. The app records each changed contact's prior
and new consent value, operator, timestamp, and evidence in an audit table.

Bulk consent withdrawal does not require proof of permission and applies
to the contact across all their private audience groups. Selected-contact
bulk removal archives and disables contacts; delivery snapshots remain
available in campaign history. Pending delivery operations recheck current
consent; messages that were already sent or are in flight cannot be
retracted by revoking consent.

## Operational cautions

- Never commit or share `.env`, `data/`, session files, database backups, or recipient exports to GitHub.
- Never regenerate the encryption key for an existing account database.
- Before applying updates, take an online SQLite backup and preserve Telegram sessions and the encryption key.
- Never recreate a container during an active broadcast.
- Do not change the Docker volume mode or project name without planning a full data migration.
- If a real security issue is found, avoid sharing credentials or private records in a public GitHub issue. Contact the repository owner privately.

## License

The original project code and documentation are released under the [MIT License](../LICENSE), copyright (c) 2026 Youssef Daoud. Reuse and redistribution require retaining the copyright and permission notice. The license provides no warranty and does not grant access to other people's Telegram accounts or personal information. Third-party dependencies remain subject to their own licenses.
