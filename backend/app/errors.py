"""RFC 9457 problem+json responses.

One error shape across the whole API, and error text a citizen can act on -
what went wrong and what to do next, never an apology and never a stack trace.
Unhandled exceptions return a request id instead of detail, so an operator can
find the log line without the response telling an attacker anything.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError

from .config import ConfigurationError
from .observability import log, request_id_var

logger = logging.getLogger("civiclens.errors")

TYPE_BASE = "https://civiclens.gov.example/problems"

TITLES = {
    400: "Bad request",
    401: "Sign in required",
    403: "Not permitted",
    404: "Not found",
    409: "Conflict",
    413: "Payload too large",
    415: "Unsupported file type",
    422: "That form could not be accepted",
    423: "Locked",
    429: "Too many requests",
    500: "Server fault",
    503: "Service unavailable",
}


def problem(
    status_code: int,
    detail: str,
    *,
    problem_type: str = "about:blank",
    headers: dict[str, str] | None = None,
    **extra: object,
) -> JSONResponse:
    body = {
        "type": f"{TYPE_BASE}/{problem_type}" if problem_type != "about:blank" else problem_type,
        "title": TITLES.get(status_code, "Error"),
        "status": status_code,
        "detail": detail,
        "request_id": request_id_var.get(),
        **extra,
    }
    return JSONResponse(status_code=status_code, content=body,
                        media_type="application/problem+json", headers=headers)


def install(app: FastAPI) -> None:
    @app.exception_handler(HTTPException)
    async def _http(_: Request, exc: HTTPException) -> JSONResponse:
        return problem(exc.status_code, str(exc.detail), headers=dict(exc.headers or {}))

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        fields = []
        for err in exc.errors():
            location = ".".join(str(p) for p in err["loc"][1:]) or "body"
            fields.append({"field": location, "problem": err["msg"]})
        return problem(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Some fields need attention before this can be accepted.",
            problem_type="validation",
            errors=fields,
        )

    @app.exception_handler(ConfigurationError)
    async def _config(_: Request, exc: ConfigurationError) -> JSONResponse:
        log(logger, logging.CRITICAL, "configuration fault", fault=str(exc))
        return problem(status.HTTP_503_SERVICE_UNAVAILABLE,
                       "This service is misconfigured and cannot serve requests.",
                       problem_type="configuration")

    @app.exception_handler(RuntimeError)
    async def _runtime(_: Request, exc: RuntimeError) -> JSONResponse:
        # Covers "no intake database in this process": a deployment fault, and
        # never a 200 with the contact silently missing.
        log(logger, logging.ERROR, "runtime fault", fault=str(exc))
        return problem(status.HTTP_503_SERVICE_UNAVAILABLE,
                       "This service cannot perform that action.", problem_type="unavailable")

    @app.exception_handler(SQLAlchemyError)
    async def _db(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        log(logger, logging.ERROR, "database error", error_type=type(exc).__name__)
        return problem(status.HTTP_503_SERVICE_UNAVAILABLE,
                       "The register is temporarily unavailable. Your report was not saved - "
                       "please try again in a few minutes.", problem_type="database")

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled exception", extra={"context": {"error_type": type(exc).__name__}})
        return problem(status.HTTP_500_INTERNAL_SERVER_ERROR,
                       "Something failed on our side. Quote the request id if you contact the helpline.",
                       problem_type="internal")
