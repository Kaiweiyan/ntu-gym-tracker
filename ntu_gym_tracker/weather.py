"""Fetch current weather for the NTU campus from Open-Meteo.

Open-Meteo is free, needs no API key, and also exposes a historical archive, so
gaps can be backfilled later for model training. We log a lean set of WMO
current fields each cycle and store them keyed by `scraped_at` so they join to
the occupancy rows of the same cycle.

Like the occupancy scraper, a failure becomes a row (source_status="fetch_error")
rather than an exception, so one bad weather call never blocks the cycle.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx

from .config import (
    NTU_LATITUDE,
    NTU_LONGITUDE,
    OPEN_METEO_CURRENT_FIELDS,
    OPEN_METEO_URL,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)
from .models import WeatherObservation


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def fetch_weather(scraped_at: str | None = None) -> WeatherObservation:
    """Fetch current campus weather. Never raises — failures become a row."""
    scraped_at = scraped_at or _utc_now_iso()
    params = {
        "latitude": NTU_LATITUDE,
        "longitude": NTU_LONGITUDE,
        "current": OPEN_METEO_CURRENT_FIELDS,
        "timezone": "Asia/Taipei",
    }
    try:
        resp = httpx.get(
            OPEN_METEO_URL,
            params=params,
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        resp.raise_for_status()
        current = resp.json()["current"]
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        return WeatherObservation(
            scraped_at=scraped_at,
            observed_at=None,
            temperature_c=None,
            apparent_temperature_c=None,
            relative_humidity=None,
            precipitation_mm=None,
            weather_code=None,
            wind_speed_kmh=None,
            source_status=f"fetch_error: {type(exc).__name__}",
        )

    return WeatherObservation(
        scraped_at=scraped_at,
        observed_at=current.get("time"),
        temperature_c=current.get("temperature_2m"),
        apparent_temperature_c=current.get("apparent_temperature"),
        relative_humidity=current.get("relative_humidity_2m"),
        precipitation_mm=current.get("precipitation"),
        weather_code=current.get("weather_code"),
        wind_speed_kmh=current.get("wind_speed_10m"),
        source_status="ok",
    )
