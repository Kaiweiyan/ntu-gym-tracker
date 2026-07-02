"""Append-only CSV storage (the committed, canonical store for the collector).

Each scrape cycle appends one row per venue. The header is written once when
the file is first created. CSV is chosen so the data lives in git history with
clean, reviewable diffs and loads trivially into pandas/SQLite later.
"""

from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path

from .config import CSV_PATH
from .models import Observation

CSV_FIELDS = [
    "venue_id",
    "venue_name",
    "scraped_at",
    "current_count",
    "source_status",
]


def _blank(value: object) -> object:
    """Encode None as an empty CSV cell."""
    return "" if value is None else value


def append_observations(observations: Iterable[Observation], path: Path = CSV_PATH) -> int:
    """Append observations to the CSV (writing a header if the file is new).

    Returns the number of rows written.
    """
    observations = list(observations)
    path.parent.mkdir(parents=True, exist_ok=True)
    is_new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        if is_new:
            writer.writeheader()
        for o in observations:
            writer.writerow(
                {
                    "venue_id": o.venue_id,
                    "venue_name": o.venue_name,
                    "scraped_at": o.scraped_at,
                    "current_count": _blank(o.current_count),
                    "source_status": o.source_status,
                }
            )
    return len(observations)
