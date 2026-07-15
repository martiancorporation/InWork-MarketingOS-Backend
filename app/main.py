"""FastAPI application entry point.

Assembles the app: logging, CORS, centralized error handlers, and the versioned
API router. No business logic lives here. Run with:

    uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(debug=settings.app.debug)

    app = FastAPI(
        title=settings.app.app_name,
        version="1.0.0",
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.security.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    if settings.app.audit_enabled:
        # Records every API request to the audit_log table. Registered last so
        # it wraps outermost and sees the final response status.
        from app.core.middleware import AuditMiddleware

        app.add_middleware(AuditMiddleware, prefix=settings.app.api_v1_prefix)

    # Outermost: assign/propagate a request id for log correlation.
    from app.core.request_context import RequestIdMiddleware

    app.add_middleware(RequestIdMiddleware)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.app.api_v1_prefix)

    @app.get("/health", tags=["health"], summary="Liveness check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
