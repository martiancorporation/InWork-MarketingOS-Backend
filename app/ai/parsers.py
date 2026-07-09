"""Helpers to parse model output into structured data.

Model responses are untrusted text; parse defensively and return ``None`` on
anything unexpected so callers can fall back instead of crashing.
"""

from __future__ import annotations

import json
from typing import Any


def parse_json_object(raw: str) -> dict[str, Any] | None:
    """Extract a JSON object from a model response.

    Tolerates surrounding prose or ```json fences by slicing to the outermost
    ``{ ... }``. Returns ``None`` if no valid object is found.
    """
    if not raw:
        return None
    start = raw.find("{")
    end = raw.rfind("}")
    if start == -1 or end == -1 or end <= start:
        return None
    try:
        parsed = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, dict) else None
