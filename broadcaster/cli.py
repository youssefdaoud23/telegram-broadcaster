"""Secure, interactive one-time administrator setup."""
from __future__ import annotations
import argparse
import getpass
import sys
from .core import create_admin, database, init_db, reset_admin_password

def main():
    parser=argparse.ArgumentParser(description="Telegram Broadcaster management")
    parser.add_argument("command", choices=["init-db","create-admin","reset-admin-password"])
    args=parser.parse_args()
    init_db()
    if args.command=="init-db":
        print("Local database initialized.")
        return
    operation = "reset" if args.command == "reset-admin-password" else "create"
    print(f"{operation.capitalize()} administrator password. The password is hidden and never stored in plaintext.")
    try:
        username=input("Admin username: ").strip()
        with database() as db:
            exists = db.execute("SELECT 1 FROM admins WHERE username=?", (username,)).fetchone() is not None
        if args.command == "create-admin" and exists:
            raise ValueError("Administrator already exists. Sign in or run reset-admin-password instead.")
        if args.command == "reset-admin-password" and not exists:
            raise ValueError("Administrator not found. Use create-admin instead.")
        password=getpass.getpass("Administrator password (any non-empty length): ")
        repeated=getpass.getpass("Repeat password: ")
        if password != repeated:
            raise ValueError("Passwords do not match")
        if args.command == "create-admin":
            create_admin(username,password)
            print("Administrator created. You can now sign in to the website.")
        else:
            reset_admin_password(username,password)
            print("Administrator password reset. Previous website sessions were signed out.")
    except (ValueError, KeyboardInterrupt, EOFError) as exc:
        print("Administrator setup:",str(exc),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()