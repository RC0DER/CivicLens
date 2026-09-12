# API-only image, for running the backend without the portal (docker-compose,
# or a deployment that serves the frontend from a CDN). The repo-root
# Dockerfile is the one that carries both, and is what Railway builds.
#
# No `# syntax=` directive on purpose: it makes the builder fetch
# docker/dockerfile from a registry before reading a line, and nothing here
# needs BuildKit-specific syntax.
#
# Base image comes from AWS's public mirror of the Docker official images.
# Hosted builders hit Docker Hub's rate limits and outages routinely; this is
# the same upstream image, with no anonymous pull limits.
# To use Docker Hub instead: FROM python:3.12-slim
#
# Single stage, no compilers: every dependency ships a prebuilt manylinux
# wheel, so nothing is built from source. cryptography bundles OpenSSL, Pillow
# bundles libjpeg, pikepdf bundles libqpdf, psycopg[binary] bundles libpq.
# `--only-binary=:all:` keeps it that way - a future dependency without a wheel
# fails the build with a clear message instead of quietly needing a toolchain.
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY requirements.txt .
RUN pip install --only-binary=:all: -r requirements.txt

COPY app ./app
COPY migrations ./migrations
COPY scripts ./scripts
COPY alembic.ini gunicorn.conf.py ./
COPY docker-entrypoint.sh /usr/local/bin/entrypoint

# No portal in this image.
ENV SERVE_FRONTEND=false

RUN chmod +x /usr/local/bin/entrypoint \
    && useradd --system --uid 10001 --home /srv civiclens \
    && mkdir -p /srv/data/evidence \
    && chown -R civiclens:civiclens /srv/data
USER civiclens

EXPOSE 8000

# Liveness only: readiness touches the database and belongs to the
# orchestrator's readiness probe, not to the container healthcheck. Uses
# Python so the image needs no extra system packages.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health/live', timeout=4).status==200 else 1)"

# One image, three services. PROFILE and which intake keys are present are what
# differ between them - see docker-compose.yml.
ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["serve"]
