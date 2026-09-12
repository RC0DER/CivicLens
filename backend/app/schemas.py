"""Request and response shapes.

Three projections of one case, and they are separate classes on purpose. A
response model with `extra="forbid"` and no reporter fields cannot leak one by
accident - if a future developer adds a column to Case, it does not appear in
any of these until someone writes it in deliberately.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

STRICT = ConfigDict(extra="forbid")


# --------------------------------------------------------------------------- intake
class OfficeAddress(BaseModel):
    model_config = STRICT
    state: str = Field(min_length=2, max_length=80)
    district: str = Field(min_length=2, max_length=80)
    city: str = Field(min_length=1, max_length=80)
    pin: str
    local_address: str = Field(min_length=4, max_length=600)

    @field_validator("pin")
    @classmethod
    def _pin(cls, v: str) -> str:
        v = v.strip()
        if len(v) != 6 or not v.isdigit() or v[0] == "0":
            raise ValueError("A PIN code is six digits and does not start with 0.")
        return v


class AccusedOfficial(BaseModel):
    model_config = STRICT
    name: str | None = Field(default=None, max_length=120)
    designation: str | None = Field(default=None, max_length=160)
    employee_code: str | None = Field(default=None, max_length=60)


class ReportIn(BaseModel):
    model_config = STRICT
    category: str = Field(min_length=3, max_length=120)
    department: str = Field(min_length=3, max_length=160)
    zone: str = Field(min_length=1, max_length=8)
    office: OfficeAddress
    accused: AccusedOfficial | None = None
    amount_text: str | None = Field(default=None, max_length=160)
    detail: str = Field(min_length=20, max_length=8000)

    # If present, this is the ONLY field in the whole API that carries a means
    # of contacting the complainant. It is sealed into the intake store and
    # never written to the case register.
    followup_contact: str | None = Field(default=None, max_length=120)


class ReportAccepted(BaseModel):
    model_config = STRICT
    case_no: str
    filed_on: date
    status: str
    upload_token: str
    assignment_due_on: date
    message: str


# --------------------------------------------------------------------------- projections
class EventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    kind: str
    note: str
    occurred_on: date


class EvidenceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True, extra="forbid")
    sha256: str
    media_type: str
    size_bytes: int
    metadata_stripped: bool
    received_on: date


class PublicCase(BaseModel):
    """What anyone in the world sees. No reporter fields exist here, and no
    accused name unless a finding substantiated the allegation."""

    model_config = STRICT
    case_no: str
    category: str
    department: str
    zone: str
    office: OfficeAddress
    amount_text: str | None
    detail: str
    accused_designation: str | None
    accused_name: str | None  # None until names_public is set by a finding
    status: str
    investigator_code: str | None
    filed_on: date
    assigned_on: date | None
    decided_on: date | None
    assignment_due_on: date
    overdue: bool
    penalty: str | None
    evidence_count: int
    events: list[EventOut]


class DeptCase(BaseModel):
    """Served to a departmental officer. Identical to the public projection
    plus the accused's details, which the department needs to answer the
    allegation - and nothing else. There is no field here for a reporter."""

    model_config = STRICT
    case_no: str
    category: str
    department: str
    office: OfficeAddress
    amount_text: str | None
    detail: str
    accused_name: str | None
    accused_designation: str | None
    accused_employee_code: str | None
    status: str
    investigator_code: str | None
    filed_on: date
    assignment_due_on: date
    reply_due_on: date
    evidence: list[EvidenceOut]
    events: list[EventOut]


class InvestigatorCase(DeptCase):
    """Adds only a flag - whether a contact exists. Reading the contact itself
    is a separate, separately audited call."""

    model_config = STRICT
    has_followup: bool


class ContactOut(BaseModel):
    model_config = STRICT
    case_no: str
    contact: str | None
    note: str


# --------------------------------------------------------------------------- register / stats
class RegisterRow(BaseModel):
    model_config = STRICT
    case_no: str
    category: str
    department: str
    zone: str
    district: str
    status: str
    investigator_code: str | None
    filed_on: date
    overdue: bool
    penalty: str | None


class Page(BaseModel):
    model_config = STRICT
    total: int
    page: int
    per_page: int
    rows: list[RegisterRow]


class Stats(BaseModel):
    model_config = STRICT
    total: int
    by_status: dict[str, int]
    acted_on_pct: int
    median_days_to_assign: float | None
    overdue: int


class ZoneDensity(BaseModel):
    model_config = STRICT
    zone: str
    reports: int
    confirmed: int
    per_10k: float
    band: Literal["low", "moderate", "high"]


# --------------------------------------------------------------------------- auth / workflow
class LoginIn(BaseModel):
    model_config = STRICT
    employee_code: str
    password: str
    otp: str


class TokenOut(BaseModel):
    model_config = STRICT
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - a scheme name, not a secret
    role: str
    department: str
    expires_in_minutes: int


class AssignIn(BaseModel):
    model_config = STRICT
    investigator_code: str = Field(min_length=3, max_length=24)


class StatusIn(BaseModel):
    model_config = STRICT
    status: Literal["investigating", "confirmed", "closed"]
    note: str = Field(min_length=3, max_length=2000)
    penalty: str | None = Field(default=None, max_length=2000)
    publish_accused_name: bool = False


class ReplyIn(BaseModel):
    model_config = STRICT
    note: str = Field(min_length=10, max_length=4000)
