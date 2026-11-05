"""Grant administrator access to one existing account.

This is the only supported way to create an administrator.
There is no public website form for this.

Example (PowerShell):

  .\\.venv\\Scripts\\Activate.ps1
  python scripts\\promote_admin.py --email you@example.com --token YOUR_ADMIN_SETUP_TOKEN

ADMIN_SETUP_TOKEN must match the value in your .env file.
The account must already exist (register on the site first).
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from app import create_app
from app.config import WEAK_SECRET_KEYS


def main():
    parser = argparse.ArgumentParser(description="Promote a CourseFinder user to administrator")
    parser.add_argument("--email", required=True, help="Exact email of the existing account")
    parser.add_argument("--token", required=True, help="Must match ADMIN_SETUP_TOKEN in .env")
    args = parser.parse_args()

    expected = (os.getenv("ADMIN_SETUP_TOKEN") or "").strip()
    if not expected or expected in WEAK_SECRET_KEYS or len(expected) < 16:
        sys.exit(
            "ADMIN_SETUP_TOKEN is missing, a placeholder, or shorter than 16 characters. "
            "Set a strong random ADMIN_SETUP_TOKEN in .env first."
        )
    if not secrets.compare_digest(args.token.strip(), expected):
        sys.exit("Setup token does not match.")

    app = create_app()
    with app.app_context():
        store = app.extensions["store"]
        try:
            user = store.set_user_role(args.email, "admin")
        except ValueError as exc:
            sys.exit(str(exc))
        print(f"Administrator role granted to {user['email']} ({user['id']})")


if __name__ == "__main__":
    main()
