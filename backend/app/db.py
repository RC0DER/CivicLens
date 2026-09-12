"""Two engines that never meet.

There is no SQLAlchemy relationship, foreign key, view or federated query
joining the case register to the intake store. They are different databases,
reached by different credentials, and in production they sit on different
hosts with different backup policies - a restore must never put both into one
environment.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import Engine, create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from .config import get_settings

logger = logging.getLogger("civiclens.db")


class CaseBase(DeclarativeBase):
    """Tables in the public case register."""


class IntakeBase(DeclarativeBase):
    """Tables in the intake store. Never imported by the dept service."""


def _engine(url: str, *, label: str) -> Engine:
    s = get_settings()
    kwargs: dict = {"pool_pre_ping": True, "future": True}

    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 15}
        path = url.split("///", 1)[-1]
        if path not in (":memory:", ""):
            os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    else:
        kwargs.update(
            pool_size=s.db_pool_size,
            max_overflow=s.db_max_overflow,
            pool_recycle=s.db_pool_recycle_seconds,
            pool_timeout=10,
            connect_args={
                # A runaway query must not hold a connection open indefinitely.
                "options": f"-c statement_timeout={s.db_statement_timeout_ms}",
                "application_name": f"{s.service_name}:{s.profile}:{label}",
            },
        )

    engine = create_engine(url, **kwargs)

    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _sqlite_pragmas(dbapi_connection, _):  # pragma: no cover - dev only
            cur = dbapi_connection.cursor()
            cur.execute("PRAGMA journal_mode=WAL")     # concurrent readers during a write
            cur.execute("PRAGMA foreign_keys=ON")      # ON DELETE CASCADE actually cascades
            cur.execute("PRAGMA busy_timeout=15000")
            cur.close()

    return engine


_settings = get_settings()

case_engine: Engine = _engine(_settings.case_db_url, label="case")
CaseSession = sessionmaker(bind=case_engine, autoflush=False, expire_on_commit=False)

intake_engine: Engine | None = None
IntakeSession: sessionmaker | None = None
if _settings.intake_db_url:
    intake_engine = _engine(_settings.intake_db_url, label="intake")
    IntakeSession = sessionmaker(bind=intake_engine, autoflush=False, expire_on_commit=False)


def get_case_db() -> Iterator[Session]:
    db = CaseSession()
    try:
        yield db
        # A handler that raised has already been rolled back by the exception
        # path below; this only guards handlers that forgot to commit.
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_intake_db() -> Iterator[Session]:
    if IntakeSession is None:
        # Reached only if a route is wired into a profile that has no business
        # holding contact data. Failing closed is the point.
        raise RuntimeError(
            "This service is not configured with an intake database. "
            "Contact details are unreachable from this process by design."
        )
    db = IntakeSession()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


@contextmanager
def intake_session() -> Iterator[Session]:
    """Short-lived intake session for the two call sites that may touch it.

    A context manager rather than a hand-driven generator, so the session is
    closed on every path - including the one where sealing raises.
    """
    if IntakeSession is None:
        raise RuntimeError(
            "This service is not configured with an intake database. "
            "Contact details are unreachable from this process by design."
        )
    db = IntakeSession()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def ping(engine: Engine) -> bool:
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        logger.error("database ping failed", extra={"context": {"error_type": type(exc).__name__}})
        return False


def readiness() -> dict:
    """What /health/ready reports. Intake is only expected where the profile
    is supposed to have it."""
    checks = {"case_db": ping(case_engine)}
    if intake_engine is not None:
        checks["intake_db"] = ping(intake_engine)
    return checks


def create_all() -> None:
    """Development convenience. Production runs Alembic - see migrations/."""
    from . import models  # noqa: F401  (registers case tables)

    CaseBase.metadata.create_all(case_engine)
    if intake_engine is not None:
        from . import intake_models  # noqa: F401

        IntakeBase.metadata.create_all(intake_engine)
