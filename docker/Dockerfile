# Image for the daily pipeline. supercronic runs main.py on the schedule in
# docker/crontab and keeps the container alive between runs.
#
# Build from the repo root: docker build -f docker/Dockerfile .

# Same uv version as local development, so uv.lock is read identically.
FROM ghcr.io/astral-sh/uv:0.12.3 AS uv

FROM python:3.13-slim

ARG TARGETARCH
ARG SUPERCRONIC_VERSION=v0.2.50
ARG SUPERCRONIC_SHA256_AMD64=d2d7c97fbfefc763d5bbcbbb3f8d61f9dd3410ce5d05a737c414994b80b8cdd0
ARG SUPERCRONIC_SHA256_ARM64=a1c0af9872e8de89a1ce5c1b596e26c42917d5a2cf914d98faee2e28156899e2

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tzdata \
 && rm -rf /var/lib/apt/lists/*

RUN set -eu; \
    case "${TARGETARCH:-amd64}" in \
      amd64) sha="$SUPERCRONIC_SHA256_AMD64" ;; \
      arm64) sha="$SUPERCRONIC_SHA256_ARM64" ;; \
      *) echo "No supercronic checksum for architecture ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL -o /usr/local/bin/supercronic \
      "https://github.com/aptible/supercronic/releases/download/${SUPERCRONIC_VERSION}/supercronic-linux-${TARGETARCH:-amd64}"; \
    echo "${sha}  /usr/local/bin/supercronic" | sha256sum -c -; \
    chmod +x /usr/local/bin/supercronic

COPY --from=uv /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv

WORKDIR /app

# Dependencies first, in their own layer, so code-only changes rebuild in seconds.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev --no-install-project

COPY README.md main.py ./
COPY src ./src
COPY docker/crontab ./docker/crontab
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev

RUN useradd --create-home --uid 1000 app
USER app

# TZ makes the crontab's 7:00 mean Toronto time, including daylight saving.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    TZ=America/Toronto

CMD ["supercronic", "/app/docker/crontab"]
