# app/ai/

The AI orchestration layer. Turns client data into intelligence by combining the
Anthropic client (`integrations/anthropic`) with templates from `prompts/`, then
parsing the model's output into typed results.

- `engine.py` — shared helper: load a prompt, call the model, return the result.
- `health_score.py`, `executive_brief.py`, `recommendations.py`, `watchdog.py`,
  `consistency.py` — one file per AI feature.
- `parsers.py` — validate/normalize model output into schemas.

Prompt *text* never lives here — it lives in `app/prompts/`.
