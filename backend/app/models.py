"""Case register tables.

Note what is absent: there is no reporter_name, reporter_phone, reporter_email,
reporter_ip, session_id or submitted_at column anywhere in this file. A
departmental account cannot be granted access to a column that does not exist,
and an SQL injection against this database cannot surface an identity it does
not hold.

`filed_on` is a Date, not a DateTime. Submission time to the second is an
identifier when combined with office CCTV or a queue token; the calendar day
is enough for the statutory clock.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import CaseBase


def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.now(UTC)


class Status(str, enum.Enum):
    pending = "pending"
    assigned = "assigned"
    investigating = "investigating"
    confirmed = "confirmed"
    closed = "closed"


OPEN_STATUSES = (Status.pending, Status.assigned, Status.investigating)


class Case(CaseBase):
    __tablename__ = "cases"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_no: Mapped[str] = mapped_column(String(24), unique=True, index=True)

    category: Mapped[str] = mapped_column(String(120))
    department: Mapped[str] = mapped_column(String(160), index=True)
    zone: Mapped[str] = mapped_column(String(8), index=True)

    # Office address - Part C of the form. Routes the file to a bench.
    state: Mapped[str] = mapped_column(String(80))
    district: Mapped[str] = mapped_column(String(80), index=True)
    city: Mapped[str] = mapped_column(String(80))
    pin: Mapped[str] = mapped_column(String(6), index=True)
    local_address: Mapped[str] = mapped_column(Text)

    amount_text: Mapped[str | None] = mapped_column(String(160), nullable=True)
    detail: Mapped[str] = mapped_column(Text)

    # Part B - the official complained against. Held for the investigator.
    # Never included in a public projection unless names_public is set, which
    # only a substantiated finding does.
    accused_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    accused_desig: Mapped[str | None] = mapped_column(String(160), nullable=True)
    accused_code: Mapped[str | None] = mapped_column(String(60), nullable=True)
    names_public: Mapped[bool] = mapped_column(Boolean, default=False)

    status: Mapped[Status] = mapped_column(String(16), default=Status.pending, index=True)
    investigator_code: Mapped[str | None] = mapped_column(String(24), nullable=True)
    penalty: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Dates only. See module docstring.
    filed_on: Mapped[date] = mapped_column(Date, index=True)
    assigned_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    decided_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    # True when the complainant chose encrypted follow-up. The row says that a
    # contact exists somewhere; it cannot say what it is or reach it.
    has_followup: Mapped[bool] = mapped_column(Boolean, default=False)
    overdue_flagged: Mapped[bool] = mapped_column(Boolean, default=False)

    events: Mapped[list[CaseEvent]] = relationship(back_populates="case", cascade="all, delete-orphan")
    evidence: Mapped[list[Evidence]] = relationship(back_populates="case", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint("length(pin) = 6", name="ck_pin_len"),
        Index("ix_cases_dept_status", "department", "status"),
    )


class CaseEvent(CaseBase):
    __tablename__ = "case_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(40))
    note: Mapped[str] = mapped_column(Text, default="")
    occurred_on: Mapped[date] = mapped_column(Date)
    public: Mapped[bool] = mapped_column(Boolean, default=True)

    case: Mapped[Case] = relationship(back_populates="events")


class Evidence(CaseBase):
    __tablename__ = "evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"), index=True)

    # The uploader's filename is discarded on ingest - "raju_bribe_receipt.jpg"
    # and "IMG_2291_myphone.jpg" both identify people. Only the extension and a
    # generated name survive.
    stored_name: Mapped[str] = mapped_column(String(64))
    media_type: Mapped[str] = mapped_column(String(60))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    metadata_stripped: Mapped[bool] = mapped_column(Boolean, default=True)
    received_on: Mapped[date] = mapped_column(Date)

    case: Mapped[Case] = relationship(back_populates="evidence")


class LedgerEntry(CaseBase):
    """Append-only hash chain. Rows are never updated or deleted."""

    __tablename__ = "ledger"

    seq: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    case_no: Mapped[str] = mapped_column(String(24), index=True)
    milestone: Mapped[str] = mapped_column(String(120))
    recorded_on: Mapped[date] = mapped_column(Date)
    payload_hash: Mapped[str] = mapped_column(String(64))
    prev_hash: Mapped[str] = mapped_column(String(64))
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True)


class Official(CaseBase):
    __tablename__ = "officials"

    employee_code: Mapped[str] = mapped_column(String(60), primary_key=True)
    department: Mapped[str] = mapped_column(String(160), index=True)
    rank: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(20), default="dept")  # dept | investigator
    password_hash: Mapped[str] = mapped_column(String(200))
    totp_secret: Mapped[str] = mapped_column(String(64))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(CaseBase):
    """Every officer read of a case file. Readable by the Ombudsman, not by
    the department it concerns."""

    __tablename__ = "audit_log"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, index=True)
    actor_code: Mapped[str] = mapped_column(String(60), index=True)
    actor_role: Mapped[str] = mapped_column(String(20))
    action: Mapped[str] = mapped_column(String(60))
    case_no: Mapped[str | None] = mapped_column(String(24), nullable=True, index=True)
    detail: Mapped[str] = mapped_column(Text, default="")


class RevokedToken(CaseBase):
    """Sign-out list for access tokens.

    Short token lifetimes make this small: rows are dropped once the token
    they revoke would have expired anyway.
    """

    __tablename__ = "revoked_tokens"

    jti: Mapped[str] = mapped_column(String(32), primary_key=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class UsedTotp(CaseBase):
    """One-time really meaning one time.

    Without this, a code observed over the shoulder stays valid for its whole
    30-second step, which makes the second factor a second password.
    """

    __tablename__ = "used_totp"

    employee_code: Mapped[str] = mapped_column(String(60), primary_key=True)
    window: Mapped[int] = mapped_column(Integer, primary_key=True)
    used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class CaseCounter(CaseBase):
    """Per-year sequence for case numbers, incremented under a row lock."""

    __tablename__ = "case_counter"

    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    last_no: Mapped[int] = mapped_column(Integer, default=0)


class IntakeThrottle(CaseBase):
    """Coarse abuse control that stores no identifier.

    A bucket key is HMAC(day, office-pin + category) - it limits how fast one
    office can be flooded, without recording who filed. There is no IP address
    and no device fingerprint here; rate limiting by identity would defeat the
    point of the platform.
    """

    __tablename__ = "intake_throttle"

    bucket: Mapped[str] = mapped_column(String(64), primary_key=True)
    day: Mapped[date] = mapped_column(Date, primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)

    __table_args__ = (UniqueConstraint("bucket", "day", name="uq_throttle"),)
