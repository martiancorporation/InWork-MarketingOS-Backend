# Production image for the FastAPI backend.
FROM python:3.12-slim AS base

# Faster, cleaner Python in containers.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install deps first for better layer caching.
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# Headless Chromium for brand extraction (system-wide path, before the user
# switch, so the non-root user can run it).
ENV PLAYWRIGHT_BROWSERS_PATH=/ms-playwright
RUN playwright install --with-deps chromium

# App code.
COPY . .

# Run as a non-root user.
RUN useradd --create-home --uid 1000 appuser
USER appuser

EXPOSE 8000

# APP_ENV should be provided at run time (e.g. production). Secrets come from
# the environment, never baked into the image.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
