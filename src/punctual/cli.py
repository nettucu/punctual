"""Admin CLI: `punctual-admin create-admin`, `punctual-admin seed-demo`."""

from __future__ import annotations

import argparse
import getpass
import sqlite3
import sys

from punctual.db import connect, init_db
from punctual.demo import ADMIN_EMAIL, DEMO_PASSWORD, seed_demo
from punctual.security import hash_password


def create_admin(name: str, email: str, password: str) -> None:
    """Insert an admin employee.

    Args:
        name: Display name.
        email: Login email.
        password: Plaintext password (min length enforced).

    Raises:
        ValueError: If the password is too short or the email exists.
    """
    init_db()
    try:
        with connect() as conn:
            conn.execute(
                "INSERT INTO employees (name, email, password_hash, role) VALUES (?, ?, ?, 'admin')",
                (name, email.strip(), hash_password(password)),
            )
    except sqlite3.IntegrityError as exc:
        raise ValueError(f"email already exists: {email}") from exc


def bootstrap_admin() -> bool:
    """Create the first admin from settings if the DB has no employees (for deployments without a shell).

    Returns:
        True if an admin was created.
    """
    from punctual.settings import settings

    if not (settings.bootstrap_admin_email and settings.bootstrap_admin_password):
        return False
    with connect() as conn:
        if conn.execute("SELECT COUNT(*) FROM employees").fetchone()[0] > 0:
            return False
    create_admin(
        "Administrator",
        settings.bootstrap_admin_email,
        settings.bootstrap_admin_password,
    )
    return True


def main() -> None:
    """Entry point."""
    parser = argparse.ArgumentParser(prog="punctual-admin")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser(
        "create-admin", help="create an admin user (password prompted, never on argv)"
    )
    p.add_argument("--name", required=True)
    p.add_argument("--email", required=True)
    sub.add_parser(
        "seed-demo",
        help=f"fill an empty database with demo data (admin {ADMIN_EMAIL}, password {DEMO_PASSWORD})",
    )
    args = parser.parse_args()
    if args.cmd == "seed-demo":
        try:
            seed_demo()
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"demo data created: log in as {ADMIN_EMAIL} / {DEMO_PASSWORD}")
        return
    password: str = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat: "):
        sys.exit("passwords differ")
    try:
        create_admin(args.name, args.email, password)
    except ValueError as exc:
        sys.exit(str(exc))
    print(f"admin {args.email} created")
