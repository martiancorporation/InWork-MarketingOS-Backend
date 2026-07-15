# syntax=docker/dockerfile:1
# Multi-stage build for the FastAPI backend.

# ---- builder: install deps into a venv -----------------------------------
FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Build deps only needed while compiling wheels; not carried into the runtime.
RUN apt-get update && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt


# ---- runtime --------------------------------------------------------------
FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

# Bring the pre-built venv over from the builder stage.
COPY --from=builder /opt/venv /opt/venv

# Headless Chromium for brand extraction. Installed system-wide (before the
# user switch) so the non-root user can run it. Optional: the app degrades to
# the httpx scrape if the browser is unavailable.
RUN playwright install --with-deps chromium

# App code + entrypoint.
COPY . .
RUN chmod +x /app/scripts/docker-entrypoint.sh

# Run as a non-root user.
RUN useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app /ms-playwright
USER appuser

EXPOSE 8000

# Liveness for orchestrators (compose/k8s). Hits the app's /health route.
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health').status==200 else 1)"

# The entrypoint runs migrations (when RUN_MIGRATIONS=1), then serves via
# gunicorn with uvicorn workers (per-worker supervision + graceful restarts).
ENTRYPOINT ["/app/scripts/docker-entrypoint.sh"]
CMD ["gunicorn", "app.main:app", \
     "-k", "uvicorn.workers.UvicornWorker", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "4", \
     "--timeout", "60", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-"]
