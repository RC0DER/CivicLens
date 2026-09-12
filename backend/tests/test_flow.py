"""End-to-end: file, track, assign, find, publish - and the ledger holds."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app.db import CaseSession, create_all
from app.main import app
from app.models import Case, Official, Status
from app.security import hash_password, new_totp_secret

REPORT = {
    "category": "Bribery - payment demanded for a routine service",
    "department": "Municipal Revenue & Property Tax",
    "zone": "C",
    "office": {
        "state": "Delhi", "district": "Central Delhi", "city": "New Delhi",
        "pin": "110002", "local_address": "Zonal Revenue Office, Block C, Civic Centre",
    },
    "accused": {"name": "S. Bhatia", "designation": "Head Clerk", "employee_code": "MCD/REV/2014/0663"},
    "amount_text": "Rs 4,500 in cash",
    "detail": "Payment was demanded at the counter to release a mutation certificate already cleared.",
}


@pytest.fixture(scope="module")
def client():
    create_all()
    return TestClient(app)


@pytest.fixture(scope="module")
def investigator(client):
    """Sign in once and reuse the session.

    Signing in twice in quick succession is refused by design - the same TOTP
    code cannot be replayed inside its 30-second step. See
    test_totp_code_cannot_be_replayed.
    """
    import pyotp

    secret = new_totp_secret()
    with CaseSession() as db:
        existing = db.get(Official, "OMB/INV/TEST")
        if existing is None:
            db.add(Official(employee_code="OMB/INV/TEST", department="Office of the Ombudsman",
                            rank="Investigator", role="investigator",
                            password_hash=hash_password("Str0ng!Passphrase"), totp_secret=secret))
            db.commit()
        else:
            secret = existing.totp_secret

    response = client.post("/api/auth/official/login", json={
        "employee_code": "OMB/INV/TEST", "password": "Str0ng!Passphrase", "otp": pyotp.TOTP(secret).now(),
    })
    assert response.status_code == 200, response.text
    return {"headers": {"Authorization": f"Bearer {response.json()['access_token']}"}, "secret": secret}


def test_report_returns_a_case_number_and_a_clock(client):
    r = client.post("/api/reports", json=REPORT)
    assert r.status_code == 201
    body = r.json()
    assert body["case_no"].startswith("CRTP-")
    assert body["status"] == "pending"
    assert date.fromisoformat(body["assignment_due_on"]) == date.today() + timedelta(days=14)


def test_pin_validation_rejects_a_bad_code(client):
    bad = {**REPORT, "office": {**REPORT["office"], "pin": "01100"}}
    assert client.post("/api/reports", json=bad).status_code == 422


def test_tracking_is_open_to_anyone_and_hides_the_name(client):
    case_no = client.post("/api/reports", json=REPORT).json()["case_no"]
    r = client.get(f"/api/cases/{case_no}")  # no Authorization header
    assert r.status_code == 200
    body = r.json()
    assert body["accused_designation"] == "Head Clerk"
    # Named but unproven: withheld from the public projection until a finding.
    assert body["accused_name"] is None
    assert "contact" not in body and "reporter" not in str(body).lower()


def test_unknown_case_number_is_a_plain_404(client):
    assert client.get("/api/cases/CRTP-2026-999999").status_code == 404


def test_department_portal_requires_sign_in(client):
    assert client.get("/api/dept/cases").status_code == 401


def test_full_lifecycle_publishes_each_step(client, investigator):
    auth = investigator["headers"]

    case_no = client.post("/api/reports", json=REPORT).json()["case_no"]

    assigned = client.post(f"/api/investigator/cases/{case_no}/assign",
                           json={"investigator_code": "INV-N-208"}, headers=auth)
    assert assigned.status_code == 200
    assert assigned.json()["investigator_code"] == "INV-N-208"

    done = client.post(f"/api/investigator/cases/{case_no}/status", headers=auth, json={
        "status": "confirmed",
        "note": "Counterfoils recovered; demand corroborated by two further complainants.",
        "penalty": "Head Clerk dismissed from service; recovery ordered.",
        "publish_accused_name": True,
    })
    assert done.status_code == 200

    public = client.get(f"/api/cases/{case_no}").json()
    assert public["status"] == "confirmed"
    assert public["accused_name"] == "S. Bhatia"  # now substantiated, so publishable
    assert public["penalty"].startswith("Head Clerk dismissed")

    assert client.get("/api/ledger/verify").json()["intact"] is True


def test_confirmed_finding_must_record_a_penalty(client, investigator):
    case_no = client.post("/api/reports", json=REPORT).json()["case_no"]
    r = client.post(f"/api/investigator/cases/{case_no}/status",
                    headers=investigator["headers"],
                    json={"status": "confirmed", "note": "substantiated"})
    assert r.status_code == 422


def test_totp_code_cannot_be_replayed(client, investigator):
    """A code seen over a shoulder is useless: it is burned on first use."""
    import pyotp

    code = pyotp.TOTP(investigator["secret"]).now()
    again = client.post("/api/auth/official/login", json={
        "employee_code": "OMB/INV/TEST", "password": "Str0ng!Passphrase", "otp": code,
    })
    assert again.status_code == 401
    assert "already been used" in again.json()["detail"]


def test_signing_out_revokes_the_token_immediately(client, investigator):
    import pyotp

    with CaseSession() as db:
        db.merge(Official(employee_code="MCD/REV/LOGOUT", department="Municipal Revenue & Property Tax",
                          rank="Deputy Commissioner", role="dept",
                          password_hash=hash_password("An0ther!Passphrase"),
                          totp_secret=new_totp_secret()))
        db.commit()
        secret = db.get(Official, "MCD/REV/LOGOUT").totp_secret

    token = client.post("/api/auth/official/login", json={
        "employee_code": "MCD/REV/LOGOUT", "password": "An0ther!Passphrase",
        "otp": pyotp.TOTP(secret).now(),
    }).json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    assert client.get("/api/auth/me", headers=auth).status_code == 200
    assert client.post("/api/auth/official/logout", headers=auth).status_code == 200
    # The token has not expired - it has been revoked.
    assert client.get("/api/auth/me", headers=auth).status_code == 401


def test_departmental_errors_use_problem_json(client):
    r = client.get("/api/dept/cases")
    assert r.status_code == 401
    assert r.headers["content-type"].startswith("application/problem+json")
    body = r.json()
    assert body["status"] == 401 and body["title"] and body["request_id"]


def test_validation_errors_name_the_field(client):
    bad = {**REPORT, "office": {**REPORT["office"], "pin": "01100"}}
    body = client.post("/api/reports", json=bad).json()
    assert body["type"].endswith("/validation")
    assert any(e["field"].endswith("pin") for e in body["errors"])


def test_health_endpoints(client):
    assert client.get("/health/live").json()["status"] == "ok"
    ready = client.get("/health/ready").json()
    assert ready["checks"]["case_db"] is True
    assert ready["publishes_names_before_finding"] is False


def test_overdue_sweep_publishes_the_breach():
    from app.jobs import assignment_sweep

    with CaseSession() as db:
        db.query(Case).filter(Case.case_no == "CRTP-1999-000001").delete()
        stale = Case(
            case_no="CRTP-1999-000001", category="Bribery", department="Water Supply & Sewerage",
            zone="E", state="Delhi", district="North West Delhi", city="New Delhi", pin="110085",
            local_address="Jal Bhawan", detail="x" * 25, status=Status.pending,
            filed_on=date.today() - timedelta(days=40),
        )
        db.add(stale)
        db.commit()

    assert assignment_sweep() >= 1

    with CaseSession() as db:
        refreshed = db.query(Case).filter(Case.case_no == "CRTP-1999-000001").one()
        assert refreshed.overdue_flagged is True
        assert any(e.kind == "assignment_overdue" for e in refreshed.events)
