"""Government employee sign-in.

Password + TOTP, lockout held server-side, rate limited per employee code, and
a failure message that never says which factor was wrong. A used TOTP code is
recorded so it cannot be replayed inside its 30-second step.

In a real deployment this delegates to the department's existing SSO/LDAP and
this table holds only role and department - see docs/RUNBOOK.md.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .. import ratelimit
from ..config import get_settings
from ..db import get_case_db
from ..deps import Actor, audit, current_officer
from ..models import AuditLog, Official, RevokedToken, UsedTotp
from ..observability import LOCKOUTS, LOGIN_FAILURES, log
from ..schemas import LoginIn, TokenOut
from ..security import issue_token, totp_window, verify_password, verify_totp

router = APIRouter(prefix="/auth", tags=["auth"])
logger = logging.getLogger("civiclens.auth")

GENERIC_FAILURE = "Those credentials were not recognised."


@router.post("/official/login", response_model=TokenOut)
def login(body: LoginIn, db: Session = Depends(get_case_db)) -> TokenOut:
    s = get_settings()
    now = datetime.now(UTC)
    code = body.employee_code.strip()

    verdict = ratelimit.check(ratelimit.actor_bucket(code), s.rate_limit_login_per_hour, 3600,
                              route="/api/auth/official/login")
    if not verdict.allowed:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "Too many sign-in attempts for this employee code. Try again shortly.",
            headers={"Retry-After": str(verdict.retry_after)},
        )

    officer = db.execute(select(Official).where(Official.employee_code == code)).scalar_one_or_none()

    if officer is None or not officer.is_active:
        # Same answer as a bad password: never confirm which service records
        # exist to somebody probing employee codes.
        LOGIN_FAILURES.inc()
        log(logger, logging.WARNING, "login failed", reason="unknown_or_inactive")
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, GENERIC_FAILURE)

    if officer.locked_until and officer.locked_until > now:
        mins = int((officer.locked_until - now).total_seconds() // 60) + 1
        raise HTTPException(
            status.HTTP_423_LOCKED,
            f"This employee code is locked for another {mins} minutes. "
            "Your departmental nodal officer can reset it.",
        )

    password_ok = verify_password(body.password, officer.password_hash)
    totp_ok = verify_totp(officer.totp_secret, body.otp)

    if not (password_ok and totp_ok):
        officer.failed_attempts += 1
        LOGIN_FAILURES.inc()
        db.add(AuditLog(actor_code=officer.employee_code, actor_role=officer.role,
                        action="login_failed", detail=f"attempt {officer.failed_attempts}"))
        log(logger, logging.WARNING, "login failed", actor=officer.employee_code,
            attempts=officer.failed_attempts)
        if officer.failed_attempts >= s.login_max_attempts:
            officer.locked_until = now + timedelta(minutes=s.lockout_minutes)
            officer.failed_attempts = 0
            LOCKOUTS.inc()
            db.commit()
            raise HTTPException(
                status.HTTP_423_LOCKED,
                f"Too many attempts. This code is locked for {s.lockout_minutes} minutes.",
            )
        left = s.login_max_attempts - officer.failed_attempts
        db.commit()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            f"{GENERIC_FAILURE} {left} attempt(s) remaining before this code is locked.")

    # Burn the code. A second use inside the same window is refused even though
    # the arithmetic would still accept it.
    try:
        db.add(UsedTotp(employee_code=officer.employee_code, window=totp_window(now)))
        db.flush()
    except IntegrityError:
        db.rollback()
        LOGIN_FAILURES.inc()
        log(logger, logging.WARNING, "totp replay refused", actor=code)
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            "That one-time code has already been used. Wait for your authenticator to show the next one.",
        ) from None

    officer.failed_attempts = 0
    officer.locked_until = None
    token, jti, expires = issue_token(officer.employee_code, officer.role, officer.department)
    db.add(AuditLog(actor_code=officer.employee_code, actor_role=officer.role,
                    action="login_ok", detail=jti))
    db.commit()
    log(logger, logging.INFO, "login ok", actor=officer.employee_code, role=officer.role)

    return TokenOut(
        access_token=token,
        role=officer.role,
        department=officer.department,
        expires_in_minutes=s.jwt_ttl_minutes,
    )


@router.post("/official/logout")
def logout(actor: Actor = Depends(current_officer), db: Session = Depends(get_case_db)) -> dict:
    """Revoke this token now rather than waiting for it to expire."""
    db.add(RevokedToken(jti=actor.jti, expires_at=actor.expires_at))
    # Housekeeping: rows for tokens that have expired anyway are dead weight.
    db.execute(delete(RevokedToken).where(RevokedToken.expires_at < datetime.now(UTC)))
    audit(db, actor, "logout")
    db.commit()
    return {"signed_out": True}


@router.get("/me")
def me(actor: Actor = Depends(current_officer)) -> dict:
    return {"employee_code": actor.code, "role": actor.role, "department": actor.department}


@router.get("/audit")
def audit_trail(
    limit: int = 100,
    actor: Actor = Depends(current_officer),
    db: Session = Depends(get_case_db),
) -> list[dict]:
    """Ombudsman investigators read the whole trail. A departmental officer
    reads only their own entries - the log exists to hold officers to account,
    not to help a department see who is looking at its cases."""
    stmt = select(AuditLog).order_by(AuditLog.at.desc()).limit(min(limit, 500))
    if actor.role != "investigator":
        stmt = stmt.where(AuditLog.actor_code == actor.code)
    rows = db.execute(stmt).scalars().all()
    audit(db, actor, "audit_read")
    db.commit()
    return [
        {"at": r.at.isoformat(), "actor": r.actor_code, "role": r.actor_role,
         "action": r.action, "case_no": r.case_no, "detail": r.detail}
        for r in rows
    ]
