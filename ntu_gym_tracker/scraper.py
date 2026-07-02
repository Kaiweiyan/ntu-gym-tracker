"""Fetch the venue page and turn it into Observation rows.

Flow: fetch HTML -> parse -> (caller stores). On a network/HTTP failure we
return a single placeholder Observation with source_status="fetch_error" so
the outage is recorded as a gap-with-reason rather than silently lost.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone

import httpx

from .config import (
    FETCH_RETRIES,
    FETCH_RETRY_BACKOFF_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    SOURCE_URL,
    USER_AGENT,
    VENUE_ID_BY_NAME,
)
from .models import Observation
from .parser import parse_observations


def utc_now_iso() -> str:
    # Second precision is plenty for a 10-min collection cadence.
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def fetch_html(url: str = SOURCE_URL) -> str:
    """GET the page as a polite, identifiable client.

    Retries transient failures (timeouts, connection errors, 5xx) up to
    `FETCH_RETRIES` times with exponential backoff — this only rescues the
    fetch itself within the same cycle; it can't recover an occupancy value we
    never sampled (the count is a live, time-varying number, not a resource
    that can be re-fetched later). Raises the last error if every attempt
    fails.
    """
    last_exc: httpx.HTTPError | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            resp = httpx.get(
                url,
                headers={"User-Agent": USER_AGENT},
                timeout=REQUEST_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            resp.raise_for_status()
            return resp.text
        except httpx.HTTPError as exc:
            last_exc = exc
            if attempt < FETCH_RETRIES - 1:
                time.sleep(FETCH_RETRY_BACKOFF_SECONDS * 2**attempt)
    assert last_exc is not None
    raise last_exc


def scrape(scraped_at: str | None = None) -> list[Observation]:
    """Run one scrape cycle, never raising — failures become rows instead.

    Pass `scraped_at` to share one timestamp across occupancy + weather rows of
    the same cycle (so the two CSVs join exactly); defaults to now (UTC).
    """
    scraped_at = scraped_at or utc_now_iso()
    try:
        html = fetch_html()
    except httpx.HTTPError as exc:
        return [
            Observation(
                venue_id="_fetch",
                venue_name="",
                scraped_at=scraped_at,
                current_count=None,
                source_status=f"fetch_error: {type(exc).__name__}",
            )
        ]

    try:
        return parse_observations(html, scraped_at)
    except ValueError as exc:
        return [
            Observation(
                venue_id="_parse",
                venue_name="",
                scraped_at=scraped_at,
                current_count=None,
                source_status=f"parse_error: {exc}",
            )
        ]


def zero_observations(scraped_at: str, status: str) -> list[Observation]:
    """One count=0 row per known venue, marking an open/close boundary.

    Used at the opening tick (the site can show a stale non-zero right at open)
    and the closing tick (curve returns to 0). `status` is "open"/"closed" for
    provenance; aggregation includes these rows because the count is non-null.
    We don't hit the site for boundary markers.
    """
    return [
        Observation(
            venue_id=venue_id,
            venue_name=venue_name,
            scraped_at=scraped_at,
            current_count=0,
            source_status=status,
        )
        for venue_name, venue_id in VENUE_ID_BY_NAME.items()
    ]
