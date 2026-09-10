"""Pluggable chat-completion (LLM) backend for the AI layer.

Default and only backend today is OpenRouter; the ``LLMClient`` protocol +
``get_llm_client()`` factory is the swap point for a different provider later.
"""

from app.integrations.llm.base import LLMClient
from app.integrations.llm.factory import get_llm_client

__all__ = ["LLMClient", "get_llm_client"]
