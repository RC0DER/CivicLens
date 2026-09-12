"""Departmental portal.

Two guarantees, enforced in two different ways:

1. An officer sees only their own department's cases - checked here.
2. An officer cannot see a reporter - not checked here, because there is
   nothing to check. This module never imports intake_models, never calls
   intake_token, and returns DeptCase, which has no reporter field. In
   production the dept service also runs without the intake database URL, so
   even a code change here could not reach contact data at runtime.

Every read is written to the audit log before the response is returned.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clock import today as clock_today
from ..db import get_case_db
from ..deps import Actor, audit, current_officer, get_case_or_404, is_overdue, to_dept
from ..models import Case, CaseEvent, Status
from ..schemas import DeptCase, ReplyIn
from ..storage import get_storage

router = APIRouter(prefix="/dept", tags=["department"])


def _own_department(case: Case, actor: Actor) -> None:
    if actor.role == "investigator":
        return
    if case.department != actor.department:
        # Deliberately 404, not 403: confirming that a case exists against
        # another department is itself information.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No case with that number is on your department's file.")


@router.get("/summary")
def summary(actor: Actor = Depends(current_officer), db: Session = Depends(get_case_db)) -> dict:
    dept = actor.department
    base = select(Case).where(Case.department == dept)
    cases = db.execute(base).scalars().all()
    open_now = [c for c in cases if c.status in (Status.assigned, Status.investigating)]
    audit(db, actor, "summary_read", detail=dept or "")
    db.commit()
    return {
        "department": dept,
        "received": len(cases),
        "active_investigations": len(open_now),
        "resolved": len([c for c in cases if c.status in (Status.confirmed, Status.closed)]),
        "awaiting_assignment": len([c for c in cases if c.status == Status.pending]),
        "overdue_assignments": len([c for c in cases if is_overdue(c)]),
        "reply_due": len([c for c in cases if c.status == Status.assigned]),
    }


@router.get("/cases", response_model=list[DeptCase])
def list_cases(
    status_filter: str | None = None,
    actor: Actor = Depends(current_officer),
    db: Session = Depends(get_case_db),
) -> list[DeptCase]:
    stmt = select(Case).where(Case.department == actor.department)
    if status_filter:
        stmt = stmt.where(Case.status == status_filter)
    rows = db.execute(stmt.order_by(Case.filed_on.desc())).scalars().all()
    audit(db, actor, "case_list_read", detail=f"{len(rows)} rows")
    db.commit()
    return [to_dept(c) for c in rows]


@router.get("/cases/{case_no}", response_model=DeptCase)
def read_case(case_no: str, actor: Actor = Depends(current_officer),
              db: Session = Depends(get_case_db)) -> DeptCase:
    case = get_case_or_404(db, case_no)
    _own_department(case, actor)
    audit(db, actor, "case_file_opened", case.case_no)
    db.commit()
    return to_dept(case)


@router.post("/cases/{case_no}/reply", response_model=DeptCase)
def departmental_reply(case_no: str, body: ReplyIn, actor: Actor = Depends(current_officer),
                       db: Session = Depends(get_case_db)) -> DeptCase:
    """The department's answer to the allegation. Published with the case -
    a department that responds on the record gets that on the record too."""
    case = get_case_or_404(db, case_no)
    _own_department(case, actor)
    db.add(CaseEvent(case_id=case.id, kind="departmental_reply",
                     note=body.note, occurred_on=clock_today(), public=True))
    audit(db, actor, "reply_filed", case.case_no)
    db.commit()
    db.refresh(case)
    return to_dept(case)


@router.get("/evidence/{sha256}")
def evidence_manifest(sha256: str, actor: Actor = Depends(current_officer),
                      db: Session = Depends(get_case_db)) -> dict:
    """Metadata plus a short-lived signed URL.

    The file is never served from a stable path: a link copied out of this
    portal stops working within minutes, and the object store logs the fetch
    against the presigned request.
    """
    from ..models import Evidence

    ev = db.execute(select(Evidence).where(Evidence.sha256 == sha256)).scalar_one_or_none()
    if not ev:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No evidence with that digest.")
    case = db.get(Case, ev.case_id)
    if case is None:
        # Orphaned evidence row. Refuse rather than serve a file whose case -
        # and therefore whose department scope - cannot be established.
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No evidence with that digest.")
    _own_department(case, actor)
    audit(db, actor, "evidence_download_url_issued", case.case_no, detail=sha256[:12])
    db.commit()
    return {"sha256": ev.sha256, "media_type": ev.media_type, "size_bytes": ev.size_bytes,
            "metadata_stripped": ev.metadata_stripped, "received_on": ev.received_on.isoformat(),
            "download_url": get_storage().signed_url(ev.stored_name),
            "note": "This link expires shortly and the download is logged."}
