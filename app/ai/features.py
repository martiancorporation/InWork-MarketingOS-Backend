"""AI feature identifiers — the "where in the app" dimension of usage tracking.

Plain string constants (an open set, like audit actions): every AI surface uses
one when it calls the model, so usage can be grouped by origin. Add a new
constant when you add a new AI feature — no migration, no enum change.

Convention: ``area.action`` (dotted, lowercase).
"""

from __future__ import annotations


class AiFeature:
    BRAND_EXTRACTION = "onboarding.brand_extraction"
    CONSISTENCY_CHECK = "onboarding.consistency_check"
    CONTENT_REVIEW = "content.review"
    CLIENT_SUMMARY = "intelligence.client_summary"
    CLIENT_DIRECTIVES = "intelligence.client_directives"
    PROJECT_AI = "project_ai.chat"
    PLAN_GENERATION = "plan.generate"
    PLAN_CHAT_INTENT = "plan.chat_intent"
    DAY_CHAT = "day.chat"
    ASSISTANT = "assistant.global"
    INSIGHTS = "insights.generate"
    REPORT_NARRATIVE = "report.narrative"
    RECOMMENDATION = "recommendation.generate"
    HEALTH_SCORE = "dashboard.health_score"
    EXECUTIVE_BRIEF = "dashboard.executive_brief"
    WATCHDOG = "dashboard.watchdog"
    OPPORTUNITY = "dashboard.opportunity"
    QA_REVIEW = "qa.review"
    MISSING_INFO = "onboarding.missing_info"
    COST_OPTIMIZATION = "cost.optimization"
    UNKNOWN = "unknown"


# Clean, human-readable names for the usage/audit UI — presentation only.
# The stored `feature` value on AiUsageEvent rows is never renamed (other code
# filters/groups by that stable machine key); this is purely what a person
# reads on the Token Usage page instead of a raw dotted string like
# "plan.generate". A client-scoped event additionally shows "{label} / {client
# name}" (composed by the caller, since usage rows don't carry a client name).
FEATURE_LABELS: dict[str, str] = {
    AiFeature.BRAND_EXTRACTION: "Brand Extraction",
    AiFeature.CONSISTENCY_CHECK: "Consistency Check",
    AiFeature.CONTENT_REVIEW: "Content Review",
    AiFeature.CLIENT_SUMMARY: "Client Summary",
    AiFeature.CLIENT_DIRECTIVES: "Client Directives",
    AiFeature.PROJECT_AI: "AI Chat",
    AiFeature.PLAN_GENERATION: "AI Content Calendar",
    AiFeature.PLAN_CHAT_INTENT: "AI Chat / Plan Intent Check",
    AiFeature.DAY_CHAT: "Day Chat",
    AiFeature.ASSISTANT: "Global Assistant",
    AiFeature.INSIGHTS: "Insights",
    AiFeature.REPORT_NARRATIVE: "Report Narrative",
    AiFeature.RECOMMENDATION: "Recommendations",
    AiFeature.HEALTH_SCORE: "Health Score",
    AiFeature.EXECUTIVE_BRIEF: "Executive Brief",
    AiFeature.WATCHDOG: "KPI Watchdog",
    AiFeature.OPPORTUNITY: "Growth Opportunities",
    AiFeature.QA_REVIEW: "QA Review",
    AiFeature.MISSING_INFO: "Missing Info Check",
    AiFeature.COST_OPTIMIZATION: "Cost Optimization",
    AiFeature.UNKNOWN: "Unattributed",
}


def feature_label(feature: str) -> str:
    """Human-readable label for a stored feature key — falls back to the raw
    key itself for anything not yet in ``FEATURE_LABELS`` (a new feature
    constant added without updating this dict degrades gracefully rather than
    ever raising)."""
    return FEATURE_LABELS.get(feature, feature)
