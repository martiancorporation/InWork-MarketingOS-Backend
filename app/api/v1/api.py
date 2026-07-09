"""Aggregates every v1 feature router into a single router."""

from __future__ import annotations

from fastapi import APIRouter

from app.api.v1.routers import auth, clients

api_router = APIRouter()
api_router.include_router(auth.router)
api_router.include_router(clients.router)
