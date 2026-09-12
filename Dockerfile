# syntax=docker/dockerfile:1
#
# Repo-root build: the image carries both the API and the portal, because the
# service serves them from one origin. Railway, Render and Fly all build this
# file with no further configuration.
FROM python:3.12-slim AS build

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential libqpdf-dev zlib1g-dev libjpeg62-turbo-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build
COPY backend/requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt


FROM python:3.12-slim AS runtime

RUN apt-get update && apt-get install -y --no-install-recommends \
      libqpdf29 libjpeg62-turbo zlib1g curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /srv
COPY --from=build /wheels /wheels
COPY backend/requirements.txt .
RUN pip install --no-index --find-links=/wheels -r requirements.txt && rm -rf /wheels

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/scripts ./scripts
COPY backend/alembic.ini backend/gunicorn.conf.py ./
COPY backend/docker-entrypoint.sh /usr/local/bin/entrypoint
COPY frontend ./frontend

# FRONTEND_DIR is relative to the app package, matching the local layout.
ENV FRONTEND_DIR=../frontend

RUN chmod +x /usr/local/bin/entrypoint \
    && useradd --system --uid 10001 --home /srv civiclens \
    && mkdir -p /srv/data/evidence \
    && chown -R civiclens:civiclens /srv/data
USER civiclens

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=15s --retries=3 \
  CMD curl -fsS "http://127.0.0.1:${PORT:-8000}/health/live" || exit 1

ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["serve"]
