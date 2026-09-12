"""Departmental and investigator behaviour, and the public register surface."""

from __future__ import annotations

import io

import pyotp
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.db import CaseSession, create_all
from app.main import app
from app.models import Official
from app.security import hash_password, new_totp_secret

REVENUE = "Municipal Revenue & Property Tax"
WATER = "Water Supply & Sewerage"

BASE_REPORT = {
    "category": "Bribery - payment demanded for a routine service",
    "zone": "C",
    "office": {"state": "Delhi", "district": "Central Delhi", "city": "New Delhi",
               "pin": "110002", "local_address": "Zonal Revenue Office, Block C, Civic Centre"},
    "detail": "Payment was demanded at the counter to release a certificate already cleared.",
}


@pytest.fixture(scope="module")
def client():
    create_all()
    return TestClient(app)


def _sign_in(client: TestClient, code: str, department: str, role: str) -> dict[str, str]:
    password = "Bench-Warrant-Seal-91!"
    with CaseSession() as db:
        existing = db.get(Official, code)
        if existing is None:
            secret = new_totp_secret()
            db.add(Official(employee_code=code, department=department, rank="Officer", role=role,
                            password_hash=hash_password(password), totp_secret=secret))
            db.commit()
        else:
            secret = existing.totp_secret
    r = client.post("/api/auth/official/login",
                    json={"employee_code": code, "password": password, "otp": pyotp.TOTP(secret).now()})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture(scope="module")
def revenue_officer(client):
    return _sign_in(client, "MCD/REV/PORTAL", REVENUE, "dept")


@pytest.fixture(scope="module")
def water_officer(client):
    return _sign_in(client, "DJB/WSS/PORTAL", WATER, "dept")


@pytest.fixture(scope="module")
def ombudsman(client):
    return _sign_in(client, "OMB/INV/PORTAL", "Office of the Ombudsman", "investigator")


def _file(client: TestClient, department: str = REVENUE, **extra) -> str:
    body = {**BASE_REPORT, "department": department, **extra}
    r = client.post("/api/reports", json=body)
    assert r.status_code == 201, r.text
    return r.json()["case_no"]


# --------------------------------------------------------------------------- departmental scope
def test_officer_sees_only_their_own_department(client, revenue_officer, water_officer):
    revenue_case = _file(client, REVENUE)

    mine = client.get("/api/dept/cases", headers=revenue_officer)
    assert mine.status_code == 200
    assert all(c["department"] == REVENUE for c in mine.json())
    assert any(c["case_no"] == revenue_case for c in mine.json())

    # Another department's officer gets 404, not 403: confirming the case
    # exists would itself be information.
    assert client.get(f"/api/dept/cases/{revenue_case}", headers=water_officer).status_code == 404


def test_department_summary_counts_its_own_work(client, revenue_officer):
    _file(client, REVENUE)
    body = client.get("/api/dept/summary", headers=revenue_officer).json()
    assert body["department"] == REVENUE
    assert body["received"] >= 1
    assert set(body) >= {"active_investigations", "resolved", "awaiting_assignment", "overdue_assignments"}


def test_departmental_reply_is_published_with_the_case(client, revenue_officer):
    case_no = _file(client, REVENUE)
    reply = client.post(f"/api/dept/cases/{case_no}/reply", headers=revenue_officer,
                        json={"note": "The counter has been reassigned pending the inquiry."})
    assert reply.status_code == 200

    public = client.get(f"/api/cases/{case_no}").json()
    assert any(e["kind"] == "departmental_reply" for e in public["events"])


def test_every_case_file_opened_is_audited(client, revenue_officer):
    case_no = _file(client, REVENUE)
    client.get(f"/api/dept/cases/{case_no}", headers=revenue_officer)

    trail = client.get("/api/auth/audit", headers=revenue_officer).json()
    opened = [row for row in trail if row["action"] == "case_file_opened" and row["case_no"] == case_no]
    assert opened, "opening a case file must leave an audit row"
    assert opened[0]["actor"] == "MCD/REV/PORTAL"


def test_a_department_cannot_read_another_officers_audit_trail(client, revenue_officer, water_officer):
    client.get("/api/dept/summary", headers=water_officer)
    trail = client.get("/api/auth/audit", headers=revenue_officer).json()
    assert {row["actor"] for row in trail} == {"MCD/REV/PORTAL"}


def test_investigator_reads_the_whole_trail(client, ombudsman):
    trail = client.get("/api/auth/audit", headers=ombudsman).json()
    assert len({row["actor"] for row in trail}) >= 1


# --------------------------------------------------------------------------- assignment rules
def test_a_case_cannot_be_assigned_inside_the_department_complained_against(client, ombudsman):
    case_no = _file(client, WATER, office={**BASE_REPORT["office"], "pin": "110085"})
    with CaseSession() as db:
        db.merge(Official(employee_code="DJB/WSS/INSIDER", department=WATER, rank="Engineer",
                          role="investigator", password_hash=hash_password("Bench-Warrant-Seal-91!"),
                          totp_secret=new_totp_secret()))
        db.commit()

    refused = client.post(f"/api/investigator/cases/{case_no}/assign", headers=ombudsman,
                          json={"investigator_code": "DJB/WSS/INSIDER"})
    assert refused.status_code == 422
    assert "posted to the department complained against" in refused.json()["detail"]


def test_a_case_cannot_be_assigned_twice(client, ombudsman):
    case_no = _file(client)
    first = client.post(f"/api/investigator/cases/{case_no}/assign", headers=ombudsman,
                        json={"investigator_code": "INV-N-208"})
    assert first.status_code == 200
    second = client.post(f"/api/investigator/cases/{case_no}/assign", headers=ombudsman,
                         json={"investigator_code": "INV-C-114"})
    assert second.status_code == 409


def test_pending_queue_and_overdue_list_are_investigator_only(client, revenue_officer):
    assert client.get("/api/investigator/queue", headers=revenue_officer).status_code == 403


def test_closing_a_case_withholds_the_name_and_publishes_reasons(client, ombudsman):
    case_no = _file(client, accused={"name": "T. Nair", "designation": "Clerk", "employee_code": None})
    client.post(f"/api/investigator/cases/{case_no}/assign", headers=ombudsman,
                json={"investigator_code": "INV-S-077"})
    closed = client.post(f"/api/investigator/cases/{case_no}/status", headers=ombudsman,
                         json={"status": "closed", "note": "No corroboration; allegation not substantiated."})
    assert closed.status_code == 200

    public = client.get(f"/api/cases/{case_no}").json()
    assert public["status"] == "closed"
    assert public["accused_name"] is None  # cleared, not merely unproven
    assert any("not substantiated" in e["note"] for e in public["events"])


# --------------------------------------------------------------------------- sealed contact
def test_anonymous_case_has_no_contact_to_open(client, ombudsman):
    case_no = _file(client)
    body = client.get(f"/api/investigator/cases/{case_no}/contact", headers=ombudsman).json()
    assert body["contact"] is None
    assert "filed anonymously" in body["note"]


def test_sealed_contact_opens_for_an_investigator_and_is_audited(client, ombudsman):
    case_no = _file(client, followup_contact="9810012345")
    body = client.get(f"/api/investigator/cases/{case_no}/contact", headers=ombudsman).json()
    assert body["contact"] == "9810012345"

    trail = client.get("/api/auth/audit", headers=ombudsman).json()
    assert any(r["action"] == "contact_opened" and r["case_no"] == case_no for r in trail)


def test_a_departmental_officer_has_no_route_to_a_contact(client, revenue_officer):
    case_no = _file(client, followup_contact="9810012345")
    # The route exists only on the investigator surface.
    assert client.get(f"/api/investigator/cases/{case_no}/contact", headers=revenue_officer).status_code == 403
    # And the departmental projection carries no trace of it.
    served = client.get(f"/api/dept/cases/{case_no}", headers=revenue_officer).json()
    assert "9810012345" not in str(served)
    assert not any("contact" in key for key in served)


# --------------------------------------------------------------------------- evidence
def test_evidence_is_served_through_an_expiring_signed_url(client, revenue_officer):
    r = client.post("/api/reports", json={**BASE_REPORT, "department": REVENUE})
    case_no, upload_token = r.json()["case_no"], r.json()["upload_token"]

    buf = io.BytesIO()
    Image.new("RGB", (32, 32), (10, 20, 30)).save(buf, "PNG")
    up = client.post(f"/api/reports/{case_no}/evidence", params={"upload_token": upload_token},
                     files={"file": ("scan.png", buf.getvalue(), "image/png")})
    assert up.status_code == 201
    digest = up.json()["sha256"]

    manifest = client.get(f"/api/dept/evidence/{digest}", headers=revenue_officer)
    assert manifest.status_code == 200
    assert "expires=" in manifest.json()["download_url"]

    fetched = client.get(manifest.json()["download_url"])
    assert fetched.status_code == 200

    tampered = manifest.json()["download_url"].rsplit("signature=", 1)[0] + "signature=" + "0" * 32
    assert client.get(tampered).status_code == 403


def test_an_upload_token_is_scoped_to_its_own_case(client):
    first = client.post("/api/reports", json={**BASE_REPORT, "department": REVENUE}).json()
    other = _file(client)
    buf = io.BytesIO()
    Image.new("RGB", (8, 8)).save(buf, "PNG")
    wrong = client.post(f"/api/reports/{other}/evidence",
                        params={"upload_token": first["upload_token"]},
                        files={"file": ("x.png", buf.getvalue(), "image/png")})
    assert wrong.status_code == 403


# --------------------------------------------------------------------------- public register
def test_register_search_and_paging(client):
    _file(client)
    page = client.get("/api/register", params={"q": "Revenue", "per_page": 2}).json()
    assert page["per_page"] == 2
    assert len(page["rows"]) <= 2
    assert page["total"] >= 1
    assert all(REVENUE in row["department"] for row in page["rows"])


def test_stats_and_heatmap_describe_the_register(client):
    stats = client.get("/api/stats").json()
    assert stats["total"] >= 1
    assert 0 <= stats["acted_on_pct"] <= 100

    zones = client.get("/api/heatmap").json()
    assert {z["zone"] for z in zones} == {"N", "C", "S", "E"}
    assert all(z["band"] in {"low", "moderate", "high"} for z in zones)


def test_ledger_lists_milestones_without_leaking_content(client):
    entries = client.get("/api/ledger", params={"limit": 5}).json()
    assert entries
    for entry in entries:
        assert set(entry) == {"seq", "case_no", "milestone", "on", "entry_hash"}


def test_read_routes_are_rate_limited(client, monkeypatch):
    from app import ratelimit

    ratelimit.reset_for_tests()
    monkeypatch.setattr("app.routers.public.get_settings", lambda: _tiny_limit())
    responses = [client.get("/api/stats").status_code for _ in range(4)]
    assert 429 in responses
    ratelimit.reset_for_tests()


def _tiny_limit():
    from app.config import get_settings

    s = get_settings().model_copy(update={"rate_limit_read_per_minute": 2})
    return s
