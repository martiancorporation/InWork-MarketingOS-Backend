# app/tasks/

Background and scheduled work that runs outside the request/response cycle
(syncing integration data, refreshing AI insights, sending digests).

- `scheduler.py` — registers periodic jobs.

Keep task definitions thin — call into `services/` for the real work.
