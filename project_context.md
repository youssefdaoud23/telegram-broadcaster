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

## Revoked Telegram session state (2026-11-22)

Past issue: Account page displayed a stored `connected` flag even after
Telegram app terminated the session. /accounts/{id}/verify correctly detected
revocation but raised a TelegramProblem without persisting `disconnected`.
Now verify_connected detects explicit unauthorized/revoked state, persists
`disconnected` and safe last_error, and removes it from composing. Account
status is refreshed on Overview, Accounts, New broadcast and account setup
pages, with 90-second in-process success cache and 12-second check timeout.
Users can force an immediate check using POST /accounts/refresh with CSRF
or the individual Check session action. Transient network failures leave
connection state unchanged. The send worker also marks confirmed expired
sessions disconnected. Status checks never send Telegram messages or
automatically reconnect sessions. All queries respect user-owned accounts.

## Independent host location and relocation

Target production path: `/opt/telegram-broadcaster`, not under n8n.
Before the operator completes the move, the live original location is
`/root/n8n/telegram-broadcaster`. The repository includes the one-time
`scripts/migrate_to_opt.sh` guarded mover, run as host root. Compose
`./data:/data` and `env_file: .env` must move together. The original and
new directory have the same basename so Compose project identity is unchanged.
The move requires Docker socket/host filesystem rights, which Joe MCP's
current scoped workspace does not grant; do not claim deployment or restart
without verifying it. Original private .env and UID 10001 sessions are preserved
by atomic directory move, SQLite online backup, health and count checks.
No interaction with n8n's own compose project or legacy bridge.

## Historical broadcast reuse and editing

GET `/compose?from_campaign=id` opens a prefilled, non-sending composer
using the original message, connected sender, and original recipient list
filtered to the current workspace's enabled, opted-in, non-archived contacts.
HTML shows prior delivery statuses and selection presets by outcome, including
sent/unknown (never silently retried). POST `/compose/preview` creates a
new draft with independent delivery rows and a `campaign_reuses` source link,
with owner ID checks and an explicit review step. GET/POST
`/campaigns/{id}/edit` modifies only an owner's unsent draft with
`BEGIN IMMEDIATE`, server-side validation, and atomic replacement of
recipient delivery rows. Existing submitted campaigns are immutable.
Continue to keep interrupted-campaign Try again limited to pending only.

## Group member export (2026-11-22)

App adds `/groups`, `POST /groups/scans`,
`GET /groups/scans/{token}` and `GET /groups/scans/{token}/export`.
Private module `broadcaster/group_export.py` runs Telethon read-only scans
against groups enumerated from the connected, owner-scoped account's
recent dialogs (not arbitrary public groups). Regular groups and
supergroups only. Explicit CSRF before starting a scan. No Telegram
messages or group joins. Member export contains Name, Username, Phone Number
for fields visible in each Telethon User object. Phone numbers are blank
when Telegram does not return them; no phone lookup/inference. Members
without usernames are included if another field is visible. Telegram
IDs are retained and can be exported or explicitly imported into
contacts without consent. No private messages or automatic import/consent. Site users cannot read
others' scan tokens or use their Telegram accounts. Background results live only
in process memory for 30 minutes; app restarts discard them.
MAX_SCAN_USERS is 25,000, scan timeout is 240 seconds, one active
scan per user, 12 stored jobs total. Group enumeration is limited
to the 500 most recent chat dialogs and can miss older groups.
Telegram itself can hide/restrict member lists, so `complete` means
API iteration ended, not guaranteed complete group membership.
Partial/limited scans display warnings and export status.
Call out that membership is not consent for unsolicited messaging.
Do not silently start a scan against the live Telegram account.


## Group export picker layout fix (2026-11-22)

Screenshot showed two large blank group cards with centered radio buttons,
horizontal overflow, and group names pushed offscreen. Root cause:
minified `broadcaster/static/app.css` applied
`input:not([type=checkbox]):not([type=file]){width:100%;...}`
to type=radio, while the group flex row prevented radio shrink.
Fixed by excluding type=radio from text field styles and replacing
the group picker grid with a single-column, compact row layout,
explicitly sized radios, text wrapping, keyboard focus and selected
state styling. Added name search with visible count and no-match state;
filtering deselects any hidden selected radio. No Telethon/group scan
behavior or user data is changed. Includes regression tests.


## Member export fields and visibility (2026-11-22)

broadcaster/group_export.py: ExportMember(name, username, phone, user_id), kept only
in owner-scoped in-memory GroupScan.members for 30 minutes. Each entity
already returned by iter_participants supplies first_name, last_name,
username and phone, when Telegram permits access. No extra lookups are made.
Telegram user ID deduplicates rows in RAM only. Hidden phone numbers remain
blank, and visible international digits are formatted with +. Invalid
phones are omitted rather than guessed. UI preview and CSV show Name,
Username, Phone Number. API route /groups/scans/{token}/export keeps
existing auth, status and partial-result safeguards. Sanitize display names
to prevent spreadsheet formula injection. Do not auto-add group members to
Contacts or mark consent. New scans are required after deployment because
previous scans live only in volatile memory.


## Contact database decision and group segmentation (2026-11-22)

Use the existing self-hosted SQLite database as the source of truth for
contacts, groups, opt-in, account ownership, and delivery history.
Avoid introducing Neon PostgreSQL unless real multi-app sharing or high
write concurrency requires migration. Mem0 is an AI memory abstraction,
not a contact relational database; never store consent only in memory.

Additive schema tables:
- contact_groups(id,owner_id,name,created_at,updated_at),
  UNIQUE(owner_id,name COLLATE NOCASE)
- contact_group_members(group_id,contact_id,added_at),
  PRIMARY KEY(group_id,contact_id), with ON DELETE CASCADE to groups
  and contacts plus triggers that reject linking different owners.

User-management flow: /contacts/groups create/list, /contacts/groups/{id}
rename/delete/member-management, contact Add/Edit forms with multi-group
checkboxes, Contacts filtered by group_id, tagged contact list, and sidebar
shortcut. Existing contacts remain unchanged and unassigned initially.
CSV contact export/import includes a Groups column separated by semicolon
and validates preexisting names; per-import row SAVEPOINT avoids accidental
partial contact creation when group validation fails.

composer_context supplies owner-only groups and memberships. /compose
accepts group_id to prefill recipient selection; JS group checkboxes select
the union of opted-in and enabled contact IDs, never duplicate overlapping
contacts. Manual per-contact refinement remains possible. Server always
validates the selected recipient IDs at draft creation/edit, and Send
continues to require explicit review. No bulk Telegram group messages,
auto-imports, consent changes, or automated sending are introduced.
Do not confuse application contact groups with Telegram chat group-export.

Production /opt source and SQLite data are untouched until authorized
backup and deployment. No Neon credentials, new Docker service or Mem0.


## Reusable deployment package (2026-11-22)

The public repository is being made portable for fresh installations
on Docker Engine + Compose v2 hosts, including Docker Desktop.
Do not assume /opt or n8n when giving general installation guidance.
README is the landing page. docs/INSTALL.md, CONFIGURATION.md,
OPERATIONS.md, SECURITY.md, TROUBLESHOOTING.md are public help pages.
docs/MAINTAINER_MIGRATION.md is ONLY for the original n8n-to-/opt
migration case. This user's actual production remains /opt/telegram-broadcaster.

scripts/configure.py is a Python-stdlib-only new-install initializer.
It generates an exclusive .env with a random Fernet encryption key,
refuses to overwrite existing config, defaults to localhost and sending
disabled, validates host port and HTTPS public origin, and prepares data/.
Compose interpolates BROADCASTER_BIND_IP/BROADCASTER_PORT with legacy
0.0.0.0:8789 fallbacks to preserve existing private .env behavior.
Fresh .env.example sets BROADCASTER_BIND_IP=127.0.0.1. The optional
compose.volume.yaml replaces ./data with a Docker-managed volume;
selecting it on existing installs WITHOUT migrating data would create
a new, empty database. Docker image creates /data owned by 10001.
Compose healthcheck uses Python stdlib against /health.

.github/workflows/ci.yml runs offline tests and a fresh container health
smoke test with sending disabled. Do not deploy, restart or touch /opt
production as a side effect of editing the public installation guide.
No LICENSE existed at audit; the maintainer must choose a software
license before third parties have explicit use/redistribution rights.


## Public MIT License (2026-11-22)

The repository owner selected the MIT License for public reuse. The root
LICENSE uses copyright (c) 2026 Youssef Daoud. The README and security
documentation link to it. Reusers must retain the notice; the project
provides no warranty. Third-party dependency licenses remain separate.
This is a repository-only change. Do not rebuild or restart production
as a side effect of changing the license.


## Selectable website templates (2026-11-22)

User requested a more sophisticated interface and switching between
the original Telegram Web-inspired default and an optional new Space
Science theme. Inspiration URL:
https://freewebsitetemplates.com/preview/space-science/index.html
Visual cues: dark space navigation, vivid purple nebula, burgundy accents,
futuristic heading treatment. The project uses original CSS gradients,
CSS orbit shapes, and no downloaded proprietary template assets/code.

New .css: broadcaster/static/space-science.css. Current app.css and
telegram-theme.css remain intact. Root html carries data-site-template
attribute; Space CSS is scoped to "space", making Telegram Classic unchanged.
The appearance page shows a preview of both options, and the topbar
offers a quick server-side template select form. POST /settings/appearance
requires CSRF, accepts only "telegram" or "space" and restricts redirect
targets to safe local paths. GET /settings/appearance is authenticated.
The selected theme is stored in admins.site_template with additive
migration and default telegram, retrieved in core.session. Each website
user has a distinct choice. Existing browser-local light/dark toggle
remains intact; Space has its own key and defaults to the night palette.
All theme assets are local and allowed by the existing CSP. No Telegram
API calls, changes to sends, contacts, sessions, or campaign histories.
Production /opt is NOT changed by editing the staging checkout.


## Space Science ornament removal (2026-11-22)

User found the added decorative visuals too esoteric and requested removing
the logos, while retaining the option to switch website templates.
The updated optional Space Science style retains its professional dark color
palette but removes the decorative planet, satellite, orbit rings,
star-like page background dots, preview logo placeholders, and the custom
rotated/glowing brand mark. The existing Telegram paper-plane brand
symbol from the original UI is preserved unmodified. The overview hero is
text-led, using "MESSAGING, SIMPLIFIED." and a restrained linear gradient.
Telegram Classic, group selection, contact data, account sessions,
campaign logic, and theme switch state are unchanged. No database schema
change. Regression tests explicitly check removed motifs.


## Explicit Telegram scan import, bulk contact assignment, and visible IDs (2026-11-22)

Group scans retain each member's numeric Telegram user ID (where supplied
by the Telegram API) alongside name/username/phone in memory. The group
CSV now includes a fourth column, Telegram User ID. A missing username
and hidden phone do not imply Telegram can never address the user: ID
resolution also needs that account's entity access hash/session cache.
The original scanning account should be used for legitimate opted-in
messages to ID-only contacts. Other senders may fail, and Telegram can
still reject messages. Do not infer phone numbers or claim 100% delivery.

On /groups/scans/{token}, an explicit CSRF-protected form posts to
/groups/scans/{token}/import with an editable suggested contact-group name.
broadcaster/group_import.py validates owner, completed/partial scan state,
group name and max 5000 scanned members, then creates/reuses same-named
private contact group and imports members with available identifiers.
Existing contacts are matched safely across Telegram ID/username/phone;
conflicting identifiers are skipped; explicit re-import restores matching
archived contacts using the original IDs and preserved group links, with
archived=0, enabled=1, consent=0 and opt-in reset audited. Active contacts
keep consent and manually edited details. Missing numeric IDs
can be filled on safely recognized existing contacts. Each member import
uses an SQLite savepoint; no Telegram API requests or messages. Newly
created contacts have consent=False and enabled=True. No opt-in is inferred
from Telegram group membership. IDs are account-specific in practical
resolvability, since access hashes differ between Telegram accounts.

Contacts page has a new select-all/bulk checkbox column and an action
form POST /contacts/bulk-group to add/remove selected members to/from
one owner-scoped private group. core.bulk_update_group_members validates
up to 5000 IDs and owner before any writes, preserves all other group
links and contact consent. Group editor's existing bulk membership
replacement remains separate. CSV Contacts import also supports scan
headers Phone Number and Telegram User ID; exported string fields have
spreadsheet formula safety treatment. No new DB tables or external apps.
Production /opt not modified by developing this staging checkout.


## Import defaults: Inactive until manual activation (2026-11-22)

Direct Telegram group scans and the Contacts CSV importer create new contacts
with enabled=0, regardless of an inbound Enabled=yes column. An explicitly
reimported archived contact is restored with archived=0, enabled=0 and
consent=0; original record ID, links and history remain preserved. Reusing
an existing, unarchived contact to add group membership does not overwrite
its enabled or consent settings. The existing single, selected, and group
Active/Inactive controls are used to enable contacts after import.
Neither importing nor activation grants verified private-message permission;
the sending eligibility rules remain unchanged.

## Contact UI: live filtering, phones, and activation switches (2026-11-22)

Contacts now renders the entire current user's active contact dataset,
not just server-prefiltered rows. An instantaneous client-side search
filters visible name, username, phone, Telegram user ID, notes, and group
labels. Group dropdown filtering is also immediate and avoids reloads;
changing filters clears any selections hidden by the new filter. The
search result count and select-all reflect only visible contacts. The
contact table shows phone and Telegram ID separately, with "Not available"
when Telegram does not provide a phone. No extra Telegram requests occur.

Group manage page replaces the prominent whole-group consent form with
Active/Inactive actions for individual contacts and the whole group;
phones and Telegram IDs are also visible in member rows. Owner- and
CSRF-checked POST /contacts/{id}/status,
/contacts/groups/{group_id}/status, and /contacts/bulk-status change only
the enabled field, not verified consent, memberships, or history. The
Contacts page retains advanced messaging-permission controls behind a
disclosure; group or username membership alone is not assumed to be
permission. Existing sending eligibility still requires enabled=1 AND
consent=1 AND archived=0, so activating imported nonconsenting contacts
does not automatically make them messageable.

## Bulk contact consent and bulk soft removal (2026-11-22)

Requested: grant/withdraw consent for multiple selected contacts or all
members of a contact group, plus removing many contacts at once.

Core adds table contact_consent_events with owner_id,actor_id,contact_id,
previous/new consent, evidence/source, created_at and owner/time index.
Bulk consent is transactionally checked against authenticated user
ownership and archived status. Direct grants require an explicit checked
attestation and a meaningful evidence/source note (minimum 10 chars,
max 500); membership in a Telegram group alone is rejected as a basis.
Withdrawal needs no attestation and records a default reason when blank.
Both actions log per-contact changes only, not redundant no-ops.
Whole-group operations read current active memberships, and contact
consent is global across all groups. Selected contact actions validate
1..5000 IDs; whole-group operations are capped at 20000 members.
No importing, membership, draft creation or sending is triggered.

POST /contacts/bulk-consent supports scope=selected with explicit
contact IDs or scope=group with validated owner group_id. The Contacts
page shows evidence/verification inputs and selected-contact Grant/
Withdraw controls. The Contact group manage page has whole-group
Grant/Withdraw forms. GET /contacts/consent-history shows recent
owner-scoped changes; pre-feature consent has no retroactive audit.
POST /contacts/bulk-remove archives/disables checked contact records
transactionally, rather than deleting campaigns or delivery history.
The existing send loop already rechecks contact consent before each
delivery; in-flight messages cannot be retracted.

Existing bulk group assign/remove remains unchanged; JS mirrors the
selected checkboxes to the consent and archive forms when submitting.
The new audit table is additive and created on normal app startup.
Development uses a temporary git checkout; never edit live /opt
data or restart production without separate verified backup.

## Hardening (2026-11-23)

POST /contacts and POST /contacts/{id}/edit set consent from the
checkbox with no reason required (maintainer decision: consent is given
with the number) and write a contact_form row to contact_consent_events
for any grant or withdrawal. CSV Opt In/Consent still imports as before.
telegram.dispatch
treats errors.FloodError and errors.PeerFloodError alike: mark the
delivery blocked, pause the campaign, leave the rest pending. An error
after send_message starts that is not a definite Telegram refusal
(connection drop, timeout, RPC 500/503) marks the delivery unknown and
also pauses; the outer handler turns a leftover sending delivery into
unknown. failed therefore always means nothing was delivered.
Sign-in throttling lives in login_failures(username, ip); web.client_ip
reads CLIENT_IP_HEADER only from private/loopback peers. Failed sign-ins
redirect with the attempts left, and a locked address sees the minutes
remaining (core.login_status). In production,
Cloudflare reaches the app via http://172.19.0.1:8789 (the tunnel
network's gateway) and BROADCASTER_BIND_IP=172.19.0.1.

## Code health (2026-11-23)

core.database() returns a connection whose with-exit commits or rolls back
and then closes. broadcaster/csv_import.py holds CSV parsing (read_rows)
and per-row import with savepoints (import_rows -> CsvImportResult); the
POST /contacts/import route only reads the upload and builds the notice.
telegram.confirm_identity(client, row, expired=, mismatch=,
revoke_on_mismatch=) is the single session/identity check. Docker installs
requirements.lock; tests install requirements-dev.txt.
