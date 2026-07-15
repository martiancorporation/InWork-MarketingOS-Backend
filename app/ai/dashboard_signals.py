"""Real, grounded signals the dashboard AI engines reason over.

Assembled once per dashboard request (in ``DashboardService``) from the client's
own data — metrics, integrations, pending approvals, onboarding/profile state,
brand rules. Both the Claude path (as prompt facts) and the deterministic
fallback (as the source of every number) read from here, so the dashboard is
always grounded in what is actually known about the client — never invented.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class DashboardSignals:
    spend: float = 0.0
    leads: int = 0
    cpl: float = 0.0
    connected_integrations: int = 0
    pending_integrations: int = 0
    pending_approvals: int = 0
    onboarding_completed: bool = False
    has_profile: bool = False
    banned_words: list[str] = field(default_factory=list)
    required_phrases: list[str] = field(default_factory=list)
    rules: list[str] = field(default_factory=list)
    platforms: list[str] = field(default_factory=list)
    goals: str | None = None
    brand_voice: str | None = None

    def as_prompt_facts(self) -> str:
        """A compact, human-readable fact sheet for the model prompt."""
        lines = [
            f"- Spend to date: ${self.spend:,.2f}",
            f"- Leads to date: {self.leads}",
            f"- Cost per lead: ${self.cpl:,.2f}",
            f"- Integrations connected: {self.connected_integrations}",
            f"- Integrations pending/not connected: {self.pending_integrations}",
            f"- Calendar posts awaiting client approval: {self.pending_approvals}",
            f"- Onboarding complete: {'yes' if self.onboarding_completed else 'no'}",
            f"- Intelligence profile ready: {'yes' if self.has_profile else 'no'}",
            f"- Channels: {', '.join(self.platforms) or 'none specified'}",
            f"- Goals: {self.goals or 'not specified'}",
            f"- Brand voice: {self.brand_voice or 'not specified'}",
        ]
        if self.banned_words:
            lines.append(f"- Banned words/phrases: {', '.join(self.banned_words)}")
        if self.required_phrases:
            lines.append(f"- Required phrases: {', '.join(self.required_phrases)}")
        if self.rules:
            lines.append(f"- Special rules: {'; '.join(self.rules)}")
        return "\n".join(lines)
