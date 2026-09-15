"""Dedupe record for the automatic month-ahead content-plan generation job.

One row per ``(client_id, period)`` the job has already generated a draft
for. The unique constraint **is** the idempotency mechanism (mirrors
``ReportEmailLog``): the sweep checks for an existing row before generating,
and a duplicate attempt (two overlapping ticks) hits ``IntegrityError`` on
insert, which the service treats as "already handled."
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import GUID, Base, TimestampMixin, TZDateTime, UUIDPrimaryKeyMixin


class AutoPlanGenerationLog(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "auto_plan_generation_log"
    __table_args__ = (UniqueConstraint("client_id", "period"),)

    client_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("clients.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # "YYYY-MM" — the month this run generated a draft *for* (next month,
    # relative to when the job fired), not the day it actually ran.
    period: Mapped[str] = mapped_column(String(7), nullable=False)
    item_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generated_at: Mapped[datetime] = mapped_column(TZDateTime, nullable=False)
