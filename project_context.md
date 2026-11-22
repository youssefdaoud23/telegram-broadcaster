# Project Context

## Objective

A secure web app replacing the cumbersome n8n-based Telegram broadcast UI, with local CRUD contact database, multi-account Telegram user sessions, guided API credentials setup, messaging composer, and delivery history.

Canonical GitHub: https://github.com/youssefdaoud23/telegram-broadcaster (PUBLIC - no secrets or personal data).
Development branch: feature/initial-dashboard.

## Existing services: preserve

- n8n at https://automation.youssefdaoud.cloud
- Legacy n8n personal Telegram workflow ID i0BtDufdmHxuu4Bw
- Legacy personal Telethon bridge at /root/n8n/telegram-personal-bridge
- Legacy Telegram bot workflow ID WLglsgf37xqnxa8Q
- Google Sheet "Telegram broadcast" (optional CSV migration source)
- Previously configured personal account in the legacy bridge: @JoeD313

Do not change or restart any existing service for the new application.

## Architecture

- Independent FastAPI container, host port 8789:8080 following the same direct host-port publishing pattern as existing n8n. Route from Cloudflare tunnel via public host IP:8789. Direct origin port needs firewall restrictions.
- App SQLite database in /data/broadcaster.sqlite3, protected storage in /data/telegram_sessions.
- Account API hashes and phone encrypted using APP_ENCRYPTION_KEY provided only in protected server-side .env.
- Website admin created by CLI with scrypt password hash.
- Sessions use opaque hashed bearer tokens, HttpOnly SameSite Strict cookies, and CSRF on authenticated POST forms.
- Separate Telegram sessions for each account; API login code and 2FA supplied through protected UI, never stored.
- Contacts must be active and opted in to become sendable.
- Campaign is drafted first, previewed on a detail page, then sent after pressing one Send button.
- SEND_ENABLED=false default; SEND_MAX_RECIPIENTS is optional (unset/blank/0/unlimited means no application cap).
- Background delivery tasks are single-worker, idempotent per campaign; uncertain sends are never retried automatically.
- Legacy Google Sheet is not a live sync dependency. CSV import/export provides basic migration.

## Development and operations

- GitHub is canonical; build on feature branch, run tests, merge, then server git pull --ff-only.
- Never commit .env, session files, personal records, contact CSV, phone numbers, or encryption keys.
- Before website deployment validate security and initialize admin; keep site local-only until HTTPS/access control is approved.
- Do not send Telegram messages unless user explicitly authorizes a live test and the recipient list is confirmed.
- Update plan.md and this file after changes.

## UI theme
Telegram Web-inspired light/dark mode is implemented in the separate telegram-theme.css and telegram-theme.js assets, loaded by the shared base template. The compose page shows a local-only live message bubble preview and Telegram-style recipient list; theme preference is browser-local. This is presentation logic only and does not change sending permissions, authentication, recipient validation, databases, or Telegram sessions.

## Safe retry design

Interrupted broadcasts expose **Try again** in History and Details only if
there are eligible pending recipients; the POST requires the existing
administrator session and CSRF token. `core.create_retry_draft()` holds an
immediate SQLite transaction, checks the source state, filters deliveries to
pending plus current enabled/consented/non-archived contact state, enforces
SEND_MAX_RECIPIENTS, and creates a new draft with the original sender and
message. `campaign_retries` maps each source to one retry draft to make
repeated clicks idempotent; it does not modify the original delivery records.
Nothing sends until the administrator separately reviews and clicks Send.
Never retry unknown, sending, failed, blocked or sent delivery results
automatically. Source database and Telegram session files stay local and
must not be published. An optional positive SEND_MAX_RECIPIENTS sets a local per-broadcast cap. Without it, no app cap applies; Telegram anti-spam/rate limits and practical HTTP processing limits still apply.
## Recipient limit configuration (2026-11-22)
An arbitrary default of three and a hard 100-recipient limit were removed. The setting is optional: missing/empty/0/unlimited disables the local cap, positive integer enforces a custom cap, invalid values fail on startup. No change to opt-in, preview, Telegram flood handling, or actual send trigger. Large HTML forms are parsed with a bounded max_fields=20000 and authenticate before parsing. Future large-scale delivery would benefit from durable worker queues rather than one in-memory sending task.

## Multi-user isolation and administrator settings (2026-11-22)

Legacy SQLite `admins` rows now have `role` (`owner` or `user`) and
`active`. Existing `accounts`, `contacts` and `campaigns` rows receive
`owner_id`; the oldest existing administrator becomes the installation owner
and receives all existing data. All other old admin accounts remain logins but
are regular users with no assigned historical data. Migration is additive and
idempotent. All owner filters must be enforced in SQL and write operations:
account lookup and Telegram login routes, contact CRUD/import/export, sender
selection, campaign creation, previews, history, retry and Send, plus the
authenticated campaign status API. The delivery worker must verify campaign
sender ownership and active user plus contact ownership before send.

Installation owner alone uses /users to create/disable/enable regular users,
and reset their passwords after owner-password reauthentication. All users
have /settings/password for an old-password-verified password change.
Both changes revoke the target user's existing web sessions, not Telegram
sessions. Never expose other users' account credentials/contact data in the
owner UI. Owner cannot disable their own login. A disabled user's in-flight
message may finish, but further deliveries require an active workspace check.

App-level data isolation shares the encrypted SQLite database, Fernet key
and process; it is not physical security separation between OS accounts.
The GitHub repository remains public; secrets, Telegram sessions, contacts,
passwords and DB files never belong in Git.

No automatic production DB migration until user performs database backup
and rebuilds the container. On upgrade, take a consistent SQLite backup with
the container's sqlite3.Connection.backup API, leave the live .env and data
volume untouched, and only recreate broadcaster when no sends are active.