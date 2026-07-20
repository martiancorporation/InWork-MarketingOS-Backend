"""add Phase-1 social_platform values (seo, influencer, google_lsa, ga4)

BE-02 narrows the marketing-channel set to Meta, Google, Google LSA, SEO and
Influencer. This extends the native ``social_platform`` enum with the new
data/channel buckets the app now writes:

* ``seo``         — SEO channel + Search Console sync
* ``influencer``  — Influencer channel
* ``google_lsa``  — Google Local Services Ads channel + LSA sync
* ``ga4``         — GA4 web-analytics bucket (distinct from Google Ads ``google``)

The deprecated channels (``x``, ``pinterest``, ``email``) are NOT dropped:
Postgres cannot remove an enum value, and existing ``marketing_events`` /
``analytics_daily`` rows may still reference them. They are instead rejected at
the onboarding input edge (``app/schemas/onboarding.py``).

Postgres can't add enum values inside a transaction, so the ADD VALUE runs in an
autocommit block. On non-Postgres backends (SQLite tests) the column is plain
text, so this migration is a no-op there.

Revision ID: e1a2b3c4d5f6
Revises: d4f9a1c7e2b5
Create Date: 2026-07-20 10:45:00.000000
"""
from __future__ import annotations

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e1a2b3c4d5f6"
down_revision: str | None = "d4f9a1c7e2b5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_NEW_VALUES = ("seo", "influencer", "google_lsa", "ga4")


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return  # SQLite/other: enum column is plain text — nothing to alter.
    with op.get_context().autocommit_block():
        for value in _NEW_VALUES:
            op.execute(f"ALTER TYPE social_platform ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type; the added values are
    # harmless if left in place, so the downgrade is intentionally a no-op.
    pass
