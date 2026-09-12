"""Append-only public ledger.

Every status change is written here within the statutory 24 hours, whether or
not it flatters the department. Each entry commits to the one before it, so a
row cannot be edited or removed later without breaking every hash after it -
which the /api/ledger/verify endpoint will report to anyone who asks.

The payload carries a case number, a milestone and a date. It never carries
the allegation text, the office address or a name.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from .models import LedgerEntry

GENESIS = "0" * 64


def _payload_hash(case_no: str, milestone: str, on: date) -> str:
    payload = json.dumps(
        {"case_no": case_no, "milestone": milestone, "on": on.isoformat()},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def append(db: Session, case_no: str, milestone: str, on: date) -> LedgerEntry:
    prev = db.execute(select(LedgerEntry).order_by(LedgerEntry.seq.desc()).limit(1)).scalar_one_or_none()
    prev_hash = prev.entry_hash if prev else GENESIS
    ph = _payload_hash(case_no, milestone, on)
    entry = LedgerEntry(
        case_no=case_no,
        milestone=milestone,
        recorded_on=on,
        payload_hash=ph,
        prev_hash=prev_hash,
        entry_hash=hashlib.sha256((prev_hash + ph).encode()).hexdigest(),
    )
    db.add(entry)
    db.flush()
    return entry


def verify(db: Session) -> dict:
    """Walk the chain. Returns the first break, if there is one."""
    rows = db.execute(select(LedgerEntry).order_by(LedgerEntry.seq)).scalars().all()
    prev_hash = GENESIS
    for row in rows:
        expected_payload = _payload_hash(row.case_no, row.milestone, row.recorded_on)
        expected_entry = hashlib.sha256((prev_hash + expected_payload).encode()).hexdigest()
        if row.prev_hash != prev_hash or row.payload_hash != expected_payload or row.entry_hash != expected_entry:
            return {"intact": False, "entries": len(rows), "broken_at": row.seq}
        prev_hash = row.entry_hash
    return {"intact": True, "entries": len(rows), "head": prev_hash}
