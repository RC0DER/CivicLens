# No `# syntax=` directive on purpose.
#
# That directive makes the builder pull docker/dockerfile from Docker Hub
# before it reads a single line, which fails the whole build when a hosted
# builder cannot reach the registry. Nothing here needs BuildKit-specific
# syntax - these are plain instructions the default frontend understands - so
# the dependency is not worth the outage.
#
# Repo-root build: the image carries both the API and the portal, because the
# service serves them from one origin. Railway, Render and Fly all build this
# file with no further configuration.
#
# Single stage, no compilers. Every dependency ships a prebuilt manylinux
# wheel, so nothing is built from source: cryptography bundles OpenSSL, Pillow
# bundles libjpeg and zlib, pikepdf bundles libqpdf, psycopg[binary] bundles
# libpq. Installing a toolchain to compile them would add ~1.5 GB to the build
# and, on a modest builder, get the job OOM-killed (exit 137) before finishing.
#
# `--only-binary=:all:` makes that a rule rather than a hope: if a future
# dependency has no wheel, the build fails here with a clear message instead of
# quietly pulling in a compiler at deploy time.
# Base image comes from AWS's public mirror of the Docker official images,
# not from Docker Hub directly.
#
# Hosted builders hit Docker Hub's rate limits and outages constantly - this
# build failed twice with "dial tcp ... i/o timeout" against registry-1.docker.io
# before reading a line. public.ecr.aws/docker/library/python is the same
# upstream image, mirrored by AWS, with no pull limits for anonymous users.
# To go back to Docker Hub, use: FROM python:3.12-slim
FROM public.ecr.aws/docker/library/python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /srv

COPY backend/requirements.txt .
RUN pip install --only-binary=:all: -r requirements.txt

COPY backend/app ./app
COPY backend/migrations ./migrations
COPY backend/scripts ./scripts
COPY backend/alembic.ini backend/gunicorn.conf.py ./
COPY backend/docker-entrypoint.sh /usr/local/bin/entrypoint
COPY frontend ./frontend

# Absolute, so serving the portal never depends on how deep the app sits.
ENV FRONTEND_DIR=/srv/frontend

RUN chmod +x /usr/local/bin/entrypoint \
    && useradd --system --uid 10001 --home /srv civiclens \
    && mkdir -p /srv/data/evidence \
    && chown -R civiclens:civiclens /srv/data
USER civiclens

EXPOSE 8000

# Uses Python rather than curl, so the image needs no extra system packages.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import os,urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:'+os.getenv('PORT','8000')+'/health/live', timeout=4).status==200 else 1)"

ENTRYPOINT ["/usr/local/bin/entrypoint"]
CMD ["serve"]
