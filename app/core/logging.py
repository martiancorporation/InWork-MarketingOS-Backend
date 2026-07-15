"""Minimal, dependency-free logging setup with request-id correlation."""

from __future__ import annotations

import logging

from app.core.request_context import RequestIdFilter


def configure_logging(*, debug: bool = False) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-8s | %(name)s | req=%(request_id)s | %(message)s"
        )
    )
    handler.addFilter(RequestIdFilter())

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.DEBUG if debug else logging.INFO)
