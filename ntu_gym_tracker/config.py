"""Static configuration for the scraper."""

from __future__ import annotations

from pathlib import Path

# Source page. Occupancy numbers are server-rendered directly in this HTML,
# so a plain GET (no JS engine) is enough.
SOURCE_URL = "https://rent.pe.ntu.edu.tw/"

# Identifiable, polite User-Agent. Frequency is kept low (see scheduler).
USER_AGENT = "ntu-gym-tracker/0.1 (personal project; contact: kaiweimail02@gmail.com)"

REQUEST_TIMEOUT_SECONDS = 15.0

# Retry transient fetch failures (timeout, connection error, 5xx) within the
# same cycle before giving up. Backoff is exponential: base * 2**attempt.
FETCH_RETRIES = 3
FETCH_RETRY_BACKOFF_SECONDS = 1.0

# Map the page's raw venue names to stable slugs used as `venue_id`.
# Unknown names fall back to a slugified version of the name.
VENUE_ID_BY_NAME: dict[str, str] = {
    "健身中心": "gym",
    "室內游泳池": "pool",
}

# optimal_count / max_capacity are fixed per venue (confirmed unchanging across
# all scrapes so far — see spec.md M0), so they live here instead of being
# scraped and stored on every row. Keyed by venue_id.
VENUE_CAPACITY: dict[str, dict[str, int]] = {
    "gym": {"optimal_count": 80, "max_capacity": 161},
    "pool": {"optimal_count": 50, "max_capacity": 130},
}

# --- Weather (Open-Meteo: free, no API key, has historical archive too) ---
# NTU Sports Center (綜合體育館), where the gym & pool are. Note: Open-Meteo
# snaps any coordinate to its model grid (~0.1°/~11km), so this precise point is
# symbolic — temperature/humidity at city scale are unaffected by the snap. For
# hyper-local rainfall, a CWA station would be the accurate source (future work).
NTU_LATITUDE = 25.0203
NTU_LONGITUDE = 121.5350
OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"
# WMO-standard current fields we log per cycle (lean but reusable later).
OPEN_METEO_CURRENT_FIELDS = (
    "temperature_2m,apparent_temperature,relative_humidity_2m,"
    "precipitation,weather_code,wind_speed_10m"
)

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Occupancy is long-format (one row per venue). Weather is NOT stored — it's
# shown live on the dashboard and can be backfilled from Open-Meteo's archive
# for training.
CSV_PATH = _DATA_DIR / "occupancy.csv"
