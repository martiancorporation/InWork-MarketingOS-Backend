# app/integrations/anthropic/

Client wrapper for the Anthropic (Claude) API. Handles auth, model selection,
and low-level request/response — model id and API key come from
`app/core/config`. Prompt content comes from `app/prompts/`, not this folder.
