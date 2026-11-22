# Telegram Broadcaster — Status and plan

- [x] Inspect canonical GitHub repo and initialize feature branch.
- [x] Add isolated Docker environment, safe configuration template, and local-only port.
- [x] Add local SQLite schema for admins, sessions, accounts, contacts and deliveries.
- [x] Add encryption, scrypt password hashing, and administrator CLI.
- [x] Add Telegram API-ID/API-Hash onboarding and Telegram code / optional 2FA in website.
- [x] Support distinct Telegram session per connected account.
- [x] Add contacts CRUD, CSV import/export, opt-in, and disabled state.
- [x] Add compose/preview/send workflow, per-recipient delivery tracking, controlled backoff, interruption safety.
- [x] Add responsive dashboard, accounts, contacts, compose, and history templates.
- [x] Add offline tests and GitHub Actions CI.
- [ ] Complete all security, unit, and UI smoke tests (unit tests currently pass after scrypt fix).
- [ ] Fix defects found during full web app testing.
- [ ] Merge feature branch to main and verify commit.
- [ ] Git pull --ff-only in independent production checkout.
- [ ] Install protected configuration and admin, deploy new Docker service on 127.0.0.1:8789.
- [ ] Verify HTTP health and protected web pages without sending messages.
- [ ] Run approved small live send test only after explicit recipient confirmation.
- [ ] Later: account removal, public HTTPS domain protected by access proxy, optional direct Google Sheets sync, CI/CD deploy.

Operational boundary: do not touch existing n8n or legacy personal Telegram bridge.
