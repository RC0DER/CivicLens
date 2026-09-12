"""Scheduled sweeps. Run from cron, systemd timer or a worker:

    python -m app.jobs assignment_sweep
    python -m app.jobs retention_sweep
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from datetime import UTC, timedelta

from sqlalchemy import select

from . import ledger
from .clock import today as clock_today
from .config import get_settings
from .db import CaseSession, IntakeSession
from .deps import assignment_due
from .models import Case, CaseEvent, RevokedToken, Status, UsedTotp
from .observability import OVERDUE_FLAGGED, configure_logging, log


def assignment_sweep() -> int:
    """Publish every breach of the 14-day assignment limit.

    The breach is written to the public ledger, not to an internal queue. A
    department cannot let a case rot quietly; the lapse is on the record with
    the same prominence as a finding.
    """
    today = clock_today()
    flagged = 0
    with CaseSession() as db:
        for case in db.execute(select(Case).where(Case.status == Status.pending)).scalars():
            if case.overdue_flagged or today <= assignment_due(case):
                continue
            days = (today - case.filed_on).days
            case.overdue_flagged = True
            db.add(CaseEvent(case_id=case.id, kind="assignment_overdue",
                             note=f"Statutory 14-day assignment limit passed. Day {days} and unassigned.",
                             occurred_on=today))
            ledger.append(db, case.case_no, "assignment limit breached", today)
            OVERDUE_FLAGGED.inc()
            flagged += 1
        db.commit()
    return flagged


def session_sweep() -> int:
    """Drop revocation and one-time-code rows that no longer protect anything."""
    from datetime import datetime

    from sqlalchemy import delete

    now = datetime.now(UTC)
    cutoff_window = int(now.timestamp()) // 30 - 120  # keep an hour of TOTP windows
    with CaseSession() as db:
        revoked = db.execute(delete(RevokedToken).where(RevokedToken.expires_at < now)).rowcount or 0
        codes = db.execute(delete(UsedTotp).where(UsedTotp.window < cutoff_window)).rowcount or 0
        db.commit()
    return revoked + codes


def retention_sweep(days: int | None = None) -> int:
    """Destroy contact details older than the retention limit.

    The case survives permanently; the means of contacting the person who
    filed it does not. Runs only where the intake store is configured.
    """
    days = days if days is not None else get_settings().contact_retention_days
    if IntakeSession is None:
        print("no intake database in this process - nothing to sweep")
        return 0

    from .intake_models import IntakeContact

    cutoff = clock_today() - timedelta(days=days)
    with IntakeSession() as idb:
        rows = idb.execute(select(IntakeContact).where(IntakeContact.created_on < cutoff)).scalars().all()
        for row in rows:
            idb.delete(row)
        idb.commit()
    return len(rows)


JOBS: dict[str, tuple[Callable[[], int], str]] = {
    "assignment_sweep": (assignment_sweep, "flagged {} overdue assignment(s)"),
    "retention_sweep": (retention_sweep, "destroyed {} expired contact record(s)"),
    "session_sweep": (session_sweep, "cleared {} expired session row(s)"),
}

if __name__ == "__main__":
    configure_logging()
    name = sys.argv[1] if len(sys.argv) > 1 else "assignment_sweep"
    if name not in JOBS:
        sys.exit(f"unknown job: {name}. Choose from: {', '.join(JOBS)}")
    func, template = JOBS[name]
    count = func()
    log(logging.getLogger("civiclens.jobs"), logging.INFO, "job complete", job=name, affected=count)
    print(template.format(count))
