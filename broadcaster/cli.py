"""Secure, interactive one-time administrator setup."""
from __future__ import annotations
import argparse
import getpass
import sys
from .core import create_admin, init_db

def main():
    parser=argparse.ArgumentParser(description="Telegram Broadcaster management")
    parser.add_argument("command", choices=["init-db","create-admin"])
    args=parser.parse_args()
    init_db()
    if args.command=="init-db":
        print("Local database initialized.")
        return
    print("Create administrator. Password is never displayed or saved in plaintext.")
    try:
        username=input("Admin username: ").strip()
        password=getpass.getpass("Administrator password (any non-empty length): ")
        repeated=getpass.getpass("Repeat password: ")
        if password != repeated:
            raise ValueError("Passwords do not match")
        create_admin(username,password)
        print("Administrator created. You can now sign in to the website.")
    except (ValueError, KeyboardInterrupt, EOFError) as exc:
        print("Could not create administrator:",str(exc),file=sys.stderr)
        sys.exit(1)

if __name__=="__main__":
    main()