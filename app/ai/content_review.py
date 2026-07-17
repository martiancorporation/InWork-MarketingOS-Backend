"""Pre-publish AI content review — the review-step guardrail.

Checks a draft caption/post BEFORE a human approves it:
- **Compliance** (deterministic): active ``banned`` terms present / ``required``
  phrases missing, read straight from the client's compliance register — reliable,
  no model needed.
- **SEO** (deterministic heuristics): length, hashtags, a clear call-to-action.
- **Brand voice + polish** (AI, when configured): grounded in the client's rule
  preamble; degrades to the deterministic result when Claude is unconfigured.
"""

from __future__ import annotations

import logging

from app.ai.features import AiFeature
from app.ai.parsers import parse_json_object
from app.models.compliance import ComplianceEntry
from app.models.enums import ComplianceKind, SocialPlatform
from app.prompts.loader import load_prompt, render
from app.repositories.compliance_repository import ComplianceRepository
from app.schemas.content import ComplianceCheck, ContentReviewReport, SeoScore
from app.services.intelligence.client_agent import ClientAgent

logger = logging.getLogger("app.ai.content_review")

_SOCIAL = {
    SocialPlatform.instagram, SocialPlatform.facebook, SocialPlatform.tiktok,
    SocialPlatform.x, SocialPlatform.linkedin, SocialPlatform.pinterest,
}
_CTA_HINTS = (
    "shop", "learn", "sign up", "signup", "call", "book", "get", "discover",
    "contact", "buy", "download", "subscribe", "register", "order", "visit",
    "explore", "join", "claim", "start",
)


class ContentReviewAgent(ClientAgent):
    feature = AiFeature.CONTENT_REVIEW

    async def review(
        self, content: str, *, platform: SocialPlatform | None = None
    ) -> ContentReviewReport:
        entries, _ = ComplianceRepository(self.db).list_for_client(
            self.client_id, active_only=True, limit=500
        )
        compliance = _check_compliance(content, entries)
        seo = _seo_score(content, platform)
        base_issues = _deterministic_issues(compliance, seo)

        if not self.ai.is_configured:
            return ContentReviewReport(
                seo=seo,
                compliance=compliance,
                brand_voice_aligned=None,
                issues=base_issues,
                suggestions=_deterministic_suggestions(seo),
                ai_generated=False,
            )

        precheck = (
            f"compliance passed={compliance.passed} "
            f"violations={compliance.violations} missing={compliance.missing_required}; "
            f"seo_score={seo.score} seo_findings={seo.findings}"
        )
        prompt = render(
            load_prompt("content_review/user_template.txt"),
            {
                "platform": platform.value if platform else "(unspecified)",
                "precheck": precheck,
                "content": content,
            },
        )
        try:
            raw = await self.ai.complete(
                system=self.system_prompt(load_prompt("content_review/system.txt")),
                prompt=prompt,
                context=None,
            )
        except Exception:
            logger.warning("Content review AI failed for client %s", self.client_id, exc_info=True)
            return ContentReviewReport(
                seo=seo, compliance=compliance, brand_voice_aligned=None,
                issues=base_issues, suggestions=_deterministic_suggestions(seo),
                ai_generated=False,
            )
        payload = parse_json_object(raw) or {}
        ai_issues = [str(i) for i in payload.get("issues", []) if isinstance(i, str)]
        ai_suggestions = [str(x) for x in payload.get("suggestions", []) if isinstance(x, str)]
        return ContentReviewReport(
            seo=seo,
            compliance=compliance,
            brand_voice_aligned=bool(payload.get("brand_voice_aligned")),
            issues=_dedupe(base_issues + ai_issues),
            suggestions=_dedupe(_deterministic_suggestions(seo) + ai_suggestions),
            ai_generated=True,
        )


def _check_compliance(content: str, entries: list[ComplianceEntry]) -> ComplianceCheck:
    low = content.lower()
    violations: list[str] = []
    missing: list[str] = []
    for e in entries:
        text = (e.text or "").strip()
        if not text:
            continue
        if e.kind == ComplianceKind.banned and text.lower() in low:
            violations.append(text)
        elif e.kind == ComplianceKind.required and text.lower() not in low:
            missing.append(text)
    return ComplianceCheck(
        passed=not violations and not missing,
        violations=violations,
        missing_required=missing,
    )


def _seo_score(content: str, platform: SocialPlatform | None) -> SeoScore:
    text = content.strip()
    findings: list[str] = []
    score = 100
    if len(text) < 20:
        score -= 30
        findings.append("Content is very short — add more substance.")
    elif len(text) > 2200:
        score -= 10
        findings.append("Content is very long for a social post — consider trimming.")
    low = text.lower()
    if not any(hint in low for hint in _CTA_HINTS):
        score -= 15
        findings.append("No clear call-to-action detected.")
    if platform in _SOCIAL and "#" not in text:
        score -= 15
        findings.append("No hashtags — add a few relevant, on-brand tags.")
    if text.count("#") > 30:
        score -= 5
        findings.append("Too many hashtags may read as spammy.")
    return SeoScore(score=max(0, min(100, score)), findings=findings)


def _deterministic_issues(compliance: ComplianceCheck, seo: SeoScore) -> list[str]:
    issues: list[str] = []
    for v in compliance.violations:
        issues.append(f"Uses a banned term: '{v}'.")
    for m in compliance.missing_required:
        issues.append(f"Missing a required phrase: '{m}'.")
    issues.extend(seo.findings)
    return issues


def _deterministic_suggestions(seo: SeoScore) -> list[str]:
    if seo.score >= 85:
        return ["Looks solid — a human reviewer can do a final on-brand pass."]
    return ["Address the flagged SEO items before sending for approval."]


def _dedupe(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in items:
        k = i.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(i.strip())
    return out
