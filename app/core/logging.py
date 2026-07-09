"""Minimal, dependency-free logging setup."""

from __future__ import annotations

import logging


def configure_logging(*, debug: bool = False) -> None:
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    )
