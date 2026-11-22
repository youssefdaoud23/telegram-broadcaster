# Project Context

## Objective

A secure web app replacing the cumbersome n8n-based Telegram broadcast UI, with local CRUD contact database, multi-account Telegram user sessions, guided API credentials setup, messaging composer, and delivery history.

Canonical GitHub: https://github.com/youssefdaoud23/telegram-broadcaster (PUBLIC — no secrets or personal data).
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
- SEND_ENABLED=false default; SEND_MAX_RECIPIENTS=3 default.
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