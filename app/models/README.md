# app/models/

SQLAlchemy ORM models — the database entities. One file per entity (or closely
related group) to keep files small.

Models describe *shape and relationships only*. No business logic, no queries
(those go in `repositories/`), no request/response shaping (that is `schemas/`).

## Conventions
- Import building blocks from `app.models.base` (`Base`, `GUID`, `TZDateTime`,
  `pg_enum`, and the mixins).
- All enums live in `enums.py`; use `pg_enum(EnumClass, "pg_type_name")` on a column.
- Timestamps are `timestamptz`; primary keys are UUIDs.
- Client-owned rows use `ON DELETE CASCADE`; references to `users` use
  `ON DELETE SET NULL` (so removing a user never deletes their history).
- Register every new model in `__init__.py` so Alembic and `Base.metadata` see it.
- Not everything is an enum: columns whose value set is app-defined and grows
  with every new feature (`audit_log.action`, `messages.category`,
  `reports.scope`) are plain indexed strings instead — a DB enum would force a
  migration every time a new value is needed. Reserve `pg_enum()` for genuinely
  closed, stable sets.

## File map
| File | Tables |
| --- | --- |
| `enums.py` | all enum definitions |
| `user.py` | users, sessions |
| `client.py` | clients, client_brand_colors, client_brand_fonts, client_platforms |
| `assignment.py` | client_assignments (which users can access which clients) |
| `contact.py` | client_contacts |
| `compliance.py` | compliance_entries, compliance_docs |
| `document.py` | documents |
| `integration.py` | integrations |
| `event.py` | marketing_events, event_posts, event_ads, event_assets, event_activity |
| `plan.py` | plan_tasks |
| `analytics.py` | analytics_daily, strategy_visuals |
| `conversation.py` | conversations, messages, message_recipients, message_attachments |
| `ai.py` | ai_chats, ai_chat_messages, ai_sources, ai_chat_sources |
| `recommendation.py` | recommendation_actions |
| `report.py` | reports |
| `audit.py` | audit_log |
