"""Venue opening hours (Asia/Taipei).

Used to skip scraping while the venues are closed: a closed venue is NOT the
same as "open with 0 people", so we record nothing rather than store a
misleading 0. Alignment across days is handled later at analysis time with an
`is_open` mask, not by zero-filling closed slots.

Opening hours (shared by 健身中心 and 室內游泳池):
    Mon-Fri  08:00-22:00
    Sat      09:00-22:00
    Sun      09:00-18:00
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")

# datetime.weekday(): Mon=0 ... Sun=6  ->  (open, close)
_HOURS: dict[int, tuple[time, time]] = {
    0: (time(8), time(22)),   # Mon
    1: (time(8), time(22)),   # Tue
    2: (time(8), time(22)),   # Wed
    3: (time(8), time(22)),   # Thu
    4: (time(8), time(22)),   # Fri
    5: (time(9), time(22)),   # Sat
    6: (time(9), time(18)),   # Sun
}


def now_taipei() -> datetime:
    return datetime.now(TAIPEI)


def is_open(dt: datetime | None = None) -> bool:
    """True if the venues are open at `dt` (Taipei time; defaults to now)."""
    dt = dt or now_taipei()
    open_t, close_t = _HOURS[dt.weekday()]
    return open_t <= dt.time() < close_t
