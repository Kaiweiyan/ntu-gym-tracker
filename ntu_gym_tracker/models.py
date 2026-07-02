"""Data structures shared across the scraping pipeline."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Observation:
    """A single occupancy reading for one venue at one point in time.

    `current_count` is optional: when a fetch or parse fails we still record a
    row (with `source_status` set accordingly) but leave it as None instead of
    guessing 0. `optimal_count` / `max_capacity` are fixed per venue and live in
    `config.VENUE_CAPACITY` instead of being carried per-row.
    """

    venue_id: str  # stable slug, e.g. "gym" / "pool"
    venue_name: str  # raw name from the page, e.g. "健身中心"
    scraped_at: str  # ISO-8601 UTC timestamp of when WE fetched it
    current_count: int | None
    source_status: str  # "ok" | "parse_error" | "fetch_error"


@dataclass(frozen=True)
class WeatherObservation:
    """Campus-wide weather at one scrape cycle (Open-Meteo current conditions).

    `scraped_at` matches the occupancy rows from the same cycle so the two CSVs
    join cleanly. `observed_at` is the weather's own valid time (Taipei) from the
    API, which updates roughly every 15 min.
    """

    scraped_at: str  # ISO-8601 UTC, same value as the cycle's occupancy rows
    observed_at: str | None  # weather valid time (local Taipei) from the API
    temperature_c: float | None
    apparent_temperature_c: float | None
    relative_humidity: int | None
    precipitation_mm: float | None
    weather_code: int | None  # WMO weather code
    wind_speed_kmh: float | None
    source_status: str  # "ok" | "fetch_error: ..."
