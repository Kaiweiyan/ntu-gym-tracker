"""Manually-maintained calendar of whole-day venue closures.

Holidays, typhoon days, maintenance — these are unscheduled/one-off, so they
have no place in the fixed weekly hours table (`hours.py`) and can't be
inferred from the occupancy data either (an earlier attempt at that, guessing
from long runs of a real 0 reading, was removed for being both unreliable and
indirect). Recorded here by hand instead.

`data/closures.csv`: one row per closure range — `venue_id, date, end_date,
reason`. Each venue's calendar is independent (gym being closed has no
effect on pool) unless `venue_id` is left blank, which applies that row to
every known venue (e.g. a whole-building maintenance closure). `end_date`
blank means a single day. Small and simple enough to hand-edit directly (a
text editor, or a spreadsheet app's CSV export) rather than needing
`csv_tool.py`'s SQL machinery.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import date, datetime

from .config import CLOSURES_PATH, VENUE_ID_BY_NAME


@dataclass(frozen=True)
class Closure:
    start: date
    end: date
    reason: str

    @property
    def multi_day(self) -> bool:
        return self.end != self.start


def _load_rows() -> list[tuple[str, Closure]]:
    """(venue_id, Closure) pairs straight from the CSV; venue_id "" means
    "every venue" rather than naming one.
    """
    if not CLOSURES_PATH.exists():
        return []
    rows = []
    with CLOSURES_PATH.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            start = datetime.strptime(row["date"], "%Y-%m-%d").date()
            end = (
                datetime.strptime(row["end_date"], "%Y-%m-%d").date()
                if row.get("end_date")
                else start
            )
            # A blank reason still counts as a closure (the date range is
            # what matters) but is normalized to a placeholder so it doesn't
            # end up stored as a bare "venue_closed: " or shown as an empty
            # "本日休館：" card detail.
            reason = row.get("reason") or "未說明原因"
            rows.append((row.get("venue_id", "").strip(), Closure(start, end, reason)))
    return rows


def closures_on(d: date) -> dict[str, Closure]:
    """{venue_id: Closure} for every known venue with an active closure on
    `d`. Each venue is matched against its own rows plus any blank-venue_id
    ("every venue") row; the two venues' calendars are otherwise
    independent, so declaring gym closed has no effect on pool.
    """
    rows = _load_rows()
    out: dict[str, Closure] = {}
    for vid in VENUE_ID_BY_NAME.values():
        for row_venue, closure in rows:
            if row_venue and row_venue != vid:
                continue
            if closure.start <= d <= closure.end:
                out[vid] = closure
                break
    return out
