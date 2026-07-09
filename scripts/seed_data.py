"""Seed the initial admin user — idempotent.

Creates a single admin account IF it does not already exist. If a user with the
same email is already present, it is left untouched.

Defaults (overridable via env for other environments):
    SEED_ADMIN_EMAIL     admin@inwork.com
    SEED_ADMIN_PASSWORD  12345678          (dev default — set a strong one elsewhere)
    SEED_ADMIN_NAME      Admin

Run from the Backend/ directory with the virtualenv active:
    python scripts/seed_data.py      # or: make seed
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

# Make the app package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.security import hash_password  # noqa: E402
from app.db.session import get_session_factory  # noqa: E402
from app.models.enums import UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.repositories.user_repository import UserRepository  # noqa: E402

ADMIN_EMAIL = os.getenv("SEED_ADMIN_EMAIL", "admin@inwork.com").lower()
ADMIN_PASSWORD = os.getenv("SEED_ADMIN_PASSWORD", "12345678")
ADMIN_NAME = os.getenv("SEED_ADMIN_NAME", "Admin")


def main() -> None:
    session = get_session_factory()()
    try:
        users = UserRepository(session)
        if users.get_by_email(ADMIN_EMAIL) is not None:
            print(f"✓ Admin '{ADMIN_EMAIL}' already exists — leaving it unchanged.")
            return
        session.add(
            User(
                email=ADMIN_EMAIL,
                name=ADMIN_NAME,
                password_hash=hash_password(ADMIN_PASSWORD),
                role=UserRole.admin,
            )
        )
        session.commit()
        print(f"✓ Created admin '{ADMIN_EMAIL}'.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
