"""Ombudsman investigator workbench.

Assignment enforces the department-external rule in code: an investigator
posted to the department complained against cannot take the case, whatever the
roster says.

This is the only module that can read a contact, and only in a process holding
both intake keys. That read is audited on its own line.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import ledger
from ..clock import today as clock_today
from ..config import get_settings
from ..db import get_case_db, intake_session
from ..deps import Actor, audit, get_case_or_404, is_overdue, require_investigator, to_investigator
from ..models import Case, CaseEvent, Official, Status
from ..schemas import AssignIn, ContactOut, InvestigatorCase, StatusIn
from ..security import IntakeKeysUnavailable, intake_token, open_contact

router = APIRouter(prefix="/investigator", tags=["investigator"])


@router.get("/queue", response_model=list[InvestigatorCase])
def queue(actor: Actor = Depends(require_investigator), db: Session = Depends(get_case_db)) -> list[InvestigatorCase]:
    rows = db.execute(
        select(Case).where(Case.status == Status.pending).order_by(Case.filed_on)
    ).scalars().all()
    audit(db, actor, "queue_read", detail=f"{len(rows)} pending")
    db.commit()
    return [to_investigator(c) for c in rows]


@router.get("/overdue", response_model=list[InvestigatorCase])
def overdue(actor: Actor = Depends(require_investigator), db: Session = Depends(get_case_db)) -> list[InvestigatorCase]:
    rows = [c for c in db.execute(select(Case)).scalars() if is_overdue(c)]
    return [to_investigator(c) for c in rows]


@router.post("/cases/{case_no}/assign", response_model=InvestigatorCase)
def assign(case_no: str, body: AssignIn, actor: Actor = Depends(require_investigator),
           db: Session = Depends(get_case_db)) -> InvestigatorCase:
    case = get_case_or_404(db, case_no)
    if case.investigator_code:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"{case.case_no} is already with {case.investigator_code}.")

    posted = db.execute(
        select(Official).where(Official.employee_code == body.investigator_code)
    ).scalar_one_or_none()
    if posted and posted.department == case.department:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "That investigator is posted to the department complained against. "
            "Cases route outside the office they concern.",
        )

    today = clock_today()
    case.investigator_code = body.investigator_code
    case.assigned_on = today
    case.status = Status.assigned
    db.add(CaseEvent(case_id=case.id, kind="assigned",
                     note=f"Routed to {body.investigator_code}, posted outside {case.department}.",
                     occurred_on=today))
    ledger.append(db, case.case_no, "investigator assigned", today)
    audit(db, actor, "case_assigned", case.case_no, detail=body.investigator_code)
    db.commit()
    db.refresh(case)
    return to_investigator(case)


@router.post("/cases/{case_no}/status", response_model=InvestigatorCase)
def change_status(case_no: str, body: StatusIn, actor: Actor = Depends(require_investigator),
                  db: Session = Depends(get_case_db)) -> InvestigatorCase:
    case = get_case_or_404(db, case_no)
    today = clock_today()

    if body.status == "investigating":
        case.status = Status.investigating
        milestone = "investigation opened"
    elif body.status == "confirmed":
        if not body.penalty:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                                "A confirmed finding must record the enforcement action taken.")
        case.status = Status.confirmed
        case.penalty = body.penalty
        case.decided_on = today
        # Only a substantiated finding may put a name on the public register.
        case.names_public = bool(body.publish_accused_name and case.accused_name)
        milestone = "finding published; penalty recorded"
    else:
        case.status = Status.closed
        case.decided_on = today
        case.names_public = False
        milestone = "closed, allegation not substantiated"

    db.add(CaseEvent(case_id=case.id, kind=body.status, note=body.note, occurred_on=today))
    ledger.append(db, case.case_no, milestone, today)
    audit(db, actor, f"status_{body.status}", case.case_no)
    db.commit()
    db.refresh(case)
    return to_investigator(case)


@router.get("/cases/{case_no}/contact", response_model=ContactOut)
def read_contact(case_no: str, actor: Actor = Depends(require_investigator),
                 db: Session = Depends(get_case_db)) -> ContactOut:
    """Open the sealed contact, where the complainant chose to leave one.

    Audited as its own action so that an investigator who opens contacts they
    have no reason to open is visible to the Ombudsman.
    """
    case = get_case_or_404(db, case_no)
    if not case.has_followup:
        return ContactOut(case_no=case.case_no, contact=None,
                          note="This report was filed anonymously. There is no contact to open.")

    if not get_settings().holds_intake_keys:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "This service is not configured to open sealed contacts.",
        )

    from ..intake_models import IntakeContact

    token = intake_token(case.case_no)
    with intake_session() as idb:
        row = idb.get(IntakeContact, token)

    audit(db, actor, "contact_opened", case.case_no)
    db.commit()

    if row is None:
        return ContactOut(case_no=case.case_no, contact=None,
                          note="The contact for this case has passed its 90-day retention and was destroyed.")
    try:
        plaintext = open_contact(row.ciphertext)
    except IntakeKeysUnavailable:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Decryption key unavailable in this process.") from None
    return ContactOut(case_no=case.case_no, contact=plaintext,
                      note="Opening this was logged. Use it only to progress this case.")
