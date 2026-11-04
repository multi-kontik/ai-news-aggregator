# Image for the daily pipeline: it runs main.py once and exits. The schedule
# lives outside the image, in Render's cron job (render.yaml).
#
# Build from the repo root: docker build -f docker/Dockerfile .

# Same uv version as local development, so uv.lock is read identically.
FROM ghcr.io/astral-sh/uv:0.12.3 AS uv

FROM python:3.13-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates tzdata \
 && rm -rf /var/lib/apt/lists/*

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, in their own layer, so code-only changes rebuild in seconds.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY README.md main.py ./
COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 app
USER app

# TZ only affects the date shown in the email, which should be the reader's
# day; the run time itself is set by the cron schedule, in UTC.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=America/Toronto

CMD ["python", "main.py"]
