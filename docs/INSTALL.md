# Install Telegram Broadcaster on your own machine

[Back to README](../README.md)

This guide is for a **new installation**. It does not assume a particular username, host folder, domain, tunnel, or cloud provider. For an existing n8n-hosted broadcaster, see [legacy migration](MAINTAINER_MIGRATION.md) instead.

## Supported deployment models

- Linux VPS, personal server, NAS, or computer with Docker Engine and the `docker compose` plugin.
- macOS and Windows with Docker Desktop and Docker Compose v2. Docker Desktop must be running.
- Local-only access over `http://localhost:8789`, remote management through an SSH tunnel, or an authenticated HTTPS reverse proxy.

You also need Git and Python **3.9+** to run the one-time configuration script. The app itself needs no Python installed on your host. The container uses Python 3.12 and runs as UID 10001.

This service requires a persistent writable data volume and **one application worker**. It is not a stateless/serverless deployment and should not be scaled to multiple independent replicas sharing SQLite.

**Do not install with a bare publicly exposed HTTP port.** The default fresh-install configuration binds to `127.0.0.1`.

## A. Clone and prepare configuration

Use any directory you can write to (for example `~/apps`, `/srv`, `/opt`, or a Docker Desktop-shared folder):

```bash
git clone https://github.com/youssefdaoud23/telegram-broadcaster.git
cd telegram-broadcaster
python3 scripts/configure.py
```

On Windows PowerShell, use `py -3 scripts/configure.py` if `python3` is not available. This script:

- Generates a **unique Fernet encryption key** into `.env`, which is excluded from Git.
- Sets `BROADCASTER_BIND_IP=127.0.0.1`, `BROADCASTER_PORT=8789`, `COOKIE_SECURE=false`, and `SEND_ENABLED=false`.
- Prepares `./data` without deleting any existing data.
- Refuses to overwrite private configuration, follow symlinked data directories, or generate a new key over nonempty old data. Recover the original encryption key if it has been lost.

To choose another local host port, run `python3 scripts/configure.py --port 8790` on **first setup only**. To prepare an HTTPS domain, run `python3 scripts/configure.py --public-url https://telegram.example.com` instead. After setup, edit the private `.env` when settings change; do not rerun the helper.

`APP_ENCRYPTION_KEY` **must not change** once you connect Telegram accounts. It encrypts saved credentials. Back it up securely, separate from the repository.

## B. Choose data storage

Choose **one** storage mode at installation and consistently use the matching Docker Compose commands. Switching modes without migrating `/data` will make the app appear empty.

### Host directory (default)

The default Compose configuration mounts `./data:/data`. The container runs as non-root UID 10001 and needs write access to that directory.

```bash
docker compose build broadcaster
docker compose run --rm --no-deps --user root --cap-add CHOWN broadcaster sh -c 'chown 10001:10001 /data'
```

This adjusts only the `/data` mount of **this Compose project**, not unrelated host folders. On Linux, `./data` will normally become owned by UID/GID 10001. If your filesystem disallows ownership changes (common with some Windows file shares), prefer the Docker-managed volume option.

### Docker-managed storage (optional)

This option avoids relying on a host bind mount and is often convenient on Docker Desktop:

```bash
docker compose -f compose.yaml -f compose.volume.yaml build broadcaster
docker compose -f compose.yaml -f compose.volume.yaml run --rm --no-deps broadcaster python -m broadcaster.cli init-db
```

The named volume is called `broadcaster_data` within the Compose project. Docker initializes it using the image's `/data` permissions. Use **both** `-f` flags for every later command, including user setup, updates, logs and backups. The `./data` directory created by the configuration helper will not be used in this mode.

**Never change an existing installation from bind storage to a named volume, or vice versa, without backing up and restoring all application data.**

## C. Create the first website administrator

For the default host-directory mode:

```bash
docker compose run --rm -it --no-deps broadcaster python -m broadcaster.cli create-admin
```

For named-volume mode, use `docker compose -f compose.yaml -f compose.volume.yaml run --rm -it --no-deps broadcaster python -m broadcaster.cli create-admin`.

The command prompts for a username and password. The first admin becomes the installation owner and can create other accounts from **User management**. Choose a strong unique password. To reset it later, use `reset-admin-password` with the same CLI.

## D. Start and verify the service

Default storage:

```bash
docker compose up -d
docker compose ps
```

For named-volume storage, use:

```bash
docker compose -f compose.yaml -f compose.volume.yaml up -d
docker compose -f compose.yaml -f compose.volume.yaml ps
```

You should see the broadcaster container running and eventually healthy. Check the application through your configured host address:

```bash
curl -fsS http://127.0.0.1:8789/health
```

If your computer does not have `curl`, open http://localhost:8789/login in a browser. For a VPS, create an SSH tunnel from your local machine:

```bash
ssh -L 8789:127.0.0.1:8789 YOUR_USER@YOUR_SERVER
```

Then open http://localhost:8789 in **your local** browser. Replace the port if you configured another one. Do not expose 8789 publicly merely to make remote login work.

## E. HTTPS for multiple remote users

Provide HTTPS using your own authenticated reverse proxy or tunnel. Examples include Caddy, Nginx, Traefik, and Cloudflare Tunnel with Access. **None of these are built into the broadcaster.**

A typical architecture:

```text
Internet -> HTTPS reverse proxy (authentication / rate limits)
         -> 127.0.0.1:BROADCASTER_PORT -> Docker broadcaster:8080
```

For an HTTPS deployment, set `COOKIE_SECURE=true` and, optionally, `PUBLIC_BASE_URL=https://telegram.example.com` in the private `.env`. The `PUBLIC_BASE_URL` field is informational in the current app, not an automatic domain or TLS installer. Keep the Docker host binding at `127.0.0.1` when the proxy runs on the same host. A reverse proxy in a different Docker network requires an explicit trusted networking arrangement.

A minimal **Caddyfile** example (Caddy installed directly on the same host, DNS pointed to it):

```caddyfile
telegram.example.com {
    reverse_proxy 127.0.0.1:8789
}
```

This example shows HTTP forwarding and Caddy-managed TLS. Add access controls suitable for the users you invite. A domain and valid certificate are required. If you prefer Cloudflare Tunnel, configure it to use the local origin and enable Cloudflare Access, or an equivalent admission policy.

After editing the private `.env` for HTTPS, run `docker compose up -d --force-recreate broadcaster` (with both `-f` flags if using a named volume). Review [security](SECURITY.md) before exposing the service.

## F. Connect Telegram and set up contacts

1. Log in with the owner account.
2. Open **Telegram accounts** and connect an account with Telegram's API ID/hash, code, and optional two-step password. Generate the API credentials at [my.telegram.org/apps](https://my.telegram.org/apps).
3. Add contacts under **Contacts**. Use **Contact groups** for different audiences.
4. Mark consent only when each individual has actually opted in.
5. In **New broadcast**, choose a connected sender, compose a message, select contacts/groups, and review the draft before sending.

Sending is **disabled** by default. A server operator may set `SEND_ENABLED=true` only after appropriate testing and confirming opt-in. This does not bypass Telegram's rate limits or anti-spam controls.

## Choose the interface template

After signing in, use **Appearance** to select **Telegram Classic** (the
default) or **Space Science** (the optional cinematic theme). A quick
**Template** selector is also provided in the top bar. The preference
is stored per user in SQLite, while the moon/sun light/dark setting
is browser-local. Theme selection never changes broadcasting behavior.
The original space reference's images and code are **not** bundled.

## Next steps

- [Settings reference](CONFIGURATION.md)
- [Updates, backups and restore](OPERATIONS.md)
- [Security and privacy](SECURITY.md)
- [Troubleshooting](TROUBLESHOOTING.md)
