"""Test isolation.

Runs before any app module is imported, so the suite never touches a
developer's .env, their database, or their evidence directory. Every run
starts on empty databases in a temporary directory - a test that only passes
because of a row left behind by the previous run is not a test.
"""

from __future__ import annotations

import os
import shutil
import tempfile

from app.security import generate_intake_keypair

_TMP = tempfile.mkdtemp(prefix="civiclens-tests-")
_SEAL_KEY, _OPEN_KEY = generate_intake_keypair()

# Environment variables win over the .env file in pydantic-settings, so this
# fully overrides local configuration.
os.environ.update(
    ENV="development",
    PROFILE="all",
    CASE_DB_URL=f"sqlite:///{_TMP}/case.db",
    INTAKE_DB_URL=f"sqlite:///{_TMP}/intake.db",
    INTAKE_HMAC_KEY="test-hmac-key",
    INTAKE_SEAL_KEY=_SEAL_KEY,
    INTAKE_OPEN_KEY=_OPEN_KEY,
    JWT_SECRET="test-secret-not-used-outside-the-suite",
    EVIDENCE_DIR=f"{_TMP}/evidence",
    STORAGE_BACKEND="local",
    METRICS_ENABLED="false",
    REDIS_URL="",
)


def pytest_sessionfinish(session, exitstatus) -> None:
    shutil.rmtree(_TMP, ignore_errors=True)
