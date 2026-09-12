"""Structured logging and metrics that cannot become a surveillance record.

An access log is the classic way a whistleblower platform betrays its users:
the application is careful, and then nginx writes $remote_addr next to
POST /api/reports and the correlation is trivial. So:

  * the log formatter redacts known-sensitive keys and never accepts a client
    address as a field at all;
  * case numbers are logged only for authenticated staff actions, where the
    audit trail is the point - never on the public intake path;
  * metric labels carry the route template, never a case number or any other
    high-cardinality value that could identify a filing.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any

from .config import get_settings

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")

SENSITIVE_KEYS = {
    "contact", "followup_contact", "phone", "mobile", "email", "password", "otp",
    "token", "access_token", "upload_token", "authorization", "ciphertext",
    "client_ip", "remote_addr", "x_forwarded_for", "user_agent",
}

# Defence in depth: even if a developer interpolates a value into a message,
# these patterns are masked before the line is written.
_PATTERNS = [
    (re.compile(r"\b[6-9]\d{9}\b"), "[redacted-phone]"),
    (re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[redacted-email]"),
    (re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}\b"), "[redacted-ip]"),
]


def scrub(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: ("[redacted]" if k.lower() in SENSITIVE_KEYS else scrub(v)) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [scrub(v) for v in value]
    if isinstance(value, str):
        for pattern, replacement in _PATTERNS:
            value = pattern.sub(replacement, value)
        return value
    return value


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        s = get_settings()
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "service": s.service_name,
            "profile": s.profile,
            "env": s.env,
            "request_id": request_id_var.get(),
            "message": scrub(record.getMessage()),
        }
        extra = getattr(record, "context", None)
        if isinstance(extra, dict):
            payload.update(scrub(extra))
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level)

    # uvicorn's own access log records the client address. We serve the access
    # log ourselves, without it.
    logging.getLogger("uvicorn.access").handlers = []
    logging.getLogger("uvicorn.access").propagate = False
    logging.getLogger("uvicorn.error").handlers = [handler]


def log(logger: logging.Logger, level: int, message: str, **context: Any) -> None:
    logger.log(level, message, extra={"context": context})


def new_request_id() -> str:
    return uuid.uuid4().hex[:16]


# --------------------------------------------------------------------------- metrics
try:  # prometheus_client is optional; absence must not take the service down
    from prometheus_client import CONTENT_TYPE_LATEST, Counter, Histogram, generate_latest

    METRICS_AVAILABLE = True

    REQUESTS = Counter("civiclens_requests_total", "HTTP requests", ["method", "route", "status"])
    LATENCY = Histogram("civiclens_request_seconds", "Request latency", ["method", "route"],
                        buckets=(0.01, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10))
    REPORTS_FILED = Counter("civiclens_reports_filed_total", "Reports accepted")
    EVIDENCE_REJECTED = Counter("civiclens_evidence_rejected_total", "Evidence rejected", ["reason"])
    LOGIN_FAILURES = Counter("civiclens_login_failures_total", "Failed official sign-ins")
    LOCKOUTS = Counter("civiclens_account_lockouts_total", "Employee codes locked")
    CONTACTS_OPENED = Counter("civiclens_contacts_opened_total", "Sealed contacts opened by investigators")
    RATE_LIMITED = Counter("civiclens_rate_limited_total", "Requests refused by rate limit", ["route"])
    OVERDUE_FLAGGED = Counter("civiclens_assignment_overdue_total", "Assignment limit breaches published")
except ImportError:  # pragma: no cover
    METRICS_AVAILABLE = False
    CONTENT_TYPE_LATEST = "text/plain"

    class _Noop:
        def labels(self, *_: Any, **__: Any) -> _Noop:
            return self

        def inc(self, *_: Any, **__: Any) -> None:
            return None

        def observe(self, *_: Any, **__: Any) -> None:
            return None

    REQUESTS = LATENCY = REPORTS_FILED = EVIDENCE_REJECTED = _Noop()  # type: ignore[assignment]
    LOGIN_FAILURES = LOCKOUTS = CONTACTS_OPENED = RATE_LIMITED = OVERDUE_FLAGGED = _Noop()  # type: ignore[assignment]

    def generate_latest() -> bytes:  # type: ignore[misc]
        return b"# prometheus_client is not installed\n"
