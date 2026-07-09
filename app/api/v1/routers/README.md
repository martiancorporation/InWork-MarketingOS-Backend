# app/api/v1/routers/

One file per resource/feature. Each file exposes an `APIRouter` with the HTTP
endpoints for that domain and nothing more — parse the request, call the
matching service, shape the response with a schema.

Keep routers thin and free of business logic or direct database access.
