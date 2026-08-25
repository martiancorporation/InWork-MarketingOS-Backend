"""merge migration heads

Revision ID: 0c9c608c75d9
Revises: 2a86ad34e4e9, ebee5d9a406d
Create Date: 2026-08-25 12:59:21.044667
"""

from __future__ import annotations

from collections.abc import Sequence

# revision identifiers, used by Alembic.
revision: str = "0c9c608c75d9"
down_revision: str | None = ("2a86ad34e4e9", "ebee5d9a406d")
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
