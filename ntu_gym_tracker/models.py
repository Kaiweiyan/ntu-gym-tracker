"""Data structures shared across the scraping pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    """A single occupancy reading for one venue at one point in time.

    `current_count` is optional: when a fetch/parse fails, or the venue has a
    manually declared closure today (see `closures.py`), we still record a
    row (with `source_status` set accordingly) but leave it None instead of
    guessing 0 — this keeps the row excluded from historical
    aggregates/forecasts. The open/close boundary ticks are the one
    exception: they deliberately use a real `0`, not None, so a chart line
    can return to 0 at close (see `scraper.zero_observation`).
    `optimal_count` / `max_capacity` are fixed per venue and live in
    `config.VENUE_CAPACITY` instead of being carried per-row.
    """

    venue_id: str  # stable slug, e.g. "gym" / "pool"
    venue_name: str  # raw name from the page, e.g. "健身中心"
    scraped_at: str  # ISO-8601 UTC timestamp of when WE fetched it
    current_count: int | None
    # "ok" | "open" | "closed" | "fetch_error: ..." | "parse_error: ..." |
    # "venue_closed: <reason>" | "sensor_error: <reason>"
    #
    # "sensor_error" isn't written by any code path here — it's a manual
    # csv_tool.py correction label for a scrape that succeeded (so it isn't
    # a fetch/parse error) but got a known-wrong reading from the site's own
    # counter while the venue was actually open (so it isn't venue_closed
    # either); current_count is set to NULL the same way, so it's excluded
    # from training like every other null-count status. First used 2026-10,
    # when gym's on-site counter reported 0 for ~5 days despite real foot
    # traffic.
    source_status: str
