# app/

The application package. Everything the running backend needs lives here.

## Request flow (layered)
```
HTTP request
   │
   ▼
api/v1/routers/*   ← thin: parse & validate, call a service, return a schema
   │
   ▼
services/*         ← business logic & orchestration (the "what should happen")
   │
   ▼
repositories/*     ← data access only (the "how we read/write the DB")
   │
   ▼
models/*  +  db/   ← ORM entities and the database session
```

`schemas/` validate the request/response edges. `core/` provides config,
security, logging and error handling to every layer. `integrations/`, `ai/`
and `prompts/` power the AI and third-party features. Keep the arrows pointing
downward — routers never touch the DB directly, repositories never import
routers.
