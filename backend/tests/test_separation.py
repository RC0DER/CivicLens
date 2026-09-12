"""The tests that matter.

If the platform's promise is false, it is false here. Each test targets one
sentence the interface says to citizens.
"""

from __future__ import annotations

import inspect
from datetime import date

import pytest
from sqlalchemy import inspect as sa_inspect

from app.db import CaseBase, case_engine
from app.models import Case
from app.schemas import DeptCase, InvestigatorCase, PublicCase

FORBIDDEN = ("reporter", "complainant", "contact", "phone", "mobile", "email", "ip_address",
             "device", "fingerprint", "session", "user_agent", "submitted_at")


def test_case_register_holds_no_identifier():
    """"The case file has no field to hold them." """
    for table in CaseBase.metadata.sorted_tables:
        if table.name == "intake_throttle":
            continue  # bucket is HMAC(office), not a person
        for column in table.columns:
            assert not any(word in column.name.lower() for word in FORBIDDEN), \
                f"{table.name}.{column.name} could hold an identifier"


def test_filing_time_is_a_date_not_a_timestamp():
    """"No timestamp finer than the calendar day is attached to it." """
    assert Case.__table__.c.filed_on.type.python_type is date


def test_dept_projection_has_no_reporter_field():
    """"Reporter identities are absent from the record served to departmental
    accounts, at any permission level." """
    for model in (PublicCase, DeptCase, InvestigatorCase):
        for field in model.model_fields:
            assert not any(word in field.lower() for word in FORBIDDEN), \
                f"{model.__name__}.{field} leaks the reporter"
        # extra="forbid" means a future dict spread cannot smuggle one in
        assert model.model_config.get("extra") == "forbid"


def test_dept_router_cannot_reach_the_intake_store():
    """Structural, not procedural: the departmental module has no import path
    to contact data at all.

    Parsed as code rather than grepped as text, so that the module is free to
    *describe* the guarantee in its docstring without tripping the test.
    """
    import ast

    from app.routers import dept

    tree = ast.parse(inspect.getsource(dept))
    referenced: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            referenced.update(a.name.split(".")[-1] for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            referenced.update(a.name for a in node.names)
            if node.module:
                referenced.update(node.module.split("."))
        elif isinstance(node, ast.Name):
            referenced.add(node.id)
        elif isinstance(node, ast.Attribute):
            referenced.add(node.attr)

    for forbidden in ("intake_models", "intake_token", "open_contact", "seal_contact",
                      "get_intake_db", "IntakeContact", "IntakeSession", "intake_engine"):
        assert forbidden not in referenced, f"dept router reaches for {forbidden}"


def test_dept_service_runs_without_intake_keys(monkeypatch):
    """A dept deployment has no keys, so the bridge cannot be computed even by
    code that tried."""
    from app import security
    from app.config import Settings

    dept_settings = Settings(profile="dept", intake_db_url=None,
                             intake_hmac_key=None, intake_enc_key=None)
    monkeypatch.setattr(security, "get_settings", lambda: dept_settings)

    assert dept_settings.holds_intake_keys is False
    with pytest.raises(security.IntakeKeysUnavailable):
        security.intake_token("CRTP-2026-004417")
    with pytest.raises(security.IntakeKeysUnavailable):
        security.seal_contact("9810000000")


def test_intake_token_is_one_way():
    """Possession of the intake store reveals no case numbers."""
    from app import security
    from app.config import Settings

    s = Settings(intake_hmac_key="unit-test-key", intake_enc_key=security.generate_fernet_key())
    original = security.get_settings
    security.get_settings = lambda: s  # type: ignore[assignment]
    try:
        token = security.intake_token("CRTP-2026-004417")
        assert "CRTP" not in token and len(token) == 64
        assert token != security.intake_token("CRTP-2026-004418")
        assert token == security.intake_token("CRTP-2026-004417")  # deterministic
    finally:
        security.get_settings = original  # type: ignore[assignment]


def test_intake_table_cannot_be_joined_to_cases():
    """No foreign key, no shared column - the two stores cannot be joined even
    by someone holding both dumps."""
    from app import intake_models

    intake_cols = {c.name for c in intake_models.IntakeContact.__table__.columns}
    case_cols = {c.name for c in Case.__table__.columns}
    assert "case_no" not in intake_cols
    assert intake_cols & case_cols == set()
    assert intake_models.IntakeContact.__table__.foreign_keys == set()


def test_case_register_engine_holds_no_contact_table():
    """Even in the single-process dev profile, contacts are in the other
    database file."""
    names = sa_inspect(case_engine).get_table_names()
    assert "intake_contacts" not in names
