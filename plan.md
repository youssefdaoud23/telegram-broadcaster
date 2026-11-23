# Telegram Broadcaster - Status and plan

## Group member-only management and phone enrichment (2026-11-22)
- [x] Group manage page displays only current group members.
- [x] Provide searchable picker to add contacts that are not in this group.
- [x] Remove only selected members, preserving contact records, history and other groups.
- [x] Search and selection are independent of Active/Inactive status.
- [x] Reimport fills only missing phone, username or Telegram numeric ID on a verified match.
- [x] Preserve nonempty identities, owner boundaries, user activation, notes and consent.
- [ ] Verify offline tests, fresh-install CI, and browser interaction.
- [ ] Deploy following backup and no-active-send check, then reimport affected contact source to backfill visible phone values.

## Imported contacts inactive-by-default (2026-11-22)
- [x] Direct Telegram scan imports create new records with enabled=0.
- [x] CSV imports create records with enabled=0 even when Enabled=yes appears in the input.
- [x] Reimported archived contacts are restored with enabled=0, consent=0 and preserved IDs/history.
- [x] Existing active or inactive contacts preserve their user-chosen status on subsequent imports.
- [x] Update import-page wording and regression tests.
- [ ] Verify complete CI tests and fresh-install Docker smoke.
- [ ] Deploy only after backup and verifying no active broadcasts.

## Contact management UI and instant filtering (2026-11-22)

- [x] Replace cramped search row with full-width search and a group filter that update as the user types/selects.
- [x] Search all imported text fields: name, username, phone, Telegram ID, notes and group labels.
- [x] Keep filter selection safe: hidden selected contacts are deselected; select-all covers only visible results.
- [x] Show username, Telegram-visible phone and numeric ID separately on Contacts and group member pages.
- [x] Replace large whole-group consent panel with Active/Inactive controls for group and individual contacts.
- [x] Add owner-scoped contact, selected-contact and group active status endpoints; do not change consent or broadcasting.
- [x] Move bulk permission controls under an advanced disclosure without removing the existing verified-permission enforcement.
- [ ] Complete new active-status and filter regression tests and fresh Docker smoke run.
- [ ] Deploy only with a verified database backup and no active sending; verify live filters and status changes.

## Archived contact re-import recovery (2026-11-22)
- [x] Reproduce the bug: removed contacts are soft-archived, so subsequent direct imports skip every matching member.
- [x] Restore matching archived contacts only on explicit scan or CSV import, reusing their original IDs and preserving previous groups/history.
- [x] Reset restored records to consent=0 and enabled=0; audit transitions from previously opted-in records.
- [x] Preserve matching identity safeguards and owner isolation; do not revive conflicting records.
- [x] Report restored counts separately from new/linked/already/skipped.
- [x] Document restoration on import pages and confirmation prompt.
- [ ] Verify 100+ offline tests, clean-install Docker CI, and review archived-contact regression coverage.
- [ ] Deploy only following a verified SQLite backup and no-active-broadcast check.
- [ ] After deployment, repeat the original group scan and explicit import. Previously removed contacts should show restored, not skipped.

## CSV / direct import reconciliation fix (2026-11-22)
- [x] Use one owner-scoped, conflict-aware contact matcher for both CSV and direct Telegram scan imports.
- [x] Link recognized existing contacts to requested groups; preserve all unrelated memberships and consent/disabled flags.
- [x] Allow manual CSV import into a named new/existing group and emit Telegram Group metadata in scan CSV exports.
- [x] Ignore inbound Enabled during CSV creation (always Inactive) and preserve existing contact settings.
- [x] Explicit re-import can safely restore archived matches without granting consent.
- [x] Stop silently truncating CSV files after 1,000 rows; reject more than 5,000 before any database writes.
- [x] Report created, linked, unchanged, skipped and group creation counts.
- [x] Clarify that group-editor contact list contains ALL workspace contacts, not only members.
- [ ] Verify full offline regression suite, fresh-install CI, and code review on a feature branch.
- [ ] Deploy to production only with a verified SQLite backup and no active sends; confirm actual group memberships before any data repair.
- [ ] Correct any previously unassigned production group contacts only after inspecting owner-scoped records and confirming the intended association.


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


## Generic self-hosted deployment (2026-11-22)

- [x] Audit Compose, Dockerfile, .env.example, CLI, and docs.
- [x] Support custom host port and bind IP, preserving existing fallback.
- [x] Default NEW installations to localhost and disabled sending.
- [x] Add Python standard-library-only configuration with a unique secret,
      no-overwrite guard, and HTTPS origin validation.
- [x] Add optional Docker-managed volume configuration for new installs.
- [x] Add /health container healthcheck.
- [x] Write generic README, installation, configuration, operations,
      privacy, troubleshooting, and legacy migration docs.
- [x] Add GitHub Actions unit and clean-install smoke test workflow.
- [x] Verify default/named-volume Compose config and offline installer.
- [ ] Observe first GitHub Actions Docker smoke on the published commit.
- [x] Publish the owner-selected MIT License with clear copyright attribution.
- [ ] Do not deploy to existing /opt installation without separate approval,
      database backup, and no-active-sends checks.


## Website template switcher (2026-11-22)

- [x] Study the provided Space Science visual reference and create an
      original nebula and mission-control look without third-party artwork.
- [x] Preserve the existing Telegram Classic design as the default.
- [x] Add a second Space Science stylesheet scoped to explicit root theme.
- [x] Add per-user persistent template preference and backward-compatible
      additive admins.site_template SQLite migration.
- [x] Add CSRF-protected choice save and safe local redirect validation.
- [x] Add Appearance page with previews and a topbar Template selector.
- [x] Keep independent day/night palette choices per template.
- [x] Add Space-only dashboard hero and responsive styling on existing pages.
- [x] Test switching, user isolation, security and migration without sends.
- [ ] Verify exact visual rendering at desktop and mobile browser sizes.
- [ ] Publish source to GitHub and verify CI success.
- [ ] Deploy to /opt/telegram-broadcaster only after validated backup,
      confirmation no campaigns are active and host Docker access.


## Space Science professional visual simplification (2026-11-22)

- [x] Remove orbit, planet, and satellite illustration from the hero.
- [x] Remove ornamental circular theme-preview marks and temporary logos.
- [x] Remove star-like backgrounds and Space-specific rotated/glowing brand.
- [x] Keep the existing Telegram logo and the Classic default appearance.
- [x] Retain the theme switcher, dark color palette, and typography-led
      alternate dashboard.
- [x] Add regression assertions that removed motifs are absent.
- [ ] Publish and confirm automated CI smoke tests.
- [ ] Apply to /opt production via normal pull/build when no send is active.


## Group scan direct-to-contacts import and bulk assignment (2026-11-22)

- [x] Retain API-provided Telegram user IDs even when username/phone are hidden.
- [x] Include Telegram User ID in the member CSV and preview.
- [x] Add explicit owner-scoped, CSRF-protected Import members directly
      action after a group scan, with same-named private group by default.
- [x] Reuse group/contacts by safe identifiers, preserve previous memberships,
      respect archived/conflicting contacts, and never set consent true.
- [x] Allow up to 5,000 scanned members to be imported directly, no sends.
- [x] Allow selecting and bulk adding/removing contacts from one group on
      Contacts page without changing other group memberships or consent.
- [x] Add unit tests for ID-only users, duplicates, collisions, isolation,
      reimport, consent, archived contacts and atomic bulk behavior.
- [x] Clarify that access hashes are sender-account-specific, and delivery
      to all selected people is never guaranteed by a successful scan.
- [ ] Publish to GitHub and verify CI clean-build tests.
- [ ] After verified backup and no in-progress broadcasts, deploy in /opt
      and verify the actual interface and scanned group import manually.


## Bulk consent and bulk contact removal (2026-11-22)

- [x] Allow selected contacts to be granted verified opt-in or withdrawn.
- [x] Allow all active members of a private group to be granted or withdrawn.
- [x] Require explicit person-by-person verification and an evidence/source
      record for opt-in grants; reject group membership as stand-alone proof.
- [x] Add owner-isolated, time-stamped per-contact consent change auditing.
- [x] Add selected-contact soft removal (archive+disable) without deleting
      historical campaign and delivery records.
- [x] Keep existing bulk membership changes independent from contact consent.
- [x] Validate user ownership, CSRF, limits, duplicates and all selected
      IDs before changes; perform updates in an atomic SQLite transaction.
- [x] Test consent grant/withdraw, whole-group effects, audit, archive
      safety, history preservation and draft eligibility without Telegram.
- [ ] Verify full CI and fresh Docker smoke after GitHub publish.
- [ ] Deploy to production only after verified backup and no sends active.

## Hardening: consent side doors, spam stop, login throttling (2026-11-23)

- [x] Production: publish port 8789 only on the private tunnel gateway
      172.19.0.1 so the Cloudflare route cannot be bypassed.
- [x] Require evidence + confirmation and record history for consent
      granted or withdrawn through the add/edit contact forms.
- [x] Stop importing CSV Opt In/Consent values.
- [x] Pause broadcasts on PeerFloodError/FloodError; no further sends.
- [x] Throttle failed sign-ins per (username, IP) and per IP, with an
      opt-in trusted client-IP header for proxies/tunnels.
- [x] Offline tests for all of the above (tests/test_hardening.py).
- [ ] Set CLIENT_IP_HEADER=CF-Connecting-IP in production .env on deploy.
- [ ] CI green, merge to main, deploy after backup with no active sends.
