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
- [x] Add offline tests; run them locally (GitHub Actions workflow removed at user request).
- [x] Run local Python compilation, 9 offline unit/UI smoke tests, JavaScript syntax checks, and template parsing.
- [ ] Fix defects found during full web app testing.
- [x] Fast-forward tested feature branch to main and verify the server git pull --ff-only.
- [ ] Git pull --ff-only in independent production checkout.
- [x] Generate protected .env with a fresh encryption key in the server checkout; validate Docker Compose configuration.\n- [ ] Fix data/ ownership (UID 10001), create administrator interactively via SSH, and deploy Docker service on 127.0.0.1:8789.
- [ ] Verify HTTP health and protected web pages without sending messages.
- [ ] Run approved small live send test only after explicit recipient confirmation.
- [ ] Later: account removal, public HTTPS domain protected by access proxy, optional direct Google Sheets sync.

Operational boundary: do not touch existing n8n or legacy personal Telegram bridge.

## Current deployment blocker (2026-11-22)
The new server directory is /root/n8n/telegram-broadcaster and has the verified main branch. The 0600 .env exists with SEND_ENABLED=false, and localhost port 8789 is available. `data/` was created as root:root mode 0700; the Docker app runs as UID 10001, so an SSH operator must run `chown 10001:10001 data` before first startup. The Joe MCP privileged terminal is not authorized for this directory. No container was started and no Telegram messages were sent. GitHub Actions workflow was removed per user request; tests are run locally.

- [x] Diagnose public hostname origin refusal: broadcaster runs only at 127.0.0.1:8789.
- [x] Replace extra Docker-network approach with same direct host-port exposure as n8n (`8789:8080`).
- [ ] Recreate only broadcaster service to apply new port mapping, then verify health from the public origin IP.
- [ ] Set Cloudflare Tunnel origin for telegram-broadcaster.youssefdaoud.com to http://72.61.177.85:8789 and verify HTTPS login.
- [ ] Apply firewall-origin restrictions; Cloudflare Access alone does not protect the direct IP:port.
