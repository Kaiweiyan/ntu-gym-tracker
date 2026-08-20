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

# --- Forecast ---
# Baseline strategy for get_forecast()'s predicted curve. One of the keys in
# `forecast_model._STRATEGIES`:
#   "same_weekday_mean" — mean per 10-min slot across all historical days that
#                          share the target day's weekday (e.g. forecasting a
#                          Monday only averages over past Mondays).
#   "recent_mean"       — mean per 10-min slot across the last
#                         FORECAST_RECENT_DAYS days, regardless of weekday.
# To add a model-based strategy once there's enough data: write a function
# `(df, target_date) -> dict[str, float]` in forecast_model.py, register it
# in `_STRATEGIES`, and point this at its key — forecast_model.forecast()
# itself doesn't change.
FORECAST_METHOD = "same_weekday_mean"
FORECAST_RECENT_DAYS = 30  # window used by the "recent_mean" strategy

_DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Occupancy is long-format (one row per venue).
CSV_PATH = _DATA_DIR / "occupancy.csv"

# Manually-maintained calendar of whole-day closures (holidays, typhoon days,
# maintenance, ...) — see closures.py. Hand-edit directly: `date, end_date,
# reason`, one row per closure range (`end_date` blank means a single day).
CLOSURES_PATH = _DATA_DIR / "closures.csv"
