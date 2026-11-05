"""Create the first CourseFinder owner in the configured local SQLite database.

The email and password are entered interactively. Password input is hidden and
only a password hash is written to the database.
"""

from __future__ import annotations

import getpass
import hmac
import os
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
from email_validator import EmailNotValidError, validate_email

load_dotenv(ROOT / ".env")

from app.config import Config
from app.store.errors import DuplicateError
from app.store.local import LocalStore


def _configured_database_path():
    if Config.DATA_BACKEND != "local":
        raise ValueError(
            "First-owner setup only supports the configured local SQLite database. "
            "No remote database was contacted."
        )
    path = Path(Config.DATABASE_PATH)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(
            f"The configured SQLite database does not exist: {path}. "
            "Start CourseFinder once to initialize its local database, then run setup again."
        )
    return path


def _check_database_is_ready(database_path):
    try:
        with sqlite3.connect(database_path.as_uri() + "?mode=ro", uri=True) as connection:
            has_users_table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'users'"
            ).fetchone()
            if not has_users_table:
                raise ValueError(
                    "The configured SQLite file is not an initialized CourseFinder database."
                )
            has_owner = connection.execute(
                "SELECT 1 FROM users WHERE role = 'admin' LIMIT 1"
            ).fetchone()
    except sqlite3.Error as exc:
        raise ValueError(
            "The configured SQLite file is not a readable CourseFinder database."
        ) from exc
    if has_owner:
        raise ValueError("An owner account already exists; first-time setup is disabled.")


def _prompt_email():
    email = input("Owner email: ").strip()
    try:
        return validate_email(email, check_deliverability=False).normalized
    except EmailNotValidError as exc:
        raise ValueError(f"Invalid email address: {exc}") from exc


def _prompt_password():
    password = getpass.getpass("New owner password (12–128 characters): ")
    confirmation = getpass.getpass("Confirm new owner password: ")
    if not 12 <= len(password) <= 128:
        raise ValueError("Password must contain 12–128 characters.")
    if not hmac.compare_digest(password, confirmation):
        raise ValueError("The password entries do not match.")
    return password


def main():
    try:
        database_path = _configured_database_path()
        _check_database_is_ready(database_path)
        email = _prompt_email()
        password = _prompt_password()

        # Recheck under an immediate SQLite transaction before creating the account.
        owner = LocalStore(database_path).create_first_owner(email, password)
        print(
            f"First owner account created for {owner['email']}. "
            "Sign in at http://127.0.0.1:5000/auth/login."
        )
    except (OSError, ValueError, DuplicateError) as exc:
        sys.exit(str(exc))


if __name__ == "__main__":
    main()
