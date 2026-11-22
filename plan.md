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

- [x] Build Telegram Web-inspired light/dark UI and chat composer preview (presentation only).
- [ ] Confirm server Docker image rebuilt and HTML/CSS visible through public hostname after git pull.

## Send endpoint incident

- [x] Found real HTTP 500 with `RuntimeError: no running event loop` in
  POST `/campaigns/{id}/send`, including campaign `QKgERa1BuzO_jrZoTp7Aso2D`.
- [x] Change FastAPI Send endpoint to asynchronous so it can schedule Telethon
  tasks on the server's running event loop.
- [x] Limit orphaned-campaign recovery to server startup; CLI operations no
  longer interrupt currently running broadcasts.
- [x] Add offline regression test with a stubbed sender (no real messages).
- [ ] Rebuild/recreate broadcaster container after updating code; then inspect
  interrupted campaign statuses. No automatic resend.
- [ ] Confirm future approved small test delivery via per-recipient results.
- [ ] Fix cosmetic missing favicon (HTTP 404) if desired.
- [ ] Review unexpected public exposure of port 8789 and secure direct origin
  access before relying on short administrator passwords.

## Safe retry and recipient limit (2026-11-22)

- [x] Add an idempotent **Try again** action to interrupted campaigns in History and Details.
- [x] Retry only currently opted-in, enabled recipients with the exact delivery
      state pending; never automatically retry sent/unknown/sending/failed/blocked.
- [x] Create a separate reviewable draft with explicit Send as a later action.
- [x] Preserve original campaign history and link retry draft to original via
      a new `campaign_retries` table (non-destructive migration).
- [x] Clarify the default 3-recipient per-broadcast safety limit and document
      changing SEND_MAX_RECIPIENTS in the private server .env (hard cap 100).
- [x] Add offline tests for retry eligibility, idempotency, CSRF and limits.
- [ ] Rebuild only broadcaster container when there are no active sends.
- [ ] Confirm retry button and preview of a previously interrupted broadcast.