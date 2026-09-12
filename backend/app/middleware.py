"""Edge behaviour: request ids, access logging without addresses, body caps,
security headers.

The access log here deliberately omits the client address. On the intake route
it also omits everything else that could narrow down a filing: no user agent,
no referrer, no query string, and a latency bucket rather than a precise
duration, so that timing cannot be correlated with an office CCTV clock.
"""

from __future__ import annotations

import logging
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from .config import get_settings
from .errors import problem
from .observability import LATENCY, REQUESTS, log, new_request_id, request_id_var

logger = logging.getLogger("civiclens.access")

# Routes where even ordinary telemetry is a risk to the person filing.
SENSITIVE_PATHS = ("/api/reports",)


def _route_template(request: Request) -> str:
    route = request.scope.get("route")
    return getattr(route, "path", request.url.path)


class RequestContextMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next) -> Response:
        request_id = new_request_id()
        request_id_var.set(request_id)
        started = time.perf_counter()

        response = await call_next(request)

        elapsed = time.perf_counter() - started
        route = _route_template(request)
        sensitive = any(request.url.path.startswith(p) for p in SENSITIVE_PATHS)

        REQUESTS.labels(request.method, route, str(response.status_code)).inc()
        LATENCY.labels(request.method, route).observe(elapsed)

        if sensitive:
            # Coarse bucket only. A precise duration is a fingerprint.
            log(logger, logging.INFO, "request", route=route, method=request.method,
                status=response.status_code, duration="fast" if elapsed < 1 else "slow")
        else:
            log(logger, logging.INFO, "request", route=route, method=request.method,
                status=response.status_code, duration_ms=round(elapsed * 1000, 1))

        response.headers["X-Request-ID"] = request_id
        return response


class BodyLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized uploads at the edge rather than buffering them."""

    async def dispatch(self, request: Request, call_next) -> Response:
        declared = request.headers.get("content-length")
        limit = get_settings().max_request_bytes
        if declared and declared.isdigit() and int(declared) > limit:
            return problem(413, f"Uploads must be {limit // (1024 * 1024)} MB or smaller.",
                           problem_type="payload-too-large")
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Belt to the reverse proxy's braces.

    The proxy MUST also be configured not to log client addresses on the
    intake route (see deploy/nginx.conf). This application cannot unwrite the
    proxy's log; it can only ensure it adds nothing of its own.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        settings = get_settings()
        self.is_production = settings.is_production
        # An API that serves no markup can lock everything down. The same
        # service serving the portal needs its own stylesheet, scripts and
        # Google Fonts - so the policy is built from what is actually mounted,
        # rather than being loosened everywhere to suit one case.
        if settings.serve_frontend:
            self.csp = "; ".join([
                "default-src 'none'",
                "script-src 'self'",
                # 'unsafe-inline' covers style attributes on elements. Scripts
                # are never inline: app.js and api.js are separate files.
                "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
                "font-src https://fonts.gstatic.com",
                "img-src 'self' data:",
                "connect-src 'self'",
                "form-action 'none'",
                "base-uri 'none'",
                "frame-ancestors 'none'",
            ])
        else:
            self.csp = "default-src 'none'; frame-ancestors 'none'"

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        if request.url.path.startswith(("/api", "/health", "/metrics")):
            response.headers["Cache-Control"] = "no-store"
        else:
            # Static portal assets: revalidate every time, but allow the
            # browser to reuse bytes it already has.
            response.headers.setdefault("Cache-Control", "no-cache")
        response.headers["Permissions-Policy"] = "geolocation=(), camera=(), microphone=(), interest-cohort=()"
        response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
        response.headers["Cross-Origin-Resource-Policy"] = "same-site"
        response.headers["Content-Security-Policy"] = self.csp
        if self.is_production:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains; preload"
        return response
