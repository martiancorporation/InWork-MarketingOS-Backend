"""Base class every client-scoped AI agent must extend.

It guarantees three things so no downstream agent can "forget" the client's
rules:

1. On construction it loads the client's **current** intelligence context
   (directive preamble + capability flags) — always the latest version.
2. ``system_prompt`` prepends that preamble to the agent's own instructions.
3. ``ensure_allowed`` turns capability flags into hard gates (e.g. an agent that
   would generate copy calls ``ensure_allowed("ai_text_generation")`` and is
   blocked deterministically when the client forbade AI-generated text).

Concrete agents (report writer, recommender, ad copy, assistant, …) subclass
this and set ``feature`` for usage attribution.
"""

from __future__ import annotations

import uuid

from sqlalchemy.orm import Session

from app.ai.features import AiFeature
from app.ai.usage import AiUsageContext
from app.core.exceptions import AppError
from app.integrations.anthropic.client import AnthropicClient
from app.integrations.embeddings.base import EmbeddingClient
from app.services.intelligence.context_service import ClientContext, ContextService


class CapabilityDeniedError(AppError):
    status_code = 409
    code = "capability_denied"


class ClientAgent:
    feature: str = AiFeature.UNKNOWN

    def __init__(
        self,
        db: Session,
        client_id: uuid.UUID,
        *,
        embedder: EmbeddingClient | None = None,
        ai_client: AnthropicClient | None = None,
    ) -> None:
        self.db = db
        self.client_id = client_id
        self._embedder = embedder
        self.context: ClientContext = ContextService(db, embedder).build(client_id)
        self.ai = ai_client or AnthropicClient(
            AiUsageContext(feature=self.feature, client_id=client_id)
        )

    def system_prompt(self, base_instructions: str) -> str:
        """The agent's instructions, prefixed with the client's rule preamble."""
        return f"{self.context.preamble}\n\n---\n\n{base_instructions}"

    def allows(self, capability: str) -> bool:
        return self.context.allows(capability)

    def ensure_allowed(self, capability: str) -> None:
        if not self.context.allows(capability):
            raise CapabilityDeniedError(f"This client's rules forbid '{capability}'.")

    def retrieve(self, query: str, *, top_k: int | None = None) -> list[str]:
        """Semantic RAG snippets relevant to ``query`` for this client."""
        ctx = ContextService(self.db, self._embedder).build(
            self.client_id, query=query, top_k=top_k
        )
        return [chunk.text for chunk, _score in ctx.retrieved]
