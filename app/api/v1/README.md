# app/api/v1/

Version 1 of the API. `api.py` aggregates every feature router from `routers/`
into a single `APIRouter` that `app/main.py` mounts under the v1 prefix.

Add a new endpoint file to `routers/`, then register it in `api.py`.
