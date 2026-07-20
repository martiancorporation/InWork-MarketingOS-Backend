"""Platform-wide AI assistant use-cases ("Ask AI about my portfolio").

Access scoping is enforced HERE, not at the router: an admin's portfolio is every
client; everyone else only ever reasons over the clients assigned to them. The
service assembles a bounded fact sheet from those clients and hands it to
``GlobalAssistantAgent``, which degrades to a deterministic summary when Claude is
unconfigured. The endpoint is stateless — nothing is persisted.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.ai.features import AiFeature
from app.ai.global_assistant import GlobalAssistantAgent
from app.ai.usage import AiUsageContext
from app.integrations.anthropic.client import AnthropicClient
from app.models.enums import UserRole
from app.models.user import User
from app.repositories.client_repository import ClientRepository
from app.schemas.assistant import (
    GlobalAssistantAskResponse,
    GlobalAssistantTurn,
)

# Cap how many clients feed the prompt so the fact sheet (and token cost) stays
# bounded regardless of portfolio size.
_MAX_CLIENTS = 50


class GlobalAssistantService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.clients = ClientRepository(db)

    async def ask(
        self, user: User, content: str, history: list[GlobalAssistantTurn] | None = None
    ) -> GlobalAssistantAskResponse:
        rows = self._accessible_clients(user)
        scope_label = (
            "all clients" if user.role == UserRole.admin else f"{len(rows)} assigned client(s)"
        )
        facts = _portfolio_facts(rows)

        agent = GlobalAssistantAgent(
            ai_client=AnthropicClient(AiUsageContext(feature=AiFeature.ASSISTANT, user_id=user.id))
        )
        answer = await agent.answer(
            content,
            platform_facts=facts,
            scope_label=scope_label,
            history=[(t.role, t.content) for t in (history or [])],
        )
        return GlobalAssistantAskResponse(
            answer=answer,
            scope=scope_label,
            clients_considered=len(rows),
            ai_generated=AnthropicClient().is_configured,
        )

    def _accessible_clients(self, user: User) -> list:
        """The clients this user may reason over — admins: all; others: assigned."""
        if user.role == UserRole.admin:
            rows, _ = self.clients.list_all(offset=0, limit=_MAX_CLIENTS)
        else:
            rows, _ = self.clients.list_assigned(user.id, offset=0, limit=_MAX_CLIENTS)
        return rows


def _portfolio_facts(clients: list) -> str:
    if not clients:
        return ""
    total_spend = sum(float(c.spend_total) for c in clients)
    total_leads = sum(int(c.leads_total) for c in clients)
    lines = [
        f"Portfolio totals: {len(clients)} client(s), "
        f"${total_spend:,.2f} spend, {total_leads} leads.",
        "",
        "Clients:",
    ]
    for c in clients:
        status = getattr(c.status, "value", c.status)
        lines.append(
            f"- {c.name} (industry: {c.industry or 'n/a'}, status: {status}): "
            f"${float(c.spend_total):,.2f} spend, {c.leads_total} leads, "
            f"${float(c.cpl):,.2f} CPL"
        )
    return "\n".join(lines)
