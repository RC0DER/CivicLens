"""Demonstration affordances.

A public demo has a problem a real deployment does not: a visitor cannot sign
in as a government officer, because they have no authenticator app enrolled
against a seeded employee code. This router hands out live TOTP codes for the
seeded demo accounts so the departmental and investigator views can be seen.

It is gated three ways, and the gates matter more than the feature:

  * DEMO_MODE must be on, and production refuses to start with it on;
  * only employee codes listed in DEMO_ACCOUNTS are served, never a real one;
  * it returns codes, never secrets, and never touches case or intake data.
"""

from __future__ import annotations

import logging

import pyotp
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_case_db
from ..models import Official
from ..observability import log

router = APIRouter(prefix="/demo", tags=["demo"])
logger = logging.getLogger("civiclens.demo")


@router.get("/accounts")
def demo_accounts(db: Session = Depends(get_case_db)) -> dict:
    s = get_settings()
    if not s.demo_mode:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Not found.")

    allowed = [code.strip() for code in s.demo_accounts.split(",") if code.strip()]
    rows = db.execute(select(Official).where(Official.employee_code.in_(allowed))).scalars().all()

    log(logger, logging.INFO, "demo credentials served", count=len(rows))
    return {
        "notice": (
            "Demonstration accounts only. These exist so a visitor can see the departmental "
            "and investigator views; they hold no real authority and no real data."
        ),
        "accounts": [
            {
                "employee_code": officer.employee_code,
                "department": officer.department,
                "role": officer.role,
                "password": s.demo_password,
                # Generated now, valid for the current 30-second step, and
                # single-use - the replay guard applies to demo accounts too.
                "otp": pyotp.TOTP(officer.totp_secret).now(),
            }
            for officer in rows
        ],
    }
