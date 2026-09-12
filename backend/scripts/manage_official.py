"""Create, disable and unlock official accounts.

There is deliberately no self-service registration endpoint: an account on this
platform is an authorisation to read complaints against a department, and it is
issued by a person, not by a form.

    python -m scripts.manage_official add --employee-code MCD/REV/2019/0447 \
        --department "Municipal Revenue & Property Tax" --rank "Deputy Commissioner" --role dept
    python -m scripts.manage_official unlock --employee-code MCD/REV/2019/0447
    python -m scripts.manage_official disable --employee-code MCD/REV/2019/0447
    python -m scripts.manage_official list
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db import CaseSession
from app.models import Official
from app.security import WeakPassword, check_password_policy, hash_password, new_totp_secret


def _generate_passphrase() -> str:
    words = ["Register", "Counter", "Ledger", "Notice", "Bench", "Warrant", "Seal", "Record"]
    return "-".join(secrets.choice(words) for _ in range(3)) + "-" + str(secrets.randbelow(90) + 10) + "!"


def add(args: argparse.Namespace) -> None:
    password = args.password or _generate_passphrase()
    try:
        check_password_policy(password)
    except WeakPassword as exc:
        sys.exit(f"refused: {exc}")

    secret = new_totp_secret()
    with CaseSession() as db:
        if db.get(Official, args.employee_code) is not None:
            sys.exit(f"{args.employee_code} already exists. Use 'disable' then re-add if this is a rotation.")
        db.add(Official(
            employee_code=args.employee_code, department=args.department, rank=args.rank,
            role=args.role, password_hash=hash_password(password), totp_secret=secret,
        ))
        db.commit()

    print(f"created   {args.employee_code}  role={args.role}  dept={args.department}")
    print(f"password  {password}")
    print(f"TOTP      {secret}")
    print()
    print("Shown once. Hand these over in person or through the department's existing")
    print("credential channel - never by email, and never in a ticket.")


def unlock(args: argparse.Namespace) -> None:
    with CaseSession() as db:
        officer = db.get(Official, args.employee_code)
        if officer is None:
            sys.exit(f"no such employee code: {args.employee_code}")
        officer.locked_until = None
        officer.failed_attempts = 0
        db.commit()
    print(f"unlocked  {args.employee_code}")


def disable(args: argparse.Namespace) -> None:
    with CaseSession() as db:
        officer = db.get(Official, args.employee_code)
        if officer is None:
            sys.exit(f"no such employee code: {args.employee_code}")
        officer.is_active = False
        db.commit()
    print(f"disabled  {args.employee_code} - existing tokens expire within their TTL")


def list_officials(_: argparse.Namespace) -> None:
    from sqlalchemy import select

    with CaseSession() as db:
        rows = db.execute(select(Official).order_by(Official.department)).scalars().all()
    for r in rows:
        state = "active" if r.is_active else "disabled"
        locked = " LOCKED" if r.locked_until else ""
        print(f"{r.employee_code:24} {r.role:13} {state:9}{locked}  {r.department}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="create an official account")
    p_add.add_argument("--employee-code", required=True)
    p_add.add_argument("--department", required=True)
    p_add.add_argument("--rank", required=True)
    p_add.add_argument("--role", choices=["dept", "investigator"], default="dept")
    p_add.add_argument("--password", help="omit to generate a compliant passphrase")
    p_add.set_defaults(func=add)

    for name, func, helptext in [
        ("unlock", unlock, "clear a lockout after too many failed attempts"),
        ("disable", disable, "deactivate an account"),
    ]:
        p = sub.add_parser(name, help=helptext)
        p.add_argument("--employee-code", required=True)
        p.set_defaults(func=func)

    sub.add_parser("list", help="list accounts").set_defaults(func=list_officials)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
