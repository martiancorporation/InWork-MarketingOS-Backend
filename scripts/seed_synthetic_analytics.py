"""Backfill synthetic demo data for clients that already exist.

New clients are seeded automatically when they're created (see
``DEMO_SEED_ON_CREATE`` and ``app/services/demo_data_service.py``). This script is
for the ones that predate that, and for re-seeding after a purge.

All the generation logic lives in the service — this file is only a CLI, so the
numbers a backfilled client gets are byte-identical to what a newly created one
gets.

Run from the Backend/ directory with the virtualenv active:
    python scripts/seed_synthetic_analytics.py                    # every active client
    python scripts/seed_synthetic_analytics.py --all-statuses     # drafts/paused too
    python scripts/seed_synthetic_analytics.py --client acme-co   # one client
    python scripts/seed_synthetic_analytics.py --days 180
    python scripts/seed_synthetic_analytics.py --purge            # synthetic rows only
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

# Make the app package importable when run as a plain script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select  # noqa: E402

from app.db.session import get_session_factory  # noqa: E402
from app.models.client import Client  # noqa: E402
from app.models.enums import ClientStatus, UserRole  # noqa: E402
from app.models.user import User  # noqa: E402
from app.services.alert_service import AlertService  # noqa: E402
from app.services.demo_data_service import DEFAULT_DAYS, DemoDataService  # noqa: E402


def _target_clients(session, slugs: list[str] | None, all_statuses: bool) -> list[Client]:
    stmt = select(Client).order_by(Client.name.asc())
    if slugs:
        stmt = stmt.where(Client.slug.in_(slugs))
    elif not all_statuses:
        # Draft clients are mid-onboarding; they're seeded on create anyway.
        stmt = stmt.where(Client.status == ClientStatus.active)
    return list(session.scalars(stmt).all())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--client",
        action="append",
        dest="clients",
        metavar="SLUG",
        help="Client slug to seed (repeatable). Defaults to every active client.",
    )
    parser.add_argument("--days", type=int, default=DEFAULT_DAYS, help="Days of history to seed.")
    parser.add_argument(
        "--all-statuses",
        action="store_true",
        help="Include draft/paused/archived clients, not just active ones.",
    )
    parser.add_argument(
        "--purge",
        action="store_true",
        help="Delete synthetic analytics rows instead of seeding. Real data is left alone.",
    )
    args = parser.parse_args()

    if args.days < 1:
        parser.error("--days must be at least 1")

    today = date.today()
    session = get_session_factory()()
    try:
        clients = _target_clients(session, args.clients, args.all_statuses)
        if not clients:
            print("No matching clients found — nothing to do.")
            return

        service = DemoDataService(session)

        if args.purge:
            removed = sum(service.purge_client(c) for c in clients)
            session.commit()
            print(f"✓ Removed {removed} synthetic analytics rows across {len(clients)} client(s).")
            return

        # Seeded records are attributed to an admin so assignee/creator columns
        # aren't null; any admin will do.
        actor = session.scalar(select(User).where(User.role == UserRole.admin).limit(1))

        totals = {"analytics": 0, "campaigns": 0, "tasks": 0, "events": 0, "protected": 0}
        for client in clients:
            result = service.seed_client(
                client, days=args.days, today=today, actor_id=actor.id if actor else None
            )
            session.commit()
            alerts = AlertService(session).evaluate(client.id)
            totals["analytics"] += result.analytics_rows
            totals["campaigns"] += result.campaigns
            totals["tasks"] += result.plan_tasks
            totals["events"] += result.events
            totals["protected"] += result.protected_cells
            note = (
                f", {result.protected_cells} cell(s) preserved as real data"
                if result.protected_cells
                else ""
            )
            print(
                f"✓ {client.name} ({client.slug}): {result.analytics_rows} analytics rows, "
                f"{result.campaigns} campaigns, {result.plan_tasks} tasks, "
                f"{result.events} calendar items, {alerts.opened + alerts.updated} alerts{note}."
            )

        print(
            f"\nDone — {len(clients)} client(s), {args.days} days: "
            f"{totals['analytics']} analytics rows, {totals['campaigns']} campaigns, "
            f"{totals['tasks']} tasks, {totals['events']} calendar items."
        )
        if totals["protected"]:
            print(f"Left {totals['protected']} existing real data cell(s) untouched.")
    finally:
        session.close()


if __name__ == "__main__":
    main()
