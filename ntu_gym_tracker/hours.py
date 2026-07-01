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


SLOT_MINUTES = 10  # collector cadence


def now_taipei() -> datetime:
    return datetime.now(TAIPEI)


def open_close(weekday: int) -> tuple[time, time]:
    """(open, close) times for a weekday (Mon=0 .. Sun=6)."""
    return _HOURS[weekday]


def is_open(dt: datetime | None = None) -> bool:
    """True if the venues are open at `dt` (Taipei time; defaults to now)."""
    dt = dt or now_taipei()
    open_t, close_t = _HOURS[dt.weekday()]
    return open_t <= dt.time() < close_t


def _slot(dt: datetime) -> tuple[int, int]:
    return (dt.hour, dt.minute // SLOT_MINUTES * SLOT_MINUTES)


def is_opening_tick(dt: datetime | None = None) -> bool:
    """True for the single ~10-min tick at opening time.

    The site can show a stale non-zero right at open, so the collector forces the
    first open slot to count=0; the next tick scrapes the settled real value.
    """
    dt = dt or now_taipei()
    open_t, _ = _HOURS[dt.weekday()]
    return _slot(dt) == _slot_of_time(open_t)


def is_closing_tick(dt: datetime | None = None) -> bool:
    """True for the single ~10-min tick at closing time.

    The venue is already "closed" at the exact close time, so this lets the
    collector record one count=0 row right at close (curve returns to 0) instead
    of just skipping. Only the first slot at/after the close time qualifies.
    """
    dt = dt or now_taipei()
    _, close_t = _HOURS[dt.weekday()]
    return _slot(dt) == _slot_of_time(close_t)


def _slot_of_time(t: time) -> tuple[int, int]:
    return (t.hour, t.minute // SLOT_MINUTES * SLOT_MINUTES)
