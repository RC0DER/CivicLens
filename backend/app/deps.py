"""Dependencies: auth, audit, projections, and the statutory clock."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import jwt
from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from .clock import today as clock_today
from .config import get_settings
from .db import CaseSession, get_case_db
from .models import AuditLog, Case, RevokedToken, Status
from .schemas import DeptCase, EventOut, EvidenceOut, InvestigatorCase, OfficeAddress, PublicCase
from .security import read_token


@dataclass
class Actor:
    code: str
    role: str
    department: str | None
    jti: str = ""
    expires_at: datetime | None = None


def _bearer(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Sign in to continue.")
    return authorization.split(" ", 1)[1]


def current_officer(authorization: str | None = Header(default=None)) -> Actor:
    try:
        claims = read_token(_bearer(authorization))
    except jwt.ExpiredSignatureError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Your session expired. Sign in again.") from None
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That session token is not valid.") from None
    if claims.get("role") not in ("dept", "investigator"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "This area is for government personnel.")

    # Signed out tokens stop working immediately rather than at expiry.
    jti = claims.get("jti", "")
    with CaseSession() as db:
        if jti and db.get(RevokedToken, jti) is not None:
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "That session was signed out. Sign in again.")

    return Actor(
        code=claims["sub"],
        role=claims["role"],
        department=claims.get("dept"),
        jti=jti,
        expires_at=datetime.fromtimestamp(claims["exp"], tz=UTC),
    )


def require_investigator(actor: Actor = Depends(current_officer)) -> Actor:
    if actor.role != "investigator":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "Only Ombudsman investigators may do this.")
    return actor


def audit(db: Session, actor: Actor, action: str, case_no: str | None = None, detail: str = "") -> None:
    db.add(AuditLog(actor_code=actor.code, actor_role=actor.role, action=action,
                    case_no=case_no, detail=detail))


def assignment_due(c: Case) -> date:
    return c.filed_on + timedelta(days=get_settings().assignment_limit_days)


def is_overdue(c: Case, today: date | None = None) -> bool:
    if c.investigator_code:
        return False
    return (today or clock_today()) > assignment_due(c)


def _office(c: Case) -> OfficeAddress:
    return OfficeAddress(state=c.state, district=c.district, city=c.city,
                         pin=c.pin, local_address=c.local_address)


def to_public(c: Case) -> PublicCase:
    s = get_settings()
    show_name = c.names_public or s.publish_names_before_finding
    return PublicCase(
        case_no=c.case_no,
        category=c.category,
        department=c.department,
        zone=c.zone,
        office=_office(c),
        amount_text=c.amount_text,
        detail=c.detail,
        accused_designation=c.accused_desig,
        accused_name=c.accused_name if show_name else None,
        status=c.status.value if isinstance(c.status, Status) else str(c.status),
        investigator_code=c.investigator_code,
        filed_on=c.filed_on,
        assigned_on=c.assigned_on,
        decided_on=c.decided_on,
        assignment_due_on=assignment_due(c),
        overdue=is_overdue(c),
        penalty=c.penalty,
        evidence_count=len(c.evidence),
        events=[EventOut.model_validate(e) for e in sorted(c.events, key=lambda e: e.occurred_on) if e.public],
    )


def to_dept(c: Case) -> DeptCase:
    return DeptCase(
        case_no=c.case_no,
        category=c.category,
        department=c.department,
        office=_office(c),
        amount_text=c.amount_text,
        detail=c.detail,
        accused_name=c.accused_name,
        accused_designation=c.accused_desig,
        accused_employee_code=c.accused_code,
        status=c.status.value if isinstance(c.status, Status) else str(c.status),
        investigator_code=c.investigator_code,
        filed_on=c.filed_on,
        assignment_due_on=assignment_due(c),
        reply_due_on=c.filed_on + timedelta(days=7),
        evidence=[EvidenceOut.model_validate(e) for e in c.evidence],
        events=[EventOut.model_validate(e) for e in sorted(c.events, key=lambda e: e.occurred_on)],
    )


def to_investigator(c: Case) -> InvestigatorCase:
    return InvestigatorCase(**to_dept(c).model_dump(), has_followup=c.has_followup)


def get_case_or_404(db: Session, case_no: str) -> Case:
    from sqlalchemy import select

    c = db.execute(select(Case).where(Case.case_no == case_no.strip().upper())).scalar_one_or_none()
    if not c:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "No case carries that number. Case numbers read CRTP-YYYY- followed by six digits.",
        )
    return c


CaseDB = Depends(get_case_db)
