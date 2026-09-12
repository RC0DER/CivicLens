# syntax=docker/dockerfile:1
# Multi-stage: wheels are built with a toolchain that never reaches the
# runtime image.
FROM python:3.12-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libqpdf-dev zlib1g-dev libjpeg62-turbo-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

# Runtime libraries only - no compilers in the shipped image.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libqpdf29 libjpeg62-turbo zlib1g curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY requirements.txt .
RUN pip install --only-binary=:all: -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini gunicorn.conf.py ./

RUN useradd --system --uid 10001 --home /srv civiclens \
    && mkdir -p /srv/data/evidence \
    && chown -R civiclens:civiclens /srv/data
USER civiclens

EXPOSE 8000

# Liveness only: readiness touches the database and belongs to the
# orchestrator's readiness probe, not to the container healthcheck.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/health/live || exit 1

# One image, three services. PROFILE and the presence of the intake keys are
# what differ between them - see docker-compose.yml.
CMD ["gunicorn", "app.main:app", "-c", "gunicorn.conf.py"]
