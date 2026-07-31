"""Synthetic data generation must be deterministic, so re-seeding is a no-op.

The generator lives in ``app.services.demo_data_service`` (it used to live in the
seeder script) so the create-time hook and the backfill CLI produce identical
numbers for a given client.
"""

from __future__ import annotations

from datetime import date

from app.models.enums import SocialPlatform
from app.services import demo_data_service as seeder

TODAY = date(2026, 7, 27)


def _slug(slug: str = "acme-co") -> str:
    """The generator keys off the slug alone — no Client object needed."""
    return slug


def test_rows_are_deterministic_for_the_same_client():
    """Same slug in, byte-identical rows out — this is what makes re-runs idempotent."""
    first = seeder.rows_for_client(_slug(), 30, TODAY)
    second = seeder.rows_for_client(_slug(), 30, TODAY)
    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]


def test_different_clients_get_different_numbers():
    a = seeder.rows_for_client(_slug("acme-co"), 30, TODAY)
    b = seeder.rows_for_client(_slug("northwind-labs"), 30, TODAY)
    assert [r.impressions for r in a] != [r.impressions for r in b]


def test_row_count_and_date_window():
    days = 45
    rows = seeder.rows_for_client(_slug(), days, TODAY)
    assert len(rows) == days * len(seeder._PROFILES)
    dates = {r.date for r in rows}
    assert max(dates) == TODAY
    assert (TODAY - min(dates)).days == days - 1


def test_organic_channels_have_no_spend():
    """ga4/seo are organic — a nonzero spend there would corrupt CPL and ROAS."""
    rows = seeder.rows_for_client(_slug(), 30, TODAY)
    organic = {SocialPlatform.ga4, SocialPlatform.seo}
    assert all(r.spend == 0 for r in rows if r.platform in organic)
    assert any(r.spend > 0 for r in rows if r.platform not in organic)


def test_metrics_form_a_coherent_funnel():
    rows = seeder.rows_for_client(_slug(), 30, TODAY)
    for r in rows:
        assert r.clicks <= r.impressions
        assert r.conversions <= r.clicks
        assert r.leads >= r.conversions


def test_weekends_dip_below_weekdays():
    rows = [
        r for r in seeder.rows_for_client(_slug(), 60, TODAY) if r.platform == SocialPlatform.google
    ]
    weekend = [r.impressions for r in rows if r.date.weekday() >= 5]
    weekday = [r.impressions for r in rows if r.date.weekday() < 5]
    assert sum(weekend) / len(weekend) < sum(weekday) / len(weekday)
