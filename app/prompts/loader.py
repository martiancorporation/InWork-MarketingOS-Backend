"""Load prompt templates from disk and fill simple ``{placeholder}`` tokens.

Prompts live as ``.txt`` files under this package so they can be reviewed and
iterated without touching Python.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_PROMPTS_DIR = Path(__file__).resolve().parent


@lru_cache
def load_prompt(relative_path: str) -> str:
    path = _PROMPTS_DIR / relative_path
    if not path.is_file():
        raise FileNotFoundError(f"Prompt template not found: {relative_path}")
    return path.read_text(encoding="utf-8")


def render(template: str, values: dict[str, str]) -> str:
    """Replace ``{key}`` tokens with values (safe for prompts with other braces)."""
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", value)
    return result
