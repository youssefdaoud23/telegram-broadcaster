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

   Keep SEND_MAX_RECIPIENTS=3 for the first live test. Increase only after verifying actual deliveries and recipient consent.

## Administrator account management

Run `create-admin` only once for each new administrator username. If `admin` already exists, **do not create another administrator or delete the SQLite database**. Sign in with the existing credentials or reset its password in the trusted server terminal:

~~~bash
cd /root/n8n/telegram-broadcaster
docker compose build broadcaster
docker compose run --rm -it broadcaster python -m broadcaster.cli reset-admin-password
~~~

Enter the existing username (for example `admin`) and choose any non-empty password. This updates only that administrator's password, revokes their existing website sessions, and clears failed-login lockouts. Contacts, other administrators, connected Telegram accounts, and delivery records stay untouched. Passwords are never printed, logged, or stored in plaintext.

If you instead run `create-admin` with an existing username, the CLI now explains how to reset the password without displaying a Python database traceback.

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