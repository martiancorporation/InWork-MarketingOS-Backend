# app/ai/

The AI orchestration layer. Turns client data into intelligence by combining the
LLM client (`integrations/llm` — OpenRouter today) with templates from
`prompts/`, then parsing the model's output into typed results.

One file per AI feature — `health_score.py`, `executive_brief.py`,
`recommendations.py`, `watchdog.py`, `consistency.py`, `opportunities.py`,
`content_review.py`, `assistant.py`, `global_assistant.py`, `brand_extraction.py`,
`missing_info.py`, `summary.py`, `directives.py`, `daily_report.py`, `qa.py` —
plus the shared pieces:

- `features.py` — the `AiFeature` labels every call is attributed with.
- `model_router.py` — per-feature model tiering (cheap / mid / flagship).
- `usage.py`, `pricing.py`, `cost_optimization.py` — token/cost accounting.
- `parsers.py` — validate/normalize model output into schemas.
- `attachments.py` — fence uploaded files into a prompt as data, never instructions.

Every feature degrades to a deterministic fallback when the AI provider is
unconfigured. Prompt *text* never lives here — it lives in `app/prompts/`.
