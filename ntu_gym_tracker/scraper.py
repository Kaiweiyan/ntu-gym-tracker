"""Fetch the venue page and turn it into Observation rows.

Flow: fetch HTML -> parse -> (caller stores). `scrape()` retries the fetch+parse
pair together (see its docstring) so a transient failure at either stage gets
a second chance within the same cycle; only once every attempt fails do we
return a placeholder Observation (`source_status` = "fetch_error: ..." or
"parse_error: ...") so the outage is recorded as a gap-with-reason rather than
silently lost.
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
    """One GET attempt as a polite, identifiable client. Raises httpx.HTTPError
    on failure — retries live in `scrape()`, which wraps this together with
    parsing so both stages share one retry budget."""
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
    the same cycle (so the two CSVs join exactly); defaults to now (UTC) if
    omitted. Either way it's captured once, up front, and reused unchanged
    across every retry below — a retry re-samples occupancy for the *same*
    scheduled tick, it does not shift the tick to whenever the retry happens
    to actually run.

    Retries the fetch+parse pair up to `FETCH_RETRIES` times with exponential
    backoff (`FETCH_RETRY_BACKOFF_SECONDS`) before giving up for the cycle.
    Both a transient HTTP failure (timeout/connection/5xx) and a transient
    parse failure (e.g. the page briefly rendering an incomplete/maintenance
    body) get the same second chance — from here, neither can be told apart
    from a genuine site outage without simply trying again. This only rescues
    *this cycle's* attempt — occupancy is a live, time-varying number, so a
    retry can never recover a reading that was genuinely never taken.
    """
    scraped_at = scraped_at or utc_now_iso()
    last_exc: httpx.HTTPError | ValueError | None = None
    for attempt in range(FETCH_RETRIES):
        try:
            html = fetch_html()
            return parse_observations(html, scraped_at)
        except (httpx.HTTPError, ValueError) as exc:
            last_exc = exc
            if attempt < FETCH_RETRIES - 1:
                time.sleep(FETCH_RETRY_BACKOFF_SECONDS * 2**attempt)

    assert last_exc is not None
    if isinstance(last_exc, httpx.HTTPError):
        venue_id, status = "_fetch", f"fetch_error: {type(last_exc).__name__}"
    else:
        venue_id, status = "_parse", f"parse_error: {last_exc}"
    return [
        Observation(
            venue_id=venue_id,
            venue_name="",
            scraped_at=scraped_at,
            current_count=None,
            source_status=status,
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
