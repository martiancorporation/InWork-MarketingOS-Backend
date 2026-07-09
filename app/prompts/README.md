# app/prompts/

All LLM prompts, stored as data (not Python). Each feature gets its own folder
containing the prompt templates it needs, so prompts are easy to find, review,
and iterate on without touching code.

- `loader.py` — helper that reads a prompt template from disk by name.
- one subfolder per feature (`health_score/`, `recommendations/`, …), each
  typically holding a `system.txt` and a `user_template.txt`.

Convention: `system.txt` = the model's role/instructions; `user_template.txt` =
the per-request template with `{placeholders}` filled in at call time. Never put
secrets or credentials in a prompt file.
