"""One definition of "today".

`date.today()` uses the server's local timezone, which in a container is UTC.
A report filed at 02:00 in Delhi would then be dated the previous day - and
since `filed_on` starts the statutory 14-day assignment clock and appears on
the public register, that is a real error, not a cosmetic one.

Every date in this system comes from here, in the civic timezone.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

# The jurisdiction the register belongs to. Change this, not the call sites.
CIVIC_TZ = ZoneInfo("Asia/Kolkata")


def today() -> date:
    """The calendar day in the jurisdiction, for filing and statutory clocks."""
    return datetime.now(CIVIC_TZ).date()


def now() -> datetime:
    """Timezone-aware instant, for staff audit trails only.

    Never use this for anything a reporter touches: a timestamp on an intake
    record is an identifier. See models.Case.filed_on.
    """
    return datetime.now(UTC)
