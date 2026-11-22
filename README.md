# Telegram Broadcaster

Self-hosted personal Telegram messaging dashboard with a beautiful browser UI, SQLite contact database, and secure multi-account Telegram login.

## What it does

- Password-protected admin dashboard, contacts, Telegram accounts, message composer, and delivery history.
- Add, edit, and remove people from a local database. Import Google Sheets CSV exports (Name and ID columns supported), and export data.
- Guided Telegram API application setup at https://my.telegram.org/apps; sign in using a Telegram code and optional 2FA password inside the authenticated website.
- Multiple personal Telegram senders with separate local Telethon sessions. API hashes and phone numbers are encrypted in SQLite; login codes and 2FA passwords are not stored.
- Separate direct messages, not a group or Telegram bot. Review exact recipients before clicking Send.
- Explicit opt-in, sender identity checks, a per-campaign limit, a delayed delivery loop, flood-wait protection, and no automatic retry of interrupted broadcasts.

**The GitHub repository is public. NEVER commit .env, Telegram session files, customer contacts, database files, API hashes, phone numbers, credentials or delivery logs.**

## Telegram Web-inspired interface

The web interface uses a Telegram-inspired day/night visual theme with rounded
contacts, a conversation-style message preview and a split recipient/composer
layout. Use the moon/sun button at the top of the page to switch modes.
The site starts with your system theme and remembers your choice in the local
browser. No outside stylesheets, images, scripts, or new API calls are required.

The composer preview is **visual only**: typing in it never sends a Telegram
message. The actual protected workflow remains **Preview > review recipients >
Send**; all original opt-in and delivery restrictions are unchanged.

## Technology

FastAPI · Telethon 1.x · Jinja2 · Python 3.12 · SQLite · vanilla JavaScript/CSS · Docker Compose.

## Deploy on your own server (independent of the existing n8n setup)

The website uses the **same host-port publishing approach as n8n**: Docker listens on port 8789 of the host. Configure Cloudflare Tunnel with your server's public IP and port; do not share secrets or session files. Protect the website and, ideally, restrict direct access to the origin port.

1. Clone the canonical repository:

   ~~~bash
   cd /root/n8n
   git clone https://github.com/youssefdaoud23/telegram-broadcaster.git
   cd telegram-broadcaster
   ~~~

2. Prepare the private configuration. The following command generates a new encryption key on your server, never in GitHub:

   ~~~bash
   umask 077
   cp .env.example .env
   python3 - <<'PY'
from pathlib import Path
import os,base64
p=Path('.env')
s=p.read_text()
s=s.replace('APP_ENCRYPTION_KEY=', 'APP_ENCRYPTION_KEY='+base64.urlsafe_b64encode(os.urandom(32)).decode(),1)
p.write_text(s)
PY
   chmod 600 .env
   install -d -m 0700 -o 10001 -g 10001 data
   ~~~

   The encryption key is essential: protect a backup of it. Losing the key makes saved Telegram account credentials unreadable.

3. Build the separate website and create the first administrator:

   ~~~bash
   docker compose build
   docker compose run --rm -it broadcaster python -m broadcaster.cli create-admin
   ~~~

   Use a unique administrator username and any non-empty password you choose. A strong, unique password is recommended. The password is hidden while you type and saved as a memory-hard password hash.

4. Start the website and check the container:

   ~~~bash
   docker compose up -d
   docker compose ps
   ~~~

   The website listens on the server's port 8789, like n8n on port 5678. For private direct testing you can also open an SSH tunnel:

   ~~~bash
   ssh -L 8789:127.0.0.1:8789 root@YOUR_SERVER
   ~~~

   Then browse to http://localhost:8789 and sign in.

5. In the UI, select **Telegram accounts → Connect account**. Follow the official my.telegram.org instructions, enter API ID/Hash and your phone, then complete the verification-code and optional two-step verification stage. Each account receives its own protected Telethon session.

6. Add contacts under **Contacts**. You may import a CSV exported from Google Sheets. Imported contacts are **not opted in by default**, so confirm permission from the recipients and mark only those who agreed as opted in.

7. On **New broadcast**, choose the sending account, enter your message, and select eligible contacts. Click **Preview** to inspect the exact list without sending.

8. Real sending is deliberately off in the initial configuration. After a controlled, authorized test is planned, edit your private .env to set SEND_ENABLED=true and recreate ONLY this new website:

   ~~~bash
   nano .env
   docker compose up -d --force-recreate broadcaster
   ~~~

   For small initial tests you can optionally set SEND_MAX_RECIPIENTS=3 in your private .env. If omitted, the application itself imposes no fixed recipient count per broadcast; Telegram's restrictions and practical delivery time still apply.

## Multiple people, separate private workspaces

One website serves multiple users without mixing their Telegram accounts or
contacts. The installation owner signs in and opens **User management** on
the left navigation to create a new user. Enter their username and an initial
non-empty password; share that password privately. **Do not give others the
installation owner's login.**

Each user then signs in at the same HTTPS hostname, opens **Telegram accounts**,
and connects their own Telegram identity using their own API ID/Hash, phone,
code and optional Telegram two-step password. Contact lists, exports, broadcasts,
campaign history, connected account choices and retry drafts are scoped to the
currently logged-in user, including backend access to guessed direct URLs.
Even the installation owner cannot view other users' Telegram data in the
website; the owner only sees usernames and whether access is active.

The installation owner can **disable or re-enable** other users and **reset
their broadcaster passwords** from User management. A reset requires entering
the owner's current password again. Disabling a user immediately invalidates
their web sessions and prevents future sends from continuing past the next
worker authorization check. A message already in flight may finish.

Every user, including the installation owner, can use **Change password** in
the sidebar. This requires their current password, signs out all their
existing website sessions, and leaves connected Telegram accounts and history
intact. **This changes only the website login, not a Telegram password.**

### Upgrade an existing single-user installation (important)

The migration is **additive**: existing admin accounts stay in place and new
`owner_id` fields are populated in accounts, contacts and campaigns.
Existing Telegram session filenames, SQLite IDs, credentials, messages and
delivery statuses are preserved. The oldest preexisting admin becomes the
installation owner and receives all historical data. Any other preexisting
admin is made a regular user, starting with no legacy records. If several
people previously shared a database, there is no safe automated way to infer
their separate ownership of historical records.

Before deploying this schema change, **back up the live database using SQLite's
consistent online backup method**. With the broadcaster running and with no
active sends, run from the server:

~~~bash
cd /root/n8n/telegram-broadcaster
docker compose exec -T broadcaster python - <<'PY'
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

source = Path("/data/broadcaster.sqlite3")
backup = source.with_name("broadcaster.pre-multiuser-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + ".sqlite3")
if backup.exists():
    raise SystemExit("Backup already exists; do not overwrite it")
original = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
copy = sqlite3.connect(str(backup))
try:
    original.backup(copy)
    if copy.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
        raise SystemExit("Backup failed integrity check; do not deploy")
finally:
    copy.close()
    original.close()
os.chmod(backup, 0o600)
print("Verified SQLite backup:", backup)
PY
docker compose up -d --build
docker compose ps
curl -fsS http://127.0.0.1:8789/health
~~~

Do not run the deployment while any broadcast is in progress: on startup the
application deliberately marks orphaned queued or sending campaigns
`interrupted` rather than automatically retrying them. Do not delete
`data/` or overwrite `.env`. Its encryption key and Telegram session files
are still needed to access existing accounts. This migration is tested
against a synthetic copy of the original schema, but the live database must
be backed up independently.

Only the broadcaster Compose project is rebuilt; n8n, the legacy Telegram
bridge and other Docker projects remain unchanged.

## Administrator account management

Use **User management** to create additional private users after upgrading.
The CLI `create-admin` is retained for bootstrap and troubleshooting; only
the very first admin becomes the installation owner, subsequent CLI accounts
are ordinary private users. Run `create-admin` only once for each username. If `admin` already exists, **do not create another administrator or delete the SQLite database**. Sign in with the existing credentials or reset its password in the trusted server terminal:

~~~bash
cd /root/n8n/telegram-broadcaster
docker compose build broadcaster
docker compose run --rm -it broadcaster python -m broadcaster.cli reset-admin-password
~~~

Enter the existing username (for example `admin`) and choose any non-empty password. This updates only that administrator's password, revokes their existing website sessions, and clears failed-login lockouts. Contacts, other administrators, connected Telegram accounts, and delivery records stay untouched. Passwords are never printed, logged, or stored in plaintext.

If you instead run `create-admin` with an existing username, the CLI now explains how to reset the password without displaying a Python database traceback.

## Troubleshooting: Send returns HTTP 500 / no running event loop

Versions before the sending-handler correction could record a campaign as queued
then fail with `RuntimeError: no running event loop` because the synchronous
FastAPI handler attempted to create an asynchronous Telethon task. The send
route is now asynchronous, which permits scheduling on FastAPI's running loop.

After pulling the corrected code, rebuild and recreate **only** the broadcaster
container. Startup marks any orphaned `queued` or `sending` campaigns
`interrupted` to prevent accidental duplicate deliveries. Review the
per-recipient history before creating a new campaign; do not repeatedly click
Send on an uncertain campaign.

For accurate operational history, send failures must be checked against the
persisted campaign/delivery states and Telegram's own chat history. The
administrator CLI no longer triggers in-flight recovery when initializing its
database connection.

## Telegram session status after revocation

The account list used to show a cached `Connected` label even after a
Telegram session was terminated from the Telegram app. An explicit
`Check session` could detect the revocation but did not update the
account's stored state. This is corrected.

When you open **Overview**, **Telegram accounts**, **New broadcast**, or an
account setup page, the website checks connected sessions against Telegram
without sending any messages. Successful checks are cached for up to
90 seconds to avoid repeated requests. To verify immediately, click
**Check all sessions** on **Telegram accounts** or **Check session** on an
individual account's setup page.

If Telegram confirms that a session is revoked, expired, or unauthorized,
the account changes to **Disconnected** and is removed from the selection
of available senders. The original username and encrypted API settings
remain for reconnecting; no messages are sent. Request a new login code and
complete normal Telegram authentication to reconnect.

Temporary network outages and timeouts do **not** automatically mark a
session disconnected. The website reports that some checks could not be
completed, preserving the prior status until a conclusive check. The
delivery worker independently checks authorization before sending and
updates the same stored status if it discovers a revoked session. The Send
endpoint also rejects an existing draft whose sender is disconnected.

Session checks happen on webpage visits and when manually requested - this
is not a permanent background monitor. A session can become stale between
visits. Telegram's own rate limits and remote connectivity still apply.

## Interrupted broadcasts: Try again without duplicate sends

Open **History**. An interrupted broadcast displays **Try again** when at
least one recipient is still eligible and has the exact delivery state
`pending` (meaning sending was never started for that recipient). The same
button is available on its Details page.

**Try again creates a separate draft; it never sends messages.** The draft
keeps the original message and sending account, but copies **only** pending,
enabled, opted-in, non-archived contacts. It deliberately excludes recipients
marked sent, sending, unknown, failed, blocked or skipped, since the actual
Telegram delivery outcome could be uncertain or they may be ineligible.
The original history remains unchanged.

Repeatedly clicking Try again on the same interrupted campaign returns to the
same retry draft instead of creating multiple drafts. If there are no safely
retryable contacts, the button is hidden and the Details page explains why.
Review the exact recipients in the newly created draft before clicking Send.
A later interruption may be retried from its own new interrupted campaign.

## Optional recipient limit

This application originally enforced a default `SEND_MAX_RECIPIENTS=3` and a
hard maximum of 100 **per broadcast** as a guard against accidental large sends
during setup. **Neither number is required by Telegram.** There is no fixed
application recipient cap when this variable is absent or blank.

The private `.env` can omit `SEND_MAX_RECIPIENTS` entirely; you can also
set `SEND_MAX_RECIPIENTS=0` or `SEND_MAX_RECIPIENTS=unlimited` to disable
the application cap. To impose your own optional safeguard, set any positive
integer, e.g. `SEND_MAX_RECIPIENTS=20`. Invalid values are rejected on startup.

Changing an existing server configuration only takes effect after recreating
the broadcaster container (when no broadcasts are actively sending):

~~~bash
cd /root/n8n/telegram-broadcaster
docker compose up -d --no-deps --force-recreate broadcaster
~~~

**Unlimited is not unlimited Telegram capacity.** Telegram may rate-limit,
restrict or temporarily block accounts that send too many messages. The sender
still delivers separately, with a delay between recipients, and stops on
Telegram flood-wait errors. The website still requires opted-in contacts,
an explicit preview and a separate Send action. Very large browser submissions
also remain subject to HTTP form parsing and server resource limits; the
application is not designed as an infinite-scale campaign queue. Consider
sending smaller approved batches and checking delivery history before expanding.

Existing contacts and message history remain in the persistent `data/`
directory. A restart interrupts any orphaned in-progress campaigns instead
of retrying them automatically.

## Deployment workflow

Always use GitHub as the source of truth. Develop on a feature branch, run tests, merge into main, then update the separate server checkout:

~~~bash
cd /root/n8n/telegram-broadcaster
git status --short
git fetch origin
git pull --ff-only origin main
docker compose up -d --build broadcaster
docker compose ps
~~~

This sequence never changes your existing n8n container, Google Sheets workflow, or legacy Telegram bridge.

## Security / operational notes

- The webpage currently supports administrator authentication and CSRF protection on all authenticated mutation routes. Because port 8789 is published on the host, Cloudflare Access on the hostname by itself will NOT protect users connecting directly to the server IP:port; use firewall restrictions or private-origin routing if this is not acceptable.
- Keep the app bound to localhost, or add HTTPS behind an authenticated proxy (e.g. Cloudflare Access). For HTTPS access set COOKIE_SECURE=true and review proxy trust, DNS, and authentication rules before exposing the site.
- Telegram privacy and spam restrictions still apply. An arbitrary phone number or numeric ID may not be resolvable. Phone lookup works best for already known contacts.
- The sender sessions are protected, local secrets. The new app deliberately does not share the existing bridge's session file.
- Run one application worker. Per-account locks and in-memory background tasks are single-worker by design.
- A server restart marks queued/in-flight campaigns as interrupted. Never automatically retry unknown delivery outcomes.
- Check the **History** page for actual sent and failed results; a submitted form alone is not proof of delivery.
- Back up the protected .env, SQLite database, and session directory as sensitive data.

## Tests

~~~bash
python -m compileall -q broadcaster tests
python -m unittest discover -s tests -v
~~~

More details: project_context.md, plan.md, CONTINUE_PROJECT.md.
## Cloudflare Tunnel (same host-port approach as existing n8n)

The existing n8n Docker Compose publishes port `5678:5678`. This application now publishes `8789:8080` using the same pattern; no additional Docker networking is required.

For the existing tunnel's published route:

- Public hostname: **telegram-broadcaster.youssefdaoud.com**
- Service type: **HTTP**
- Origin URL: **http://72.61.177.85:8789**

After updating GitHub, pull the latest commit on the server and recreate only this application:

~~~bash
cd /root/n8n/telegram-broadcaster
git pull --ff-only origin main
docker compose up -d --no-deps --force-recreate broadcaster
curl -fsS http://127.0.0.1:8789/health
~~~

The private server-side `.env` should contain `COOKIE_SECURE=true` and `PUBLIC_BASE_URL=https://telegram-broadcaster.youssefdaoud.com`. Do not commit `.env` or restart the unrelated tunnel/n8n containers.

**Security:** Like n8n's host port, this port can be reached directly by the public IP if the firewall permits it. Cloudflare Access does **not** protect direct IP connections. Because this website holds Telegram account credentials and may use user-chosen short passwords, strongly prefer restricting the direct port in the server firewall to known authorized origins or switching to a private tunnel origin later. Always use HTTPS on the public hostname.