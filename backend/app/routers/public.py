"""Public routes: file a report, attach evidence, track any case, read the
register, the heatmap and the ledger.

No route in this module authenticates anybody. That is the design: an account
is a record of who reported what.
"""

from __future__ import annotations

import hashlib
import logging
from datetime import timedelta
from typing import Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    UploadFile,
    status,
)
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import ledger, ratelimit
from ..clock import today as clock_today
from ..config import get_settings
from ..db import get_case_db, intake_session
from ..deps import get_case_or_404, is_overdue, to_public
from ..evidence import EvidenceRejected, ingest
from ..models import Case, CaseCounter, CaseEvent, Evidence, IntakeThrottle, Status
from ..observability import REPORTS_FILED, log
from ..schemas import (
    Page,
    PublicCase,
    RegisterRow,
    ReportAccepted,
    ReportIn,
    Stats,
    ZoneDensity,
)
from ..security import IntakeKeysUnavailable, intake_token, issue_upload_token, read_token, seal_contact
from ..storage import StorageError, get_storage, verify_local

router = APIRouter(tags=["public"])
logger = logging.getLogger("civiclens.public")


def read_guard(request: Request) -> None:
    """Burst protection for read routes only.

    The client address is hashed with a per-process salt, used for the length
    of this call, and dropped. It is never logged, stored or returned - and
    this dependency is deliberately absent from the intake route, where even
    an ephemeral bucket would be a correlation risk.
    """
    s = get_settings()
    bucket = ratelimit.client_bucket(request.client.host if request.client else None)
    verdict = ratelimit.check(bucket, s.rate_limit_read_per_minute, 60, route=request.url.path)
    if not verdict.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many requests. Wait a moment and try again.",
            headers={"Retry-After": str(verdict.retry_after)},
        )

# Illustrative ward populations; replace with census figures per zone.
ZONE_POPULATION = {"N": 812_000, "C": 690_000, "S": 934_000, "E": 755_000}


def _next_case_no(db: Session) -> str:
    year = clock_today().year
    row = db.execute(
        select(CaseCounter).where(CaseCounter.year == year).with_for_update()
    ).scalar_one_or_none()
    if row is None:
        row = CaseCounter(year=year, last_no=4000)
        db.add(row)
        db.flush()
    row.last_no += 1
    return f"CRTP-{year}-{row.last_no:06d}"


def _throttle(db: Session, department: str, pin: str) -> None:
    """Flood control that records nothing about the person filing.

    The bucket is derived from the office, not the reporter - so a campaign
    against one office is slowed, and a citizen reporting three different
    offices in one day is not.
    """
    s = get_settings()
    today = clock_today()
    bucket = hashlib.sha256(f"{department}|{pin}".encode()).hexdigest()[:32]
    row = db.execute(
        select(IntakeThrottle).where(IntakeThrottle.bucket == bucket, IntakeThrottle.day == today)
    ).scalar_one_or_none()
    if row is None:
        db.add(IntakeThrottle(bucket=bucket, day=today, count=1))
        return
    if row.count >= s.rate_limit_reports_per_office_per_day:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "This office has received an unusual number of reports today and intake is paused "
            "for a few hours. Call the helpline on 1800-11-0031 if the matter is urgent.",
        )
    row.count += 1


@router.post("/reports", response_model=ReportAccepted, status_code=status.HTTP_201_CREATED)
def file_report(body: ReportIn, db: Session = Depends(get_case_db)) -> ReportAccepted:
    s = get_settings()
    _throttle(db, body.department, body.office.pin)

    today = clock_today()
    case_no = _next_case_no(db)

    case = Case(
        case_no=case_no,
        category=body.category,
        department=body.department,
        zone=body.zone,
        state=body.office.state,
        district=body.office.district,
        city=body.office.city,
        pin=body.office.pin,
        local_address=body.office.local_address,
        amount_text=body.amount_text,
        detail=body.detail,
        accused_name=(body.accused.name if body.accused else None),
        accused_desig=(body.accused.designation if body.accused else None),
        accused_code=(body.accused.employee_code if body.accused else None),
        names_public=False,
        status=Status.pending,
        filed_on=today,          # a DATE - the clock time of filing is never stored
        has_followup=bool(body.followup_contact),
    )
    db.add(case)
    db.flush()

    db.add(CaseEvent(case_id=case.id, kind="received",
                     note="Report received and sealed. Case number issued.", occurred_on=today))

    # The contact, if any, goes to the other database under a token this
    # process can compute only because it holds the HMAC key. The public
    # profile does not hold that key, so in production this branch raises and
    # the deployment is misconfigured rather than silently leaking.
    if body.followup_contact:
        try:
            token = intake_token(case_no)
            sealed = seal_contact(body.followup_contact)
        except IntakeKeysUnavailable:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "Encrypted follow-up is unavailable right now. Your report can still be filed "
                "anonymously - remove the contact number and submit again.",
            ) from None
        from ..intake_models import IntakeContact

        with intake_session() as idb:
            idb.merge(IntakeContact(token=token, ciphertext=sealed, created_on=today))
            idb.commit()

    ledger.append(db, case_no, "intake sealed", today)
    db.commit()
    REPORTS_FILED.inc()
    # Department and zone only: enough to spot an intake outage or a flood,
    # and not enough to narrow down who filed.
    log(logger, logging.INFO, "report accepted", department=case.department, zone=case.zone)

    return ReportAccepted(
        case_no=case_no,
        filed_on=today,
        status=Status.pending.value,
        upload_token=issue_upload_token(case_no),
        assignment_due_on=today + timedelta(days=s.assignment_limit_days),
        message=("Write this case number down. It is the only way back to this report, "
                 "and it cannot be recovered from anything else you told us."),
    )


@router.post("/reports/{case_no}/evidence", status_code=status.HTTP_201_CREATED)
async def attach_evidence(
    case_no: str = Path(...),
    upload_token: str = Query(..., description="Issued with the case number at filing"),
    file: UploadFile = File(...),
    db: Session = Depends(get_case_db),
) -> dict:
    try:
        claims = read_token(upload_token)
    except Exception:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            "That upload link has expired. File the evidence with a fresh report "
                            "referencing this case number.") from None
    if claims.get("role") != "upload" or claims.get("sub") != case_no.upper():
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That upload link belongs to another case.")

    case = get_case_or_404(db, case_no)
    s = get_settings()
    if len(case.evidence) >= s.max_evidence_per_case:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"This case already holds {s.max_evidence_per_case} files.")

    raw = await file.read()
    try:
        stored = ingest(raw)
    except EvidenceRejected as exc:
        raise HTTPException(status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, str(exc)) from exc

    db.add(Evidence(case_id=case.id, stored_name=stored.stored_name, media_type=stored.media_type,
                    size_bytes=stored.size_bytes, sha256=stored.sha256,
                    metadata_stripped=stored.metadata_stripped, received_on=clock_today()))
    db.add(CaseEvent(case_id=case.id, kind="evidence", note="Evidence admitted; metadata stripped on ingest.",
                     occurred_on=clock_today()))
    ledger.append(db, case.case_no, "evidence admitted", clock_today())
    db.commit()

    return {
        "sha256": stored.sha256,
        "media_type": stored.media_type,
        "size_bytes": stored.size_bytes,
        "metadata_stripped": stored.metadata_stripped,
        "note": "Location, device and authorship data were removed before this file was written to disk.",
    }


@router.get("/cases/{case_no}", response_model=PublicCase, dependencies=[Depends(read_guard)])
def track(case_no: str, db: Session = Depends(get_case_db)) -> PublicCase:
    return to_public(get_case_or_404(db, case_no))


@router.get("/register", response_model=Page, dependencies=[Depends(read_guard)])
def register(
    q: str | None = Query(default=None, max_length=120),
    department: str | None = None,
    zone: str | None = None,
    status_filter: str | None = Query(default=None, alias="status"),
    page: int = Query(default=1, ge=1),
    per_page: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_case_db),
) -> Page:
    stmt = select(Case)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(
            Case.case_no.ilike(like) | Case.department.ilike(like)
            | Case.district.ilike(like) | Case.category.ilike(like) | Case.city.ilike(like)
        )
    if department:
        stmt = stmt.where(Case.department == department)
    if zone:
        stmt = stmt.where(Case.zone == zone)
    if status_filter:
        stmt = stmt.where(Case.status == status_filter)

    total = db.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
    rows = db.execute(
        stmt.order_by(Case.filed_on.desc(), Case.case_no.desc())
        .offset((page - 1) * per_page).limit(per_page)
    ).scalars().all()

    return Page(
        total=total, page=page, per_page=per_page,
        rows=[
            RegisterRow(
                case_no=c.case_no, category=c.category, department=c.department, zone=c.zone,
                district=c.district,
                status=c.status.value if isinstance(c.status, Status) else str(c.status),
                investigator_code=c.investigator_code, filed_on=c.filed_on,
                overdue=is_overdue(c), penalty=c.penalty,
            )
            for c in rows
        ],
    )


@router.get("/stats", response_model=Stats, dependencies=[Depends(read_guard)])
def stats(db: Session = Depends(get_case_db)) -> Stats:
    rows = db.execute(select(Case.status, func.count()).group_by(Case.status)).all()
    by_status: dict[str, int] = {
        (status.value if isinstance(status, Status) else str(status)): int(count)
        for status, count in rows
    }
    total = sum(by_status.values())
    acted = total - by_status.get("pending", 0)

    gaps = [
        (c.assigned_on - c.filed_on).days
        for c in db.execute(select(Case).where(Case.assigned_on.is_not(None))).scalars()
        if c.assigned_on is not None  # the WHERE guarantees it; this narrows the type
    ]
    gaps.sort()
    median = None
    if gaps:
        mid = len(gaps) // 2
        median = float(gaps[mid]) if len(gaps) % 2 else (gaps[mid - 1] + gaps[mid]) / 2

    overdue = sum(1 for c in db.execute(select(Case)).scalars() if is_overdue(c))

    return Stats(
        total=total, by_status=by_status,
        acted_on_pct=round(acted / total * 100) if total else 0,
        median_days_to_assign=median, overdue=overdue,
    )


@router.get("/heatmap", response_model=list[ZoneDensity], dependencies=[Depends(read_guard)])
def heatmap(db: Session = Depends(get_case_db)) -> list[ZoneDensity]:
    out: list[ZoneDensity] = []
    for zone, population in ZONE_POPULATION.items():
        reports = db.execute(select(func.count()).select_from(Case).where(Case.zone == zone)).scalar_one()
        confirmed = db.execute(
            select(func.count()).select_from(Case).where(Case.zone == zone, Case.status == Status.confirmed)
        ).scalar_one()
        per_10k = round(reports / population * 10_000, 2)
        band: Literal["low", "moderate", "high"] = (
            "high" if per_10k >= 0.5 else "moderate" if per_10k >= 0.2 else "low"
        )
        out.append(ZoneDensity(zone=zone, reports=reports, confirmed=confirmed, per_10k=per_10k, band=band))
    return out


@router.get("/ledger")
def read_ledger(limit: int = Query(default=50, ge=1, le=500), db: Session = Depends(get_case_db)) -> list[dict]:
    from ..models import LedgerEntry

    rows = db.execute(select(LedgerEntry).order_by(LedgerEntry.seq.desc()).limit(limit)).scalars().all()
    return [
        {"seq": r.seq, "case_no": r.case_no, "milestone": r.milestone,
         "on": r.recorded_on.isoformat(), "entry_hash": r.entry_hash[:12]}
        for r in rows
    ]


@router.get("/ledger/verify")
def verify_ledger(db: Session = Depends(get_case_db)) -> dict:
    return ledger.verify(db)


@router.get("/evidence/{key}", include_in_schema=False)
def local_evidence(key: str, expires: int, signature: str) -> Response:
    """Signed, expiring download for the local storage backend.

    Production uses S3 presigned URLs instead, so that the object store's own
    access log records the fetch. This route exists so development behaves the
    same way rather than serving evidence off an open path.
    """
    if get_settings().storage_backend != "local":
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")
    if not verify_local(key, expires, signature):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "That link has expired. Open the case file again.")
    try:
        data = get_storage().get(key)
    except StorageError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "That file is no longer held.") from None
    return Response(data, media_type="application/octet-stream",
                    headers={"Content-Disposition": "attachment"})
