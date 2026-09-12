"""The service also serves the portal, which changes its attack surface.

A single-origin deployment is the simplest thing that works and avoids CORS
entirely - but it means the API's security headers now apply to a document,
and a misconfigured Content-Security-Policy silently blanks the page rather
than failing loudly. These tests pin that down.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.config import ConfigurationError, Settings
from app.db import create_all
from app.main import app


@pytest.fixture(scope="module")
def client():
    create_all()
    _seed_demo_account()
    return TestClient(app)


def _seed_demo_account() -> None:
    """One of the accounts DEMO_ACCOUNTS names, so the gate has something to
    serve. The suite runs on an empty database by design."""
    from app.db import CaseSession
    from app.models import Official
    from app.security import hash_password, new_totp_secret

    with CaseSession() as db:
        db.merge(Official(employee_code="MCD/REV/2019/0447", department="Municipal Revenue & Property Tax",
                          rank="Deputy Commissioner", role="dept",
                          password_hash=hash_password("Register-Counter-Seal-42!"),
                          totp_secret=new_totp_secret()))
        db.commit()


def test_the_portal_is_served_at_the_root(client):
    response = client.get("/")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "CivicLens" in response.text


def test_portal_assets_are_served(client):
    for path, fragment in [("/styles.css", "--paper"), ("/app.js", "CivicLens portal"), ("/api.js", "API client")]:
        response = client.get(path)
        assert response.status_code == 200, path
        assert fragment in response.text, path


def test_api_routes_win_over_static_files(client):
    """The static mount sits at '/', so this asserts ordering rather than luck."""
    assert client.get("/api/stats").status_code == 200
    assert client.get("/health/live").json()["status"] == "ok"


def test_csp_permits_the_pages_own_assets(client):
    """The bug this catches: `default-src 'none'` blocks the page's own
    stylesheet and scripts, and the browser renders a blank document with no
    server-side error at all."""
    csp = client.get("/").headers["content-security-policy"]
    assert "script-src 'self'" in csp
    assert "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com" in csp
    assert "font-src https://fonts.gstatic.com" in csp
    assert "connect-src 'self'" in csp
    # Still locked down where it matters.
    assert "default-src 'none'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "object-src" not in csp or "object-src 'none'" in csp


def test_api_responses_are_never_cached(client):
    assert client.get("/api/stats").headers["cache-control"] == "no-store"
    assert client.get("/").headers["cache-control"] == "no-cache"


def test_security_headers_are_present_on_the_document(client):
    headers = client.get("/").headers
    assert headers["x-content-type-options"] == "nosniff"
    assert headers["x-frame-options"] == "DENY"
    assert headers["referrer-policy"] == "no-referrer"
    assert "geolocation=()" in headers["permissions-policy"]


# --------------------------------------------------------------------------- demo gate
def test_demo_accounts_are_served_only_in_demo_mode(client, monkeypatch):
    from app.config import get_settings

    live = get_settings()

    monkeypatch.setattr("app.routers.demo.get_settings",
                        lambda: live.model_copy(update={"demo_mode": True}))
    body = client.get("/api/demo/accounts").json()
    assert body["accounts"], "demo mode should list the seeded accounts"
    for account in body["accounts"]:
        assert account["otp"].isdigit() and len(account["otp"]) == 6
        assert "totp_secret" not in account       # codes, never secrets
        assert "secret" not in str(account).lower()

    monkeypatch.setattr("app.routers.demo.get_settings",
                        lambda: live.model_copy(update={"demo_mode": False}))
    assert client.get("/api/demo/accounts").status_code == 404


def test_demo_mode_is_refused_in_production():
    with pytest.raises(ConfigurationError, match="never be on in production"):
        Settings(
            _env_file=None, env="production", profile="dept", demo_mode=True,
            jwt_secret="x" * 48, case_db_url="postgresql+psycopg://u@db/cases",
            storage_backend="s3", s3_bucket="b", cors_origins="https://x.example",
            trusted_hosts="x.example", metrics_token="t",
            intake_db_url=None, intake_hmac_key=None, intake_seal_key=None, intake_open_key=None,
        )


def test_demo_endpoint_serves_only_listed_accounts(client, monkeypatch):
    """A real employee code must never be handed a working one-time code."""
    from app.config import get_settings
    from app.db import CaseSession
    from app.models import Official
    from app.security import hash_password, new_totp_secret

    with CaseSession() as db:
        db.merge(Official(employee_code="REAL/OFFICER/0001", department="Municipal Revenue & Property Tax",
                          rank="Commissioner", role="dept",
                          password_hash=hash_password("Bench-Warrant-Seal-91!"),
                          totp_secret=new_totp_secret()))
        db.commit()

    live = get_settings()
    monkeypatch.setattr("app.routers.demo.get_settings",
                        lambda: live.model_copy(update={"demo_mode": True}))
    codes = client.get("/api/demo/accounts").json()["accounts"]
    assert all(account["employee_code"] != "REAL/OFFICER/0001" for account in codes)
