"""FastAPI application entry point.

Assembles the app: logging, CORS, centralized error handlers, and the versioned
API router. No business logic lives here. Run with:

    uvicorn app.main:app --reload
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.api import api_router
from app.core.config import get_settings
from app.core.exceptions import register_exception_handlers
from app.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(debug=settings.app.debug)

    # Interactive docs + the raw OpenAPI schema enumerate every route/schema —
    # keep them off in production (no auth gate exists in front of them).
    docs_enabled = not settings.app.is_production
    app = FastAPI(
        title=settings.app.app_name,
        version="1.0.0",
        docs_url="/docs" if docs_enabled else None,
        redoc_url="/redoc" if docs_enabled else None,
        openapi_url="/openapi.json" if docs_enabled else None,
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

    # True outermost: reject an oversized body before routing/auth/parsing —
    # added last so Starlette wraps it around everything else.
    from app.core.middleware import BodySizeLimitMiddleware

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.app.max_request_body_bytes)

    register_exception_handlers(app)
    app.include_router(api_router, prefix=settings.app.api_v1_prefix)

    @app.get("/health", tags=["health"], summary="Liveness check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"], summary="Readiness check (verifies DB connectivity)")
    def ready() -> JSONResponse:
        from sqlalchemy import text

        from app.db.session import get_engine

        try:
            with get_engine().connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse(status_code=503, content={"status": "not_ready"})
        return JSONResponse(status_code=200, content={"status": "ready"})

    return app


app = create_app()
