"""Read the collector's CSVs and compute the aggregates the web API serves.

The collector writes raw rows to `data/*.csv`; this module turns them into the
shapes the dashboard needs: latest reading, history time-series, a
weekday*hour heatmap, and an average-day profile. We use pandas for the
grouping/resampling. The forecast itself (baselines, gap-fill, a future ML
model) lives in `forecast_model.py`; `get_forecast()` here is just the thin
data-layer wrapper that resolves the venue's readings and hands them off.

Loading is cached on the file's modification time, so we only re-read the CSV
when the collector has actually appended new data (cheap per-request reads).
All timestamps are stored UTC; we convert to Asia/Taipei here because the
weekday/hour buckets only make sense in local time.

Layout: cache/load helpers, then the public functions (in the same order as
their routes in `app.py`), then all private helpers grouped at the bottom.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from . import forecast_model
from .config import CSV_PATH, VENUE_CAPACITY

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
    means = forecast_model.slot_means(df)
    return {
        "slots": list(means.keys()),
        "counts": list(means.values()),
    }


def get_forecast(venue_id: str, day: str = "today") -> dict:
    """Occupancy forecast for `day` ('today' | 'tomorrow') for one venue.

    Resolves this venue's readings, then delegates the actual forecast
    assembly to `forecast_model.forecast()`.
    """
    df = _occupancy_ok()
    df = df.loc[df["venue_id"] == venue_id] if not df.empty else df
    if df.empty:
        return forecast_model.empty_forecast()

    return forecast_model.forecast(df, day=day)


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
