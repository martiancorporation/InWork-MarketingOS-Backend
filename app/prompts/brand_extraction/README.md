# prompts/brand_extraction/

Prompts for the AI-assisted onboarding **brand extraction** step. The site is
rendered in a headless browser (`app/utils/render.py` — post-JS text, computed
brand colors/fonts, screenshot); one Claude *vision* call over the screenshot +
text writes summary/tone/imagery. Colors/fonts come primarily from the
measured computed styles, merged in `app/ai/brand_extraction.py`.

The same prompts serve the text-only fallback (plain httpx scrape) used when a
headless browser isn't available.

- `system.txt` — the analyst role + strict output contract (JSON only).
- `user_template.txt` — per-request template; `{website}` / `{text}` are filled
  at call time.
