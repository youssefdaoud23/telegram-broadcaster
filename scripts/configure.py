#!/usr/bin/env python3
"""Safe, dependency-free configuration for a NEW Telegram Broadcaster installation.

Never overwrites an existing .env or touches existing database/session data.
Works with Python 3.9+ on Linux, macOS and Windows.
"""
from __future__ import annotations

import argparse
import base64
import ipaddress
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import urlparse


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / ".env.example"
DESTINATION = ROOT / ".env"
DATA = ROOT / "data"


def valid_bind_address(raw: str) -> str:
    try:
        ip = ipaddress.IPv4Address(raw)
    except ipaddress.AddressValueError as exc:
        raise argparse.ArgumentTypeError("Use an IPv4 bind address, such as 127.0.0.1 or 0.0.0.0") from exc
    return str(ip)


def valid_port(raw: str) -> int:
    try:
        port = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Port must be an integer between 1 and 65535") from exc
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("Port must be between 1 and 65535")
    return port


def valid_public_url(raw: str) -> str:
    parsed = urlparse(raw)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise argparse.ArgumentTypeError("Public URLs must be HTTPS without embedded credentials")
    if parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise argparse.ArgumentTypeError("Use the HTTPS site origin without a path, query or fragment")
    try:
        _ = parsed.port
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Public URL contains an invalid port") from exc
    return raw.rstrip("/")


def render_example(template: str, *, host: str, port: int, public_url: str | None) -> str:
    values = {
        "APP_ENCRYPTION_KEY": base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii"),
        "BROADCASTER_BIND_IP": host,
        "BROADCASTER_PORT": str(port),
        "COOKIE_SECURE": "true" if public_url else "false",
        "PUBLIC_BASE_URL": public_url or f"http://localhost:{port}",
    }
    lines = template.splitlines()
    for key, value in values.items():
        indexes = [i for i, line in enumerate(lines) if line.startswith(key + "=")]
        if len(indexes) != 1:
            raise RuntimeError(f"Expected exactly one {key}= setting in .env.example")
        lines[indexes[0]] = key + "=" + value
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a private .env for a fresh Telegram Broadcaster install"
    )
    parser.add_argument("--port", type=valid_port, default=8789, help="Host port (default: 8789)")
    parser.add_argument("--bind-address", type=valid_bind_address, default="127.0.0.1",
                        help="Host IPv4 address (default: loopback only)")
    parser.add_argument("--public-url", type=valid_public_url,
                        help="HTTPS origin, e.g. https://telegram.example.com (enables secure cookies)")
    args = parser.parse_args(argv)

    if args.bind_address != "127.0.0.1" and not args.public_url:
        parser.error("Non-loopback exposure requires --public-url with HTTPS and an access-controlled reverse proxy")

    if DESTINATION.exists() or DESTINATION.is_symlink():
        print("Refusing to overwrite existing .env. Configuration and encryption key preserved.", file=sys.stderr)
        return 1
    if DATA.is_symlink():
        print("Refusing to prepare a symlinked data directory.", file=sys.stderr)
        return 1
    if DATA.exists():
        if not DATA.is_dir() or any(DATA.iterdir()):
            print("Existing data/ is not empty but .env is missing. Recover the original "
                  ".env and encryption key; refusing a new installation.", file=sys.stderr)
            return 1
    if not TEMPLATE.is_file():
        print(".env.example not found. Run this helper from a complete project checkout.", file=sys.stderr)
        return 1

    config = render_example(TEMPLATE.read_text(encoding="utf-8"),
                            host=args.bind_address, port=args.port,
                            public_url=args.public_url)
    DATA.mkdir(mode=0o700, exist_ok=True)
    # O_EXCL and 0600 prevent races and temporary world-readable secrets on Linux.
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        fd = os.open(DESTINATION, flags, 0o600)
    except FileExistsError:
        print(".env appeared during setup; refusing to overwrite it.", file=sys.stderr)
        return 1
    with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as target:
        target.write(config)
    try:
        os.chmod(DESTINATION, 0o600)
    except OSError:
        pass  # Docker Desktop / Windows host permissions vary.

    print("Created .env with a unique encryption key. Keep it private and backed up.")
    print("Created or verified data/ (persistent database and Telegram sessions).")
    print("Next: docker compose build broadcaster")
    print("Then: docker compose run --rm --no-deps --user root --cap-add CHOWN broadcaster sh -c 'chown 10001:10001 /data'")
    print("Then: docker compose run --rm -it --no-deps broadcaster python -m broadcaster.cli create-admin")
    print("Then: docker compose up -d")
    if args.public_url:
        print("HTTPS is required at your reverse proxy. Confirm the origin is access-controlled.")
    else:
        print(f"Open http://localhost:{args.port} on this machine or use an SSH tunnel.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
