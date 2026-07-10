"""Aggregates every v1 feature router into a single router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import ai_usage, assignments, audit, auth, clients, users

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(users.router)
api_router.include_router(clients.router)
api_router.include_router(assignments.router)
api_router.include_router(audit.router)
api_router.include_router(ai_usage.router)
