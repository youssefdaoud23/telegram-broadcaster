# Telegram Broadcaster

A self-hosted, multi-user Telegram messaging dashboard. Run it on a Docker-capable Linux server, NAS, VPS, or Docker Desktop (macOS/Windows). No n8n, Cloudflare, Neon, Mem0, or external contact database is required.

**Important:** Sending uses your own Telegram account, not a Telegram bot. Telegram restrictions and recipients' consent still apply. The app starts with sending **disabled**.

## Quick start

Requirements: **Docker Engine with Compose v2**, Git, and Python 3.9+ (Python is used only for the initial configuration helper; the application runs in Docker).

```bash
git clone https://github.com/youssefdaoud23/telegram-broadcaster.git
cd telegram-broadcaster
python3 scripts/configure.py
docker compose build broadcaster
docker compose run --rm --no-deps --user root --cap-add CHOWN broadcaster sh -c 'chown 10001:10001 /data'
docker compose run --rm -it --no-deps broadcaster python -m broadcaster.cli create-admin
docker compose up -d
docker compose ps
```

On a desktop, open **http://localhost:8789**. On a remote host, use an SSH tunnel:

```bash
ssh -L 8789:127.0.0.1:8789 YOUR_USER@YOUR_SERVER
```

Then open http://localhost:8789 in your local browser. By default, **fresh installations bind only to localhost**, not the public Internet. Do not expose the login page directly over HTTP.

For Windows, use `py -3 scripts/configure.py` in PowerShell and run the same Docker Compose commands without Bash-specific syntax. On Docker Desktop, the optional [named-volume install](docs/INSTALL.md#docker-managed-storage-optional) avoids host file ownership issues.

To set up an HTTPS domain behind an authenticated reverse proxy:

```bash
python3 scripts/configure.py --public-url https://telegram.example.com
```

Use that option instead of the default helper command. The helper preserves existing private configuration and refuses to create a new key over nonempty data. It enables secure cookies but does not install a reverse proxy or TLS certificate.

### First login

1. Sign in using the administrator created above. The first administrator becomes the installation owner.
2. Open **Telegram accounts** and connect an account using your own API ID and hash from [my.telegram.org/apps](https://my.telegram.org/apps). Complete Telegram's login code and optional two-step password.
3. Add contacts under **Contacts**, mark only people who opted in, and organize them under **Contact groups** if desired.
4. Create and **review** a broadcast draft. Sending remains disabled until the server operator explicitly sets `SEND_ENABLED=true`.

## What it includes

- **Separate user workspaces:** Installation owner creates users, disables accounts, and resets passwords. Each user has isolated Telegram senders, contacts, contact groups, campaigns, and delivery history.
- **Contact database:** Built-in SQLite with CSV import/export and opt-in status. A contact can belong to several private audience groups. Overlapping groups never produce duplicate deliveries in one campaign.
- **Telegram account management:** Encrypted API credentials, separate Telegram sessions, status verification, and reauthentication after session revocation.
- **Broadcast controls:** Compose, preview, confirm, and send individually to opted-in contacts. Reuse a previous broadcast as a new editable draft, edit unsent drafts, and perform cautious pending-only retries.
- **Telegram group export and direct import:** Scan a group you can access, view member names, usernames, visible phone numbers and Telegram user IDs, or explicitly import the scanned members straight into a same-named private contact group. Hidden phone numbers remain blank; importing never grants messaging consent.
- **Operational safeguards:** One application worker, sender authorization checks, flood-wait handling, no automatic resends after interruptions, and a health endpoint at `/health`.

A **contact group** is an application-defined list for individual messages. It is different from a **Telegram group**, whose members may be available through the read-only group export page.

## Import scanned Telegram group members directly into Contacts

Open **Group export**, choose your connected Telegram account, and scan a
group. The results page offers **Import members directly** as well as
Download CSV. The import button proposes the Telegram group's original name
for a private application contact group. You may edit the name; an existing
group with that name is reused. There is no download/re-upload step.

Each new contact stores available name, username, phone and Telegram user ID
(up to 5,000 members per direct import). Contacts already in the database
are linked to the group without duplicating them or deleting their previous
group memberships. Conflicting identifiers are skipped, while an explicit
re-import safely restores matching previously removed contacts using their
original database record and group memberships. Restored contacts return as
Inactive with consent reset to No consent; any previous opt-in is recorded as
withdrawn in the audit history. Group scans with partial results remain
explicitly labeled incomplete.

**Every newly imported or restored contact starts Inactive** regardless of
an Enabled column in the incoming CSV. You can activate them individually or
in bulk afterward. Re-importing an existing contact does not override a
previously chosen Active/Inactive status. Messaging permission remains a
separate requirement: direct Telegram scans never grant consent, and a Telegram
membership alone does not establish opt-in. CSV opt-in values are retained
separately from Active status. Existing contacts keep their
recorded consent and enabled status. A shared Telegram group is not consent to receive
private promotional messages. The app never sends or drafts messages as
part of importing.

**Missing username or phone does not always mean an unusable member.**
If an earlier import stored a contact without a phone number or username,
re-importing a scan or CSV containing that verified identifier now fills the
**missing** field on a securely matched existing contact. An existing phone
number or username is never overwritten, and conflicting identifiers are
skipped. A phone that Telegram does not expose cannot be recovered by import;
the CSV export likewise leaves it empty. A Telegram numeric user ID can
identify them if the same sending account's
Telethon session has encountered the member and retained their entity
access hash. Use the connected account that scanned the group when sending
to ID-only recipients. Telegram can still deny access or restrict messages,
and no software can guarantee delivery to all scanned members. An ID alone
is not a phone number and cannot be used to reveal private phone numbers.

To reorganize existing contacts without individually editing each one,
open **Contacts**, tick the people you need, select a group and use
**Add selected** or **Remove selected**. This affects only that group's
membership, not contacts' other groups or consent. Opening a particular
group now shows **only that group's members**, with a separate searchable
**Add contacts** picker for contacts outside the group. Removing selected
members affects only this group and never deletes their contact records.
There is no group-wide membership replacement when using these actions. A broadcast selects only opted-in,
enabled recipients and requires the usual explicit preview and Send action.

Exported group CSV files now have a fourth column, **Telegram User ID**.
The Group export CSV now includes **Telegram Group**, allowing a later CSV
import to recreate or reuse the source contact group without manually assigning
each member. The **Contacts** CSV importer also offers an optional **Import into
contact group** name for older CSVs without group metadata. Both import paths
safely reuse existing matches and add missing group associations, without
changing existing consent, enabled status, notes, or unrelated memberships.

CSV imports always create new contacts as Inactive, even if a supplied
`Enabled` column says `yes`. The column remains in exports for backup purposes.

CSV files can contain up to 5,000 contact rows (1 MB), with an explicit rejection
instead of silent truncation. The import result reports created, linked,
unchanged, and skipped records. An existing **Groups** column continues to refer
only to already-created groups, while the **Telegram Group** column or the
manually entered group name can create a new group. The original direct-import
workflow remains preferred for new scans.

## Live contact search and Active/Inactive controls

The **Contacts** page displays a full-width contact table with separate
**Telegram username**, **Phone number**, and **Telegram ID** columns. Phone
numbers are displayed only when Telegram or a manual import supplied one;
hidden numbers are never inferred or retrieved. Search filters name,
username, phone, Telegram ID, notes, and group names as you type, without
a page reload. Changing the group dropdown immediately updates results.
Selecting all applies only to contacts currently shown; filtered-out
selections are cleared for safety.

A contact's **Active/Inactive** switch and the group-wide status buttons
control its `enabled` flag. The selected-contact toolbar has the same
actions. The change is owner-scoped and does not modify group memberships,
message history, or the separate verified messaging permission requirement.
An Active contact may still be ineligible for private broadcasts until
the required permission is documented.

## Bulk consent, withdrawal, and contact removal

You can manage many contacts at once without editing them individually:

1. Open **Contacts**, optionally filter by a contact group, select several
   contacts or use **Select all visible**.
2. To manage messaging permission, expand **Advanced messaging permission
   controls**, enter a source/evidence note, and choose **Record verified
   opt-in** or **Withdraw consent**. Granting consent requires an
   explicit checkbox confirming that *each selected person* has already
   agreed to private messages. Simply joining a Telegram group or importing
   the group is not evidence of messaging consent.
3. To remove contacts, choose **Remove selected contacts** and confirm.
   This **archives and disables** contacts instead of permanently deleting
   their campaign and delivery history. Archived contacts are excluded from
   future broadcasts.

To make an entire group Active or Inactive, open **Contact groups**,
choose the group, and use its **Group Active status** controls.
These controls do not establish messaging consent. For verified
messaging-permission changes, select the relevant contacts in the
Contacts table, open **Advanced messaging permission controls**, and
provide the required direct opt-in evidence.

**View permission history** under the advanced controls lists recent bulk changes, the
operator who made them, timestamps, and the recorded evidence or withdrawal
reason. Historical consent values entered before this feature do not gain
retroactive audit records. New records are owner-scoped in SQLite.

These operations never send Telegram messages. Both campaign creation and
the sending worker check current consent before delivering. Revocation
prevents future pending deliveries; it cannot recall a message already
sent or stop one already in flight.

## Website templates and appearance

The interface includes two selectable designs. **Telegram Classic** is the
original design and remains the default for new and existing users.
**Space Science** is an original, professional dark theme with
subtle gradients, restrained navigation, status highlights, and a clean
text-focused dashboard banner. Decorative planets, orbit marks and
symbolic motifs have been intentionally excluded. The visual inspiration is the
[Space Science template](https://freewebsitetemplates.com/preview/space-science/index.html);
no stock artwork, downloaded images, external stylesheets, or code from that
site are included in this repository.

To change the look while signed in, open **Appearance** in the sidebar,
compare the two previews, and select a template. You can also use the
**Template** dropdown and Apply button in the top bar on any page. The choice
is saved against **your own website account** and follows you across
browsers and devices. Other users retain their own choices. Switching is
visual only; it does not change Telegram connections, contacts, group
membership, permissions, campaigns, or sending status.

The existing moon/sun toggle remains available for light/dark appearances
within either template. That color choice is saved locally in each browser
and independently for Classic and Space Science. Space Science defaults
to its night palette unless the user selected another palette.

The appearance migration adds only the `admins.site_template` preference
column, defaulting to `telegram`. Existing websites continue to show
the original design until someone explicitly chooses Space Science.
No external CDN, image host, API, or additional Docker service is required.

## Deployment and help

| Guide | Purpose |
|---|---|
| [Install anywhere](docs/INSTALL.md) | Linux, macOS, Windows, VPS, NAS, localhost, HTTPS proxy, Docker volume options |
| [Configuration](docs/CONFIGURATION.md) | Environment variables, encryption keys, ports, cookie security, sending controls |
| [Operations](docs/OPERATIONS.md) | Updates, backups, restore, health checks, admin password reset |
| [Security and privacy](docs/SECURITY.md) | Public exposure, consent, credential protection, safe data handling |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Common startup, database, proxy, Telegram and delivery issues |
| [Existing installation migration](docs/MAINTAINER_MIGRATION.md) | Legacy n8n-to-/opt migration only; not for new installations |

## Architecture and persistence

```text
Your browser
   |
   | HTTP on localhost, or HTTPS through your own reverse proxy
   v
Docker Compose -> FastAPI + Telethon (one worker)
                     |
                     v
                   /data
                   |- broadcaster.sqlite3 (users, contacts, groups, campaigns)
                   |- telegram_sessions/ (sensitive Telegram account sessions)
```

The default Compose configuration binds `./data` on your host to `/data` in the container. It can alternatively use a Docker-managed named volume. The Docker image runs as non-root UID **10001**, with a read-only root filesystem and only `/data` writable.

**Keep your private `.env`, data directory/volume, and backups together.** The `APP_ENCRYPTION_KEY` is required to decrypt previously saved Telegram account credentials. Never commit any of them to GitHub.

## Security defaults and limitations

- New installations bind to `127.0.0.1:8789` by default. Use HTTPS and suitable authentication/network controls for remote users.
- Sending starts **off**. Opt-in and Telegram restrictions still apply after it is enabled. No campaign is sent automatically by setup or tests.
- Temporary network errors do not automatically mark Telegram sessions revoked.
- Group member APIs may hide members or telephone numbers. A completed scan does not guarantee all group members were accessible.
- The website has app-level user isolation, not independent databases or operating-system isolation for each user.

Read the [security guide](docs/SECURITY.md) before making the service reachable from the Internet.

## Development and testing

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt "httpx>=0.27,<1"
python -m unittest discover -s tests -v
docker compose config --quiet
```

For Windows PowerShell, activate the virtual environment using `.venv\Scripts\Activate.ps1` or run the interpreter inside `.venv` directly. Tests use temporary local databases and mocked Telegram calls; they do not send real messages.

The default Compose image uses Python 3.12, FastAPI, Telethon 1.x, Jinja2, and SQLite. The first-run helper requires only Python's standard library.

## License

Telegram Broadcaster is available under the [MIT License](LICENSE), copyright (c) 2026 Youssef Daoud. You may use, copy, modify and redistribute the software, including commercially, provided you retain the copyright and license notice. The software is provided without warranty. Third-party dependencies retain their respective licenses.
