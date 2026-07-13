"""AI feature identifiers — the "where in the app" dimension of usage tracking.

Plain string constants (an open set, like audit actions): every AI surface uses
one when it calls the model, so usage can be grouped by origin. Add a new
constant when you add a new AI feature — no migration, no enum change.

Convention: ``area.action`` (dotted, lowercase).
"""

from __future__ import annotations


class AiFeature:
    BRAND_EXTRACTION = "onboarding.brand_extraction"
    CLIENT_SUMMARY = "intelligence.client_summary"
    CLIENT_DIRECTIVES = "intelligence.client_directives"
    PROJECT_AI = "project_ai.chat"
    DAY_CHAT = "day.chat"
    ASSISTANT = "assistant.global"
    INSIGHTS = "insights.generate"
    REPORT_NARRATIVE = "report.narrative"
    RECOMMENDATION = "recommendation.generate"
    UNKNOWN = "unknown"
