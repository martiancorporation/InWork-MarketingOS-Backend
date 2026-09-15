# app/integrations/llm/

The AI layer's single LLM provider boundary. `base.py` defines the `LLMClient`
contract (`complete` / `complete_with_image(s)` / `stream` / `is_configured`);
`factory.py`'s `get_llm_client()` is the ONE place that decides which concrete
backend to use. Every `app/ai/*` agent and client-scoped service calls
`get_llm_client()` — never a vendor class directly — so switching providers
later (or adding a second one) means changing this folder, not every caller.

Current backend: `openrouter.py` (OpenRouter's OpenAI-compatible
`/chat/completions` API — routes to Anthropic/OpenAI/Google/etc. models behind
one key). Model id and API key come from `app/core/config/ai.py`
(`OPENROUTER_*`). Prompt content comes from `app/prompts/`, not this folder.

To add another provider: implement `LLMClient` in a new module here, then
point `get_llm_client()` at it (by settings, if it should be switchable at
runtime).
