"""Read the collector's CSVs and compute the aggregates the web API serves.

The collector writes raw rows to `data/*.csv`; this module turns them into the
shapes the dashboard needs: latest reading, history time-series, a
weekday*hour heatmap, an average-day profile, and a baseline forecast. We use
pandas for the grouping/resampling.

Loading is cached on the file's modification time, so we only re-read the CSV
when the collector has actually appended new data (cheap per-request reads).
All timestamps are stored UTC; we convert to Asia/Taipei here because the
weekday/hour buckets only make sense in local time.

Layout: cache/load helpers, then the public functions (in the same order as
their routes in `app.py`), then all private helpers grouped at the bottom.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from .config import CSV_PATH, FORECAST_METHOD, FORECAST_RECENT_DAYS, VENUE_CAPACITY
from .hours import SLOT_MINUTES, now_taipei, open_close

TAIPEI = "Asia/Taipei"  # IANA timezone name
WEEKDAY_LABELS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAY_ZH = ["一", "二", "三", "四", "五", "六", "日"]  # Mon=0 .. Sun=6

# mtime-keyed cache: {path: (mtime, DataFrame)}
_cache: dict[str, tuple[float, pd.DataFrame]] = {}


def _load_csv(path) -> pd.DataFrame:
    """Read a CSV, cached until the file changes on disk."""
    if not path.exists():
        return pd.DataFrame()
    mtime = path.stat().st_mtime
    cached = _cache.get(str(path))
    if cached and cached[0] == mtime:
        return cached[1]
    df = pd.read_csv(path)
    if not df.empty and "scraped_at" in df.columns:
        # format="ISO8601" tolerates mixed precision (with/without microseconds).
        df["scraped_at"] = pd.to_datetime(df["scraped_at"], utc=True, format="ISO8601")
        local = df["scraped_at"].dt.tz_convert(TAIPEI)
        df["weekday"] = local.dt.weekday  # Mon=0 .. Sun=6
        df["hour"] = local.dt.hour
        df["local"] = local
    _cache[str(path)] = (mtime, df)
    return df


def _occupancy_ok() -> pd.DataFrame:
    """Occupancy rows that carry a real count.

    Keeps "ok" readings and the count=0 "closed" markers (both have a non-null
    count); drops fetch/parse-error rows (null count).
    """
    df = _load_csv(CSV_PATH)
    if df.empty:
        return df
    return df.dropna(subset=["current_count"])


def list_venues() -> list[dict]:
    """Distinct venues seen in the data, e.g. [{'id': 'gym', 'name': '健身中心'}]."""
    df = _occupancy_ok()
    if df.empty:
        return []
    pairs = df[["venue_id", "venue_name"]].drop_duplicates()
    return [
        {"id": vid, "name": vname}
        for vid, vname in zip(pairs["venue_id"], pairs["venue_name"])
    ]


def get_current() -> list[dict]:
    """Latest reading per venue, with occupancy % and an absolute busyness level
    (vs. the venue's own optimal_count, not the historical average for this
    time slot — see `_busyness`). `typical_count` (same weekday+hour historical
    mean) is still returned as informational context.
    """
    df = _occupancy_ok()
    if df.empty:
        return []

    out = []
    for _, group in df.groupby("venue_id"):
        # Latest row of this venue as a plain dict (values are Any -> no pandas
        # "Scalar" typing noise on the arithmetic / int() below).
        row: Any = group.sort_values("scraped_at").iloc[-1].to_dict()
        # Typical count at this weekday+hour across all history for this venue.
        same_slot = df.loc[
            (df["venue_id"] == row["venue_id"])
            & (df["weekday"] == row["weekday"])
            & (df["hour"] == row["hour"]),
            "current_count",
        ]
        typical = _mean(same_slot)
        count = int(row["current_count"])
        cap = VENUE_CAPACITY.get(row["venue_id"], {})
        out.append(
            {
                "venue_id": row["venue_id"],
                "venue_name": row["venue_name"],
                "current_count": count,
                "optimal_count": cap.get("optimal_count"),
                "max_capacity": cap.get("max_capacity"),
                "occupancy_pct": _pct(count, cap.get("max_capacity")),
                "typical_count": typical,
                "busyness": _busyness(count, cap.get("optimal_count")),
                "scraped_at": row["scraped_at"].isoformat(),
                "local_time": row["local"].strftime("%Y-%m-%d %H:%M"),
            }
        )
    return out


def get_heatmap(venue_id: str) -> dict:
    """Mean occupancy per (weekday, hour) cell for one venue, for an ECharts heatmap.

    Only "ok" (real scrape) rows count — the opening/closing boundary
    count=0 markers (`source_status` "open"/"closed") are excluded, unlike
    other charts that deliberately keep them to make a *line* return to 0.
    A heatmap has no such need, and keeping them would permanently drag the
    opening hour's average down and add a fake, always-empty closing-hour
    column (real scrapes never land exactly on the closing tick).
    """
    empty = {"data": [], "hours": [], "weekdays": WEEKDAY_LABELS, "max": 0}
    df = _occupancy_ok()
    if df.empty:
        return empty
    df = df.loc[(df["venue_id"] == venue_id) & (df["source_status"] == "ok")]
    if df.empty:
        return empty
    grouped = df.groupby(["weekday", "hour"])["current_count"].mean().reset_index()
    # ECharts wants [x_index, y_index, value] — an *index* into `hours`, not the
    # raw hour number (opening hour is 8, not 0, so the two only coincide by
    # accident and every cell used to land 8 slots to the right of its label).
    hours = sorted({int(h) for h in grouped["hour"]})
    hour_index = {h: i for i, h in enumerate(hours)}
    data = [
        [hour_index[int(h)], int(wd), round(float(c), 1)]
        for wd, h, c in zip(
            grouped["weekday"], grouped["hour"], grouped["current_count"]
        )
    ]
    return {
        "data": data,
        "hours": [f"{h:02d}" for h in hours],
        "weekdays": WEEKDAY_LABELS,
        "max": _to_float(df["current_count"].max()),
    }


def get_profile(venue_id: str, days: int = 7) -> dict:
    """Average 'typical day': mean occupancy per 10-min time-of-day slot.

    Buckets every reading in the last `days` days to its 10-min slot of the day
    (08:00, 08:10, ...) and averages across days, so the chart shows one smooth
    daily curve aligned to the collector's 10-min cadence.
    """
    empty = {"slots": [], "counts": []}
    df = _for_venue_since(_occupancy_ok(), venue_id, days)
    if df.empty:
        return empty
    slot_means = _slot_means(df)
    return {
        "slots": list(slot_means.keys()),
        "counts": list(slot_means.values()),
    }


def get_forecast(venue_id: str, day: str = "today") -> dict:
    """Baseline occupancy forecast for `day` ('today' | 'tomorrow'), 10-min slots.

    Baseline = `config.FORECAST_METHOD` strategy (see `_FORECAST_STRATEGIES`),
    swappable for a model later without any frontend change. `forecast` covers
    the *whole* day (open→close), including
    the already-elapsed part, so the dashed baseline sits next to the solid
    `actual` line wherever both exist — that overlap is what lets a user judge
    how accurate the forecast has been today, not just what it predicts ahead.
    `actual` holds real readings up to now (short gaps from a missed scrape are
    interpolated, see `_fill_short_gaps`); for tomorrow it's empty.
    """
    empty = {"slots": [], "actual": [], "forecast": [], "now_slot": None}
    df = _occupancy_ok()
    if df.empty:
        return empty
    df = df.loc[df["venue_id"] == venue_id]
    if df.empty:
        return empty

    now = now_taipei()
    target_date = now.date() if day == "today" else now.date() + timedelta(days=1)
    slots = _day_slots(*open_close(target_date.weekday()))

    baseline = _forecast_baseline(df, target_date)
    now_slot = f"{now.hour:02d}:{now.minute // SLOT_MINUTES * SLOT_MINUTES:02d}"
    actual_by_slot = (
        _slot_means(df.loc[df["local"].dt.date == target_date]) if day == "today" else {}
    )

    actual: list[float | None] = []
    forecast: list[float | None] = []
    for s in slots:
        a = actual_by_slot.get(s)
        past_or_now = day == "today" and s <= now_slot
        actual.append(a if (past_or_now and a is not None) else None)
        forecast.append(baseline.get(s))

    return {
        "slots": slots,
        "actual": _round_ints(_fill_short_gaps(actual)),
        "forecast": _round_ints(forecast),
        "now_slot": now_slot if day == "today" else None,
    }


# Weather is shown live (not stored in data/), with a short server-side cache so
# many page refreshes don't each hit Open-Meteo (which updates ~every 15 min).
_WEATHER_TTL_SECONDS = 600
_weather_cache: dict[str, Any] = {"at": 0.0, "value": None}


def get_current_weather() -> dict | None:
    """Live weather for the dashboard header (cached ~10 min). Never stored."""
    now = time.time()
    if now - _weather_cache["at"] < _WEATHER_TTL_SECONDS and _weather_cache["value"]:
        return _weather_cache["value"]

    from .weather import fetch_weather  # local import avoids a startup dependency

    w = fetch_weather()
    if w.source_status != "ok":
        return _weather_cache["value"]  # serve stale (or None) on a failed fetch

    value = {
        "temperature_c": w.temperature_c,
        "apparent_temperature_c": w.apparent_temperature_c,
        "precipitation_mm": w.precipitation_mm,
        "weather_code": w.weather_code,
        "local_time": (w.observed_at or "").replace("T", " "),
    }
    _weather_cache.update(at=now, value=value)
    return value


# --- private helpers ---------------------------------------------------------
# Params are intentionally untyped in a few spots: pandas scalars come through
# as Any, which keeps the type checker quiet while these guard the None/format
# conversions.


def _for_venue_since(df: pd.DataFrame, venue_id: str, days: int) -> pd.DataFrame:
    """Rows for one venue from the last `days` days (used by history/profile)."""
    if df.empty:
        return df
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    return df.loc[(df["venue_id"] == venue_id) & (df["scraped_at"] >= cutoff)]


def _day_slots(open_t, close_t) -> list[str]:
    """10-min slot labels 'HH:MM' from open to close inclusive."""
    start = open_t.hour * 60 + open_t.minute
    end = close_t.hour * 60 + close_t.minute
    return [f"{m // 60:02d}:{m % 60:02d}" for m in range(start, end + 1, SLOT_MINUTES)]


def _slot_means(df: pd.DataFrame) -> dict[str, float]:
    """Mean current_count per 10-min time-of-day slot, over the given rows."""
    if df.empty:
        return {}
    tmp = df.copy()
    tmp["slot"] = tmp["local"].dt.floor("10min").dt.strftime("%H:%M")
    g = tmp.groupby("slot")["current_count"].mean().reset_index()
    return {s: round(float(c), 1) for s, c in zip(g["slot"].tolist(), g["current_count"].tolist())}


def _baseline_same_weekday_mean(df: pd.DataFrame, target_date) -> dict[str, float]:
    """Mean per slot over all historical days sharing `target_date`'s weekday."""
    return _slot_means(df.loc[df["weekday"] == target_date.weekday()])


def _baseline_recent_mean(df: pd.DataFrame, target_date) -> dict[str, float]:
    """Mean per slot over the last `FORECAST_RECENT_DAYS` days, any weekday."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=FORECAST_RECENT_DAYS)
    return _slot_means(df.loc[df["scraped_at"] >= cutoff])


# Registry of forecast baseline strategies, selected by config.FORECAST_METHOD.
# Add a new "(df, target_date) -> {slot: mean}" function and register it here
# to plug in a model later — get_forecast() itself doesn't need to change.
_FORECAST_STRATEGIES = {
    "same_weekday_mean": _baseline_same_weekday_mean,
    "recent_mean": _baseline_recent_mean,
}


def _forecast_baseline(df: pd.DataFrame, target_date) -> dict[str, float]:
    try:
        strategy = _FORECAST_STRATEGIES[FORECAST_METHOD]
    except KeyError:
        raise ValueError(
            f"config.FORECAST_METHOD={FORECAST_METHOD!r} is not a known "
            f"strategy; expected one of {list(_FORECAST_STRATEGIES)}"
        ) from None
    return strategy(df, target_date)


# A single missed scrape (fetch/parse failure) leaves an isolated None in
# get_forecast's `actual`; bridge runs up to this many consecutive slots by
# interpolating between the real readings on either side. Longer runs are a
# real outage (site down, closed venue, ...) and are left as None rather than
# papered over.
MAX_INTERP_GAP_SLOTS = 2


def _fill_short_gaps(values: list[float | None]) -> list[float | None]:
    """Linearly interpolate short runs of None that have real neighbors on both
    sides. Leading/trailing None (no data yet, or future slots) is untouched
    since there's no neighbor on one side to interpolate from.
    """
    filled = list(values)
    i, n = 0, len(filled)
    while i < n:
        if filled[i] is not None:
            i += 1
            continue
        j = i
        while j < n and filled[j] is None:
            j += 1
        gap_len = j - i
        if i > 0 and j < n and gap_len <= MAX_INTERP_GAP_SLOTS:
            left, right = filled[i - 1], filled[j]
            for k in range(gap_len):
                filled[i + k] = round(left + (right - left) * (k + 1) / (gap_len + 1), 1)
        i = j
    return filled


def _round_ints(values: list[float | None]) -> list[int | None]:
    """Round a list of people-counts to whole numbers for display."""
    return [round(v) if v is not None else None for v in values]


def _to_float(v) -> float:
    return round(float(v), 1)


def _mean(series) -> float | None:
    return round(float(series.mean()), 1) if not series.empty else None


def _pct(count, capacity) -> int | None:
    if pd.isna(capacity) or not capacity:
        return None
    return round(100 * count / capacity)


def _busyness(count: int, optimal_count: int | None) -> str:
    """Absolute crowding level vs. the venue's own fixed 'optimal' (comfortable)
    count — not a comparison to the historical average for this time slot, so
    two venues/slots with different typical loads aren't both labelled
    "normal" just because each is near its own average.
    """
    if not optimal_count:
        return "unknown"
    if count < 0.5 * optimal_count:
        return "quiet"
    if count <= optimal_count:
        return "normal"
    return "busy"
