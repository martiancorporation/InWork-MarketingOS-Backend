"""The synthetic seeder must be deterministic, so re-running it is a no-op upsert."""

from __future__ import annotations

import importlib.util
import sys
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from app.models.enums import SocialPlatform

# The seeder is a script, not part of the app package — load it by path.
_SPEC = importlib.util.spec_from_file_location(
    "seed_synthetic_analytics",
    Path(__file__).resolve().parents[2] / "scripts" / "seed_synthetic_analytics.py",
)
assert _SPEC and _SPEC.loader
seeder = importlib.util.module_from_spec(_SPEC)
# @dataclass looks its own module up in sys.modules, so register before exec.
sys.modules[_SPEC.name] = seeder
_SPEC.loader.exec_module(seeder)

TODAY = date(2026, 7, 27)


def _fake_client(slug: str = "acme-co"):
    return SimpleNamespace(slug=slug, name="Acme Co.")


def test_rows_are_deterministic_for_the_same_client():
    """Same slug in, byte-identical rows out — this is what makes re-runs idempotent."""
    first = seeder._rows_for_client(_fake_client(), 30, TODAY)
    second = seeder._rows_for_client(_fake_client(), 30, TODAY)
    assert [r.model_dump() for r in first] == [r.model_dump() for r in second]


def test_different_clients_get_different_numbers():
    a = seeder._rows_for_client(_fake_client("acme-co"), 30, TODAY)
    b = seeder._rows_for_client(_fake_client("northwind-labs"), 30, TODAY)
    assert [r.impressions for r in a] != [r.impressions for r in b]


def test_row_count_and_date_window():
    days = 45
    rows = seeder._rows_for_client(_fake_client(), days, TODAY)
    assert len(rows) == days * len(seeder._PROFILES)
    dates = {r.date for r in rows}
    assert max(dates) == TODAY
    assert (TODAY - min(dates)).days == days - 1


def test_organic_channels_have_no_spend():
    """ga4/seo are organic — a nonzero spend there would corrupt CPL and ROAS."""
    rows = seeder._rows_for_client(_fake_client(), 30, TODAY)
    organic = {SocialPlatform.ga4, SocialPlatform.seo}
    assert all(r.spend == 0 for r in rows if r.platform in organic)
    assert any(r.spend > 0 for r in rows if r.platform not in organic)


def test_metrics_form_a_coherent_funnel():
    rows = seeder._rows_for_client(_fake_client(), 30, TODAY)
    for r in rows:
        assert r.clicks <= r.impressions
        assert r.conversions <= r.clicks
        assert r.leads >= r.conversions


def test_weekends_dip_below_weekdays():
    rows = [
        r
        for r in seeder._rows_for_client(_fake_client(), 60, TODAY)
        if r.platform == SocialPlatform.google
    ]
    weekend = [r.impressions for r in rows if r.date.weekday() >= 5]
    weekday = [r.impressions for r in rows if r.date.weekday() < 5]
    assert sum(weekend) / len(weekend) < sum(weekday) / len(weekday)
