"""Unit tests: Google Ads request headers — ``login-customer-id`` is only
sent when the caller explicitly supplies one (operator-entered per real
account, never hardcoded — see ``Integration.login_customer_id``)."""

from __future__ import annotations

from app.integrations.google.ads import GoogleAdsClient
from app.integrations.google.lsa import LsaClient


def test_login_customer_id_sent_when_supplied() -> None:
    client = GoogleAdsClient()
    headers = client._headers("token", "452-764-8021")  # noqa: SLF001
    assert headers["login-customer-id"] == "4527648021"  # dashes stripped


def test_login_customer_id_omitted_when_not_supplied() -> None:
    client = GoogleAdsClient()
    headers = client._headers("token", None)  # noqa: SLF001
    assert "login-customer-id" not in headers


def test_lsa_client_never_sends_login_customer_id() -> None:
    client = LsaClient()
    headers = client._headers("token")  # noqa: SLF001
    assert "login-customer-id" not in headers


def test_lsa_client_sends_login_customer_id_only_for_discovery() -> None:
    """``fetch_daily_insights`` never sends it (above) — but the manager-linked
    account *discovery* call (``list_customer_clients``) has to query *through*
    the manager, so it's the one exception, scoped to that call only."""
    client = LsaClient()
    headers = client._headers("token", "452-764-8021")  # noqa: SLF001
    assert headers["login-customer-id"] == "4527648021"
