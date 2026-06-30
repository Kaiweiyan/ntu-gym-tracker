"""Fetch the venue page and turn it into Observation rows.

Flow: fetch HTML -> parse -> (caller stores). On a network/HTTP failure we
return a single placeholder Observation with source_status="fetch_error" so
the outage is recorded as a gap-with-reason rather than silently lost.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .config import REQUEST_TIMEOUT_SECONDS, SOURCE_URL, USER_AGENT, VENUE_ID_BY_NAME
from .models import Observation
from .parser import parse_observations


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_html(url: str = SOURCE_URL) -> str:
    """GET the page as a polite, identifiable client. Raises on HTTP errors."""
    resp = httpx.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp.text


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
                optimal_count=None,
                max_capacity=None,
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
                optimal_count=None,
                max_capacity=None,
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
            optimal_count=None,
            max_capacity=None,
            source_status=status,
        )
        for venue_name, venue_id in VENUE_ID_BY_NAME.items()
    ]
