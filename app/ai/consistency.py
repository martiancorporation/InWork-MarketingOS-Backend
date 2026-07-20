"""Onboarding Consistency Agent.

Reads everything the operator entered across the onboarding steps and flags
*contradictions* between them — e.g. "business type: steel" but the compliance
notes talk about selling cars, or a brand voice that violates the client's own
banned-words list. This is the review-step guardrail: catch
disconnects before the client is created.

Uses Claude when configured (it's good at cross-field contradiction reasoning);
otherwise falls back to a deterministic rule set mirroring the web's
``runConsistencyCheck``, so the endpoint always returns something useful. All
onboarding text is treated as untrusted data, never as instructions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from app.ai.parsers import parse_json_object
from app.ai.usage import AiUsageContext
from app.integrations.anthropic.client import AnthropicClient
from app.models.client import Client
from app.models.enums import ConsistencyLevel
from app.prompts.loader import load_prompt, render

logger = logging.getLogger("app.ai.consistency")

_VALID_LEVELS = {level.value for level in ConsistencyLevel}


@dataclass
class ConsistencyFindingResult:
    level: str
    message: str
    step: str | None = None


@dataclass
class ConsistencyResult:
    findings: list[ConsistencyFindingResult] = field(default_factory=list)
    ai_generated: bool = False


class ConsistencyAgent:
    def __init__(self, client: AnthropicClient | None = None) -> None:
        self._client = client or AnthropicClient()

    async def check(
        self, client: Client, context: AiUsageContext | None = None
    ) -> ConsistencyResult:
        corpus = _corpus(client)
        if not self._client.is_configured or not corpus.strip():
            return self._fallback(client)
        try:
            system = load_prompt("consistency/system.txt")
            prompt = render(
                load_prompt("consistency/user_template.txt"),
                {"client_name": client.name or "the client", "corpus": corpus},
            )
            raw = await self._client.complete(
                system=system, prompt=prompt, max_tokens=2000, context=context
            )
        except Exception:
            logger.warning("Consistency agent failed for client %s", client.id, exc_info=True)
            return self._fallback(client)

        payload = parse_json_object(raw)
        if payload is None or not isinstance(payload.get("findings"), list):
            return self._fallback(client)

        findings: list[ConsistencyFindingResult] = []
        for item in payload["findings"]:
            if not isinstance(item, dict):
                continue
            level = str(item.get("level") or "").lower()
            message = str(item.get("message") or "").strip()
            if level not in _VALID_LEVELS or not message:
                continue
            step = item.get("step")
            findings.append(
                ConsistencyFindingResult(
                    level=level, message=message, step=str(step) if step else None
                )
            )
        if not findings:
            findings.append(_ok())
        return ConsistencyResult(findings=findings, ai_generated=True)

    # ---- deterministic fallback (mirrors web runConsistencyCheck) ------ #

    def _fallback(self, client: Client) -> ConsistencyResult:
        findings: list[ConsistencyFindingResult] = []
        haystack = " ".join(filter(None, [client.about_brand, client.brand_voice])).lower()
        for word in (
            e.text
            for e in client.compliance_entries
            if getattr(e.kind, "value", e.kind) == "banned"
        ):
            w = (word or "").strip().lower()
            if w and w in haystack:
                findings.append(
                    ConsistencyFindingResult(
                        level=ConsistencyLevel.error.value,
                        message=(
                            f'Brand copy contains the banned word "{word}", which '
                            f"contradicts the compliance rules."
                        ),
                        step="compliance",
                    )
                )

        selected = {p.channel for p in client.platforms}
        connected = {
            getattr(i.key, "value", i.key)
            for i in client.integrations
            if getattr(i.status, "value", i.status) == "connected"
        }
        if selected and not connected:
            findings.append(
                ConsistencyFindingResult(
                    level=ConsistencyLevel.warn.value,
                    message=(
                        "Platforms are selected but no integrations are connected yet — "
                        "connect the accounts to start collecting data."
                    ),
                    step="connect-platforms",
                )
            )

        goals = (client.goals or "").strip()
        if len(goals) < 20:
            findings.append(
                ConsistencyFindingResult(
                    level=ConsistencyLevel.warn.value,
                    message="Goals are missing or very short — add detail for a better strategy.",
                    step="client-goals",
                )
            )

        if not findings:
            findings.append(_ok())
        return ConsistencyResult(findings=findings, ai_generated=False)


def _ok() -> ConsistencyFindingResult:
    return ConsistencyFindingResult(
        level=ConsistencyLevel.ok.value,
        message="No inconsistencies detected across the onboarding inputs.",
    )


def _corpus(client: Client) -> str:
    """Compact, labelled dump of the onboarding inputs for the model to reconcile."""
    parts: list[str] = []

    def add(label: str, value: str | None) -> None:
        if (value or "").strip():
            parts.append(f"{label}: {value.strip()}")

    add("Client name", client.name)
    add("Business type", client.business_type)
    add("Industry", client.industry)
    add("Website", client.website)
    add("Location", client.location)
    add("Markets", client.markets)
    add("About the brand", client.about_brand)
    add("Brand voice", client.brand_voice)
    add("Goals", client.goals)
    if client.platforms:
        parts.append("Platforms: " + ", ".join(p.channel for p in client.platforms))
    if client.compliance_entries:
        parts.append(
            "Compliance & rules:\n"
            + "\n".join(
                f"- [{getattr(e.kind, 'value', e.kind)}] {e.text}"
                for e in client.compliance_entries
            )
        )
    return "\n".join(parts)
