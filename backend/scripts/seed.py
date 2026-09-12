"""Seed a development register: officials, a few cases, a ledger with history.

    python -m scripts.seed
"""

from __future__ import annotations

import os
import sys
from datetime import timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import ledger
from app.clock import today as clock_today
from app.db import CaseSession, create_all
from app.models import Case, CaseCounter, CaseEvent, Official, Status
from app.security import hash_password, new_totp_secret

# Meets the password policy, and is still obviously a development credential.
DEMO_PASSWORD = "Register-Counter-Seal-42!"  # noqa: S105 - development seed only

OFFICES = {
    "Municipal Revenue & Property Tax": ("Delhi", "Central Delhi", "New Delhi", "110002",
                                         "Zonal Revenue Office, Block C, 2nd Floor, Civic Centre, Minto Road"),
    "Building Plan Approval": ("Delhi", "Central Delhi", "New Delhi", "110006",
                               "Building Sanction Branch, Room 214, Town Hall Annexe, Chandni Chowk"),
    "Water Supply & Sewerage": ("Delhi", "North West Delhi", "New Delhi", "110085",
                                "Consumer Services Centre, Jal Bhawan, Sector 9, Rohini"),
}

CASES = [
    ("Building Plan Approval", "C", "Bribery - payment demanded for a routine service",
     "Occupancy certificate cleared by the technical branch on 11 August was returned twice at counter 3 "
     "citing an annexure that is not required for residential plots. A figure of Rs 35,000 was quoted to "
     "move the file upstairs, payable in two instalments.",
     "Rs 35,000 in cash", "R. Khandelwal", "Junior Engineer, Sanction Branch", "MCD/BPA/2017/0219",
     Status.confirmed, "INV-C-114", 19,
     "Junior Engineer suspended pending departmental inquiry; file recommended for prosecution under s.7 PC Act."),
    ("Municipal Revenue & Property Tax", "C", "Extortion or threat by a public official",
     "Shopkeeper on the Central market row was told the annual valuation would be raised fourfold if the "
     "monthly collection stopped. Two adjacent shops report the same demand from the same inspector round.",
     "Rs 8,000 per month", None, "Assessment Inspector, Ward 22 round", "MCD/REV/2021/1187",
     Status.investigating, "INV-N-208", 22, None),
    ("Water Supply & Sewerage", "E", "Bribery - payment demanded for a routine service",
     "Disconnection notice was issued against the wrong consumer number. Restoration was quoted at "
     "Rs 4,500 as an urgent charge with no receipt offered.",
     "Rs 4,500", None, "Meter Inspector, Consumer Services", None,
     Status.assigned, "INV-C-133", 32, None),
    ("Municipal Revenue & Property Tax", "C", "Falsification of official records",
     "Receipt book 114-C shows eleven entries dated before the assessment they discharge. The physical "
     "counterfoils are missing from the treasury.",
     "Rs 6.7 lakh", "S. Bhatia", "Head Clerk, Receipt Section", "MCD/REV/2014/0663",
     Status.pending, None, 41, None),
]


def main() -> None:
    create_all()
    today = clock_today()

    with CaseSession() as db:
        counter = db.get(CaseCounter, today.year)
        if counter is None:
            counter = CaseCounter(year=today.year, last_no=4400)
            db.add(counter)
            db.flush()

        for code, dept, rank, role in [
            ("MCD/REV/2019/0447", "Municipal Revenue & Property Tax", "Deputy Commissioner", "dept"),
            ("MCD/BPA/2018/0112", "Building Plan Approval", "Assistant Commissioner", "dept"),
            ("OMB/INV/2020/0031", "Office of the Ombudsman", "Senior Investigator", "investigator"),
        ]:
            if db.get(Official, code) is None:
                secret = new_totp_secret()
                db.add(Official(employee_code=code, department=dept, rank=rank, role=role,
                                password_hash=hash_password(DEMO_PASSWORD), totp_secret=secret))
                print(f"{code:22} role={role:13} TOTP secret: {secret}")

        for (dept, zone, category, detail, amount, name, desig, emp_code,
             status, inv, age_days, penalty) in CASES:
            counter.last_no += 1
            case_no = f"CRTP-{today.year}-{counter.last_no:06d}"
            state, district, city, pin, local = OFFICES[dept]
            filed = today - timedelta(days=age_days)

            case = Case(
                case_no=case_no, category=category, department=dept, zone=zone,
                state=state, district=district, city=city, pin=pin, local_address=local,
                amount_text=amount, detail=detail,
                accused_name=name, accused_desig=desig, accused_code=emp_code,
                names_public=(status == Status.confirmed and name is not None),
                status=status, investigator_code=inv, penalty=penalty, filed_on=filed,
                assigned_on=(filed + timedelta(days=6)) if inv else None,
                decided_on=(filed + timedelta(days=15)) if status in (Status.confirmed, Status.closed) else None,
                has_followup=(status == Status.investigating),
            )
            db.add(case)
            db.flush()

            db.add(CaseEvent(case_id=case.id, kind="received",
                             note="Report received and sealed. Case number issued.", occurred_on=filed))
            ledger.append(db, case_no, "intake sealed", filed)
            if inv:
                db.add(CaseEvent(case_id=case.id, kind="assigned",
                                 note=f"Routed to {inv}, posted outside {dept}.",
                                 occurred_on=filed + timedelta(days=6)))
                ledger.append(db, case_no, "investigator assigned", filed + timedelta(days=6))
            if penalty:
                db.add(CaseEvent(case_id=case.id, kind="confirmed", note=penalty,
                                 occurred_on=filed + timedelta(days=15)))
                ledger.append(db, case_no, "finding published; penalty recorded", filed + timedelta(days=15))
            print(f"seeded {case_no}  {status.value:14} {dept}")

        db.commit()

    print(f"\nPassword for every seeded official: {DEMO_PASSWORD}")
    print("Feed the TOTP secret above into any authenticator to get the 6-digit code.")


if __name__ == "__main__":
    main()
