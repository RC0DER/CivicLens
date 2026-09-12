"""Intake store - the only place a contact detail is ever written.

One table, and note its primary key: there is no case_no column. The key is
HMAC-SHA256(INTAKE_HMAC_KEY, case_no), so a complete dump of this database
shows a list of opaque tokens against ciphertext. Without the HMAC key you
cannot ask "what is the contact for case CRTP-2026-004417?", because you
cannot compute which row to look at. Without the encryption key the row is
unreadable even if you find it.

Both keys live only in the investigator service's environment.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import Date, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import IntakeBase


class IntakeContact(IntakeBase):
    __tablename__ = "intake_contacts"

    token: Mapped[str] = mapped_column(String(64), primary_key=True)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)

    # Retention clock. A sweep deletes rows older than 90 days, after which
    # the case survives and the means of contact does not.
    created_on: Mapped[date] = mapped_column(Date, index=True)
