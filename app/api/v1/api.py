"""Aggregates every v1 feature router into a single router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import (
    ai,
    ai_usage,
    alerts,
    analytics,
    assignments,
    assistant,
    audit,
    auth,
    automation,
    calendar,
    campaigns,
    clients,
    compliance,
    content,
    conversations,
    global_assistant,
    integrations,
    intelligence,
    notifications,
    plans,
    reports,
    uploads,
    users,
)

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(clients.router)
api_router.include_router(assignments.router)
api_router.include_router(assistant.router)
api_router.include_router(global_assistant.router)
api_router.include_router(audit.router)
api_router.include_router(ai_usage.router)
api_router.include_router(uploads.router)
api_router.include_router(intelligence.router)
api_router.include_router(calendar.router)
api_router.include_router(ai.router)
api_router.include_router(conversations.router)
api_router.include_router(reports.router)
api_router.include_router(compliance.router)
api_router.include_router(content.router)
api_router.include_router(analytics.router)
api_router.include_router(campaigns.router)
api_router.include_router(alerts.router)
api_router.include_router(integrations.router)
api_router.include_router(plans.router)
api_router.include_router(automation.router)
api_router.include_router(notifications.router)
