# prompts/brand_extraction/

Prompts for the AI-assisted onboarding **brand extraction** step — turning a
client website or brand document into a structured brand theme (palette, fonts,
tone, imagery direction).

- `system.txt` — the analyst role + strict output contract (JSON only).
- `user_template.txt` — per-request template; `{website}` / `{text}` are filled
  at call time by `app/ai/brand_extraction.py`.
