# prompts/plan_generation/

Prompts for AI-generated content calendars — a manager asks in chat ("create a
content calendar for this month") and gets back a structured, guardrail-respecting
set of draft posts to review, assign, and track. `system.txt`/`user_template.txt`
generate a full month; `single_item_system.txt`/`single_item_user_template.txt`
regenerate exactly one already-scheduled item (the "client changed their mind
about one day" case), leaving every other day untouched.
