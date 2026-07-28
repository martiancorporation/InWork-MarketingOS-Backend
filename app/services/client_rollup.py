"""Keep the client-level rollup columns in step with the daily facts.

``clients.spend_total`` / ``leads_total`` / ``cpl`` are denormalised lifetime
figures. They are read in several places — the client list, the dashboard's
executive brief and budget card, and the cross-client assistant — but nothing
used to write them, so every client reported $0 spend and 0 leads no matter how
much data it had.

Recomputing is a single aggregate over ``analytics_daily``, so it is cheap enough
to run on every write path rather than trying to maintain the columns
incrementally (which would drift the moment a row is corrected or re-synced).
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.models.client import Client
from app.repositories.analytics_repository import AnalyticsRepository


def refresh_client_rollups(db: Session, client_id: uuid.UUID) -> None:
    """Recompute lifetime spend/leads/CPL for one client. Caller commits."""
    client = db.get(Client, client_id)
    if client is None:
        return
    totals = AnalyticsRepository(db).totals(client_id)
    spend = float(totals.get("spend") or 0)
    leads = int(totals.get("leads") or 0)
    client.spend_total = round(spend, 2)
    client.leads_total = leads
    client.cpl = round(spend / leads, 2) if leads else 0
