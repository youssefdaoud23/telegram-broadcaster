# Telegram Broadcaster - Status and plan

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
## Optional recipient limit (2026-11-22)
- [x] Remove default three-recipient limit and arbitrary hard cap of 100 from the application.
- [x] Leave SEND_MAX_RECIPIENTS optional for administrators who prefer their own cap.
- [x] Keep consent, manual review/Send, stop-on-FloodWait, and idempotent retry behavior.
- [x] Cover unlimited drafts and retries, including 1,005-recipient browser form, with offline regression tests.
- [ ] Rebuild broadcaster image only when no active messages are being sent; verify actual UI status and configuration.

## Multi-user workspaces / password management (2026-11-22)

- [x] Design isolated private workspaces on one hosted broadcaster service.
- [x] Implement additive legacy migration assigning prior records to the
      oldest administrator (installation owner), with idempotent owner_id columns.
- [x] Scope all contact/account/campaign routes, imports, exports and API access
      by authenticated user ID, not just UI filtering.
- [x] Ensure Telegram delivery worker verifies sender and recipient workspace
      ownership and active account status before each send.
- [x] Add owner-only User management (create/reset/disable/enable users).
- [x] Add Change password UI for all users, requiring current password and
      revoking web sessions, without touching Telegram sessions.
- [x] Test old-schema migration and cross-user ID/URL access and writes offline.
- [ ] Before production deployment, use SQLite online backup on the running
      database, verify a copy exists, and ensure no campaigns are sending.
- [ ] Rebuild only broadcaster, verify login and legacy data preservation.
- [ ] Create a dedicated test user, verify they start with empty contacts
      and accounts and can independently connect Telegram (no message sends).
- [ ] Restrict publicly reachable port 8789 at firewall / Cloudflare origin.

## Telegram authorization status (2026-11-22)

- [x] Diagnose stale connected flag after Telegram app revokes session.
- [x] Persist disconnected after explicit unauthorized/revoked checks.
- [x] Automatically check connected accounts on Overview, Accounts,
      New broadcast, and account setup pages, with cached success checks.
- [x] Add CSRF-protected Check all sessions button to force a new check.
- [x] Keep temporary network failures from incorrectly revoking valid logins.
- [x] Drop revoked accounts from sending account selection immediately.
- [x] Reject Send on old drafts whose Telegram sender is now disconnected.
- [x] Mark disconnected on revoked sessions discovered by delivery worker.
- [x] Verify offline regression tests with no live Telegram requests.
- [ ] Rebuild only broadcaster when no campaign is actively running, then
      confirm a previously revoked account becomes Disconnected in UI.
- [ ] Confirm reconnect code request can authenticate a new Telegram session.

## Independent /opt deployment and broadcast management (2026-11-22)

- [x] Verify source compose uses relative .env and persistent ./data paths.
- [x] Verify current Joe MCP scope lacks host /opt and production Docker
      project permissions. Avoid a partial move of live data.
- [x] Create guarded host-root scripts/migrate_to_opt.sh with no-active-send
      check, timestamped SQLite online backup + integrity check, immutable
      data-directory move, minimal broadcaster-only restart, health/count checks
      and attempted rollback on failure.
- [x] Add Reuse and edit actions for history and campaign details, including
      finished, interrupted and failed broadcasts.
- [x] Add outcome-based recipient-selection shortcuts and duplicate-send warning.
- [x] Add source-to-new-draft audit links and owner-isolated queries.
- [x] Add in-place editing for unsent drafts only, with atomic validation.
- [x] Preserve manual preview/Send and narrower pending-only Try again.
- [ ] Run migration script on authorized host terminal, with original container
      running, no active send, and empty /opt target. Verify PASS output.
- [ ] Confirm public site after move, Telegram account status, user workspaces
      and all history (without sending actual messages).
- [ ] Grant Joe MCP /opt workspace access separately if future maintenance
      through Joe MCP is desired.

## Telegram group member export (2026-11-22)

- [x] Add Group export page to sidebar, with owner-scoped connected account
      selection, recent regular groups and supergroups, and no direct-chat scans.
- [x] Discover groups using authenticated Telethon iter_dialogs rather than
      arbitrary group IDs or joining groups.
- [x] Launch read-only member scan with bounded concurrency, progress, and
      a 240-second timeout. Detect limited and partial responses.
- [x] Export accessible non-bot group members as CSV with Name, Username
      and Phone Number fields, leaving missing Telegram fields blank. Include
      name-only members; never infer hidden phone numbers or import contacts.
      Telegram user IDs remain internal for deduplication only.
- [x] Protect all scans and CSV downloads with workspace ownership, and
      require CSRF on scan creation. Do not add contacts or send messages.
- [x] Hold results only in memory for 30 minutes; no migration or new tables.
- [x] Add isolated offline tests with simulated Telegram accounts and groups.
- [ ] Sync published source to /opt/telegram-broadcaster, rebuild its container
      when no broadcasts are active, and verify the Group export UI.
- [ ] With an explicitly selected real group, manually verify Telegram
      returns the participants that account is allowed to view.


## Group export picker UI correction (2026-11-22)

- [x] Reproduce and diagnose full-width radio selectors in group list.
- [x] Exclude type=radio from global text-input sizing rule.
- [x] Render compact one-column group rows with visible names/types.
- [x] Add client-side search and selected-hidden-group protection.
- [x] Add regression checks for CSS, HTML and group list data rendering.
- [ ] Pull and rebuild production only when no campaign is sending;
      confirm normal group list appearance in desktop and mobile browsers.


## Group member CSV fields (2026-11-22)

- [x] Include Telegram-visible first and last name, username, and phone.
- [x] Include members without a username if they have a visible name/phone.
- [x] Normalize valid international phone digits and leave hidden values blank.
- [x] Deduplicate by Telegram account user ID; no phone lookups or added API calls.
- [x] Show name, username, and phone columns in member preview and CSV.
- [x] Prevent spreadsheet-formula execution in attacker-controlled display names.
- [x] Preserve read-only/no-send behavior, workspace isolation and CSV authorization.
- [x] Run offline tests for visible/hidden phones and name-only members.
- [ ] Deploy to /opt/telegram-broadcaster when no sends are active.


## Contacts database and many-to-many groups (2026-11-22)

- [x] Retain self-hosted SQLite as the canonical contact database. No Neon
      migration or Mem0 memory layer is necessary for the current workload.
- [x] Add owner-scoped contact_groups and contact_group_members with indexes,
      foreign keys and SQL triggers that reject cross-owner links.
- [x] Provide group creation, rename, deletion, and bulk membership editing.
- [x] Assign a contact to multiple groups from Add/Edit Contact pages.
- [x] Show group tags and filtering on Contacts, with sidebar navigation.
- [x] Add Groups column to contact CSV export and support importing existing
      group names separated by semicolons with per-row savepoint rollback.
- [x] Preselect members using /compose?group_id=... and support combining
      groups by set union before manual per-recipient refinement.
- [x] Preserve archived/disabled/nonconsenting contact restrictions, owner
      isolation, draft review, and explicit user-initiated Send.
- [x] Test group overlap, CSV roundtrip, deletion history preservation,
      CSRF, database integrity and user isolation completely offline.
- [ ] Before deployment: back up the live SQLite database, verify no active
      send, pull the new GitHub commit into /opt/telegram-broadcaster, and
      rebuild only broadcaster.
- [ ] Verify contact counts and group UI after schema migration, without
      sending any real Telegram messages.
