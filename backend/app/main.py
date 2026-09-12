"""CivicLens API.

Which routers exist depends on PROFILE. The dept service does not merely
forbid the intake routes - it never registers them.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, HTTPException, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.staticfiles import StaticFiles

from . import errors
from .config import get_settings
from .db import create_all, readiness
from .middleware import BodyLimitMiddleware, RequestContextMiddleware, SecurityHeadersMiddleware
from .observability import CONTENT_TYPE_LATEST, METRICS_AVAILABLE, configure_logging, generate_latest, log
from .routers import auth, demo, dept, investigator, public

settings = get_settings()
logger = logging.getLogger("civiclens")


@asynccontextmanager
async def lifespan(_: FastAPI):
    configure_logging()
    if not settings.is_production:
        # Production schema changes go through Alembic; auto-create would mask
        # a migration that was never written.
        create_all()
    log(logger, logging.INFO, "service starting",
        profile=settings.profile, env=settings.env, version=settings.version,
        holds_intake_keys=settings.holds_intake_keys, storage=settings.storage_backend)
    yield
    log(logger, logging.INFO, "service stopping")


app = FastAPI(
    lifespan=lifespan,
    title="CivicLens API",
    version=settings.version,
    description=(
        "Corruption reporting and transparency platform. Filing and tracking require no "
        "account; departmental and investigator access is authenticated and audited."
    ),
    # Interactive docs are useful internally and are noise on a public edge.
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

# Order matters: the outermost middleware runs first on the way in.
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(RequestContextMiddleware)
app.add_middleware(BodyLimitMiddleware)
app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_host_list)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,  # bearer tokens, never cookies
    allow_methods=["GET", "POST"],
    allow_headers=["Authorization", "Content-Type"],
    max_age=600,
)

errors.install(app)


@app.get("/health/live", tags=["ops"])
def live() -> dict:
    """Process is up. Deliberately does not touch the database, so a database
    blip does not cause an orchestrator to restart healthy processes."""
    return {"status": "ok", "service": settings.service_name, "version": settings.version}


@app.get("/health/ready", tags=["ops"])
def ready(response: Response) -> dict:
    checks = readiness()
    ok = all(checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return {
        "status": "ok" if ok else "degraded",
        "checks": checks,
        "profile": settings.profile,
        # Visible on purpose: an operator can confirm at a glance that the
        # public and dept services are running without the intake keys.
        "holds_intake_keys": settings.holds_intake_keys,
        "publishes_names_before_finding": settings.publish_names_before_finding,
    }


if settings.metrics_enabled:
    @app.get("/metrics", tags=["ops"], include_in_schema=False)
    def metrics(authorization: str | None = Header(default=None)) -> Response:
        if settings.metrics_token:
            expected = f"Bearer {settings.metrics_token}"
            if authorization != expected:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Metrics require a scrape token.")
        if not METRICS_AVAILABLE:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "prometheus_client is not installed.")
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)


if settings.profile in ("public", "all"):
    app.include_router(public.router, prefix="/api")

if settings.profile in ("dept", "investigator", "all"):
    app.include_router(auth.router, prefix="/api")
    app.include_router(dept.router, prefix="/api")

if settings.profile in ("investigator", "all"):
    app.include_router(investigator.router, prefix="/api")

# Mounted always; the route itself refuses unless DEMO_MODE is on, so the
# gate lives in one place rather than being split between wiring and handler.
app.include_router(demo.router, prefix="/api")

def resolve_frontend_dir(configured: str) -> str | None:
    """Find the portal's files, whatever the layout.

    The repository nests the app one level deeper than the container does
    (`backend/app` versus `/srv/app`), so a single relative path is right in
    one and wrong in the other - and getting it wrong serves a bare JSON 404 at
    the site root with no error anywhere in the logs. Each plausible base is
    tried and the first one holding an index.html wins.
    """
    if os.path.isabs(configured):
        return configured if os.path.isfile(os.path.join(configured, "index.html")) else None

    package = os.path.dirname(os.path.abspath(__file__))       # .../app
    candidates = [
        os.path.join(package, configured),                      # /srv/app/../frontend
        os.path.join(os.path.dirname(package), configured),     # backend/../frontend
        os.path.join(os.getcwd(), configured),
    ]
    for candidate in candidates:
        resolved = os.path.abspath(candidate)
        if os.path.isfile(os.path.join(resolved, "index.html")):
            return resolved
    return None


# The portal is mounted last, at the root, so every /api and /health route
# above takes precedence over a static file of the same name.
if settings.serve_frontend:
    _frontend = resolve_frontend_dir(settings.frontend_dir)
    if _frontend:
        app.mount("/", StaticFiles(directory=_frontend, html=True), name="portal")
    else:
        logging.getLogger("civiclens").error(
            "SERVE_FRONTEND is on but no portal was found for FRONTEND_DIR=%s - "
            "the site root will return 404", settings.frontend_dir,
        )
