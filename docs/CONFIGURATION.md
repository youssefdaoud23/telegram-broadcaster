# Configuration reference

[Back to README](../README.md) | [Install](INSTALL.md)

The repository includes `.env.example`. Run `python3 scripts/configure.py` on a fresh installation to produce an untracked private `.env`. If your install already has a private `.env`, **do not replace or regenerate it**. The helper intentionally refuses to overwrite it.

| Setting | New install default | Notes |
|---|---|---|
| `APP_ENCRYPTION_KEY` | Random Fernet key | Mandatory. Back up securely; changing it makes encrypted Telegram credentials unreadable. |
| `DATABASE_PATH` | `/data/broadcaster.sqlite3` | SQLite path **inside** the container. |
| `TELEGRAM_SESSION_DIR` | `/data/telegram_sessions` | Sensitive session files; must persist across rebuilds. |
| `BROADCASTER_BIND_IP` | `127.0.0.1` | Host IP where Docker publishes port. Use a trusted HTTPS proxy for remote access. |
| `BROADCASTER_PORT` | `8789` | Host port; container always listens on 8080. |
| `COOKIE_SECURE` | `false` locally | Set `true` when users access the website over HTTPS. |
| `SESSION_DAYS` | `7` | Website session lifetime, clamped to 1 to 30 days. |
| `SEND_ENABLED` | `false` | Keep disabled during setup. Turning on does not authorize unsolicited messaging. |
| `SEND_MAX_RECIPIENTS` | Unset | Optional positive integer limit. Unset, `0`, `none`, or `unlimited` means no app cap. |
| `PUBLIC_BASE_URL` | `http://localhost:8789` | Currently informational; does not configure routing, TLS or cookies. |

Docker Compose reads `.env` to interpolate the host bind IP and port and also passes its settings into the broadcaster container. Shell environment variables can override Compose interpolation. The Compose file's **fallback** host binding remains `0.0.0.0` for compatibility with preexisting installations whose private `.env` does not have a `BROADCASTER_BIND_IP` key. The **new** configuration helper writes `127.0.0.1`, avoiding inadvertent public exposure. Use `docker compose config --quiet` to validate without printing decrypted/interpolated secrets in your terminal.

After changing port, binding, or application environment, use `docker compose up -d --force-recreate broadcaster` when no broadcasts are active. Some changes require `--build` only when code or the image changed.

## Persistent data modes

Default: `./data:/data` bind mount, useful for visible local backups. The image runs as UID 10001.

Optional: `compose.volume.yaml` changes the `/data` mount to a Docker-managed volume. Use `-f compose.yaml -f compose.volume.yaml` on **every** Docker Compose invocation for that deployment.

Changing volume type without a data migration causes the app to use a different SQLite database and Telegram sessions. Never run `docker compose down -v` while you want to preserve data.

## Ports and HTTPS

The website listens on `8080` inside the container. The host port defaults to `8789`. New installs publish to the loopback interface by default, so only local applications or SSH tunnels can reach it.

For public access, terminate TLS at a reverse proxy and protect the site with access controls. Set `COOKIE_SECURE=true` when the browser uses HTTPS. If your proxy shares a separate Docker network, configure routing explicitly rather than publishing an open host port.

## Encryption and cookies

The setup script creates a unique random 32-byte Fernet key. Keep backups of `.env` encrypted and access-restricted. The app protects session cookies with `HttpOnly`, enables CSRF checking on writes, and sends security headers. Website access control is application-level: the installation owner can create accounts but cannot view another workspace's contact records through the UI.

See [Security](SECURITY.md) and [Operations](OPERATIONS.md).

SQLite and session files require persistent storage on one instance.
