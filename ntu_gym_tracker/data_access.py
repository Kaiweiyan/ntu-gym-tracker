"""Read the collector's CSVs and compute the aggregates the web API serves.

The collector writes raw rows to `data/*.csv`; this module turns them into the
shapes the dashboard needs: latest reading, history time-series, and a
weekday*hour heatmap. We use pandas for the grouping/resampling.

Loading is cached on the file's modification time, so we only re-read the CSV
when the collector has actually appended new data (cheap per-request reads).
All timestamps are stored UTC; we convert to Asia/Taipei here because the
weekday/hour buckets only make sense in local time.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any

import pandas as pd

from .config import CSV_PATH

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
    """Latest reading per venue, with occupancy % and 'vs typical' busyness."""
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
        ratio = (count / typical) if typical else None
        out.append(
            {
                "venue_id": row["venue_id"],
                "venue_name": row["venue_name"],
                "current_count": count,
                "optimal_count": _int_or_none(row["optimal_count"]),
                "max_capacity": _int_or_none(row["max_capacity"]),
                "occupancy_pct": _pct(count, row["max_capacity"]),
                "typical_count": typical,
                "busyness": _busyness(ratio),
                "scraped_at": row["scraped_at"].isoformat(),
                "local_time": row["local"].strftime("%Y-%m-%d %H:%M"),
            }
        )
    return out


def get_history(venue_id: str, days: int = 7, granularity: str = "hour") -> dict:
    """Resampled mean occupancy over the last `days` days for one venue.

    Closed-hour buckets (which resample produces as NaN) are dropped, so the
    chart's x-axis is a continuous run of OPEN slots with no nightly gaps. We
    also return `day_boundaries` (indices where a new calendar day starts) and a
    parallel `day_labels` (e.g. "06-24 (三)") so the frontend can draw a dashed
    separator and a weekday-tagged date label per day.
    """
    empty = {"labels": [], "counts": [], "day_boundaries": [], "day_labels": []}
    df = _occupancy_ok()
    if df.empty:
        return empty
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    df = df.loc[(df["venue_id"] == venue_id) & (df["scraped_at"] >= cutoff)]
    if df.empty:
        return empty

    rule = "D" if granularity == "day" else "h"
    series = df.set_index("local")["current_count"].resample(rule).mean().dropna()
    resampled = series.reset_index()
    resampled.columns = ["ts", "count"]

    labels: list[str] = []
    counts: list[float] = []
    day_boundaries: list[int] = []
    day_labels: list[str] = []
    prev_day = None
    for ts, val in zip(resampled["ts"].tolist(), resampled["count"].tolist()):
        day = ts.strftime("%Y-%m-%d")
        if day != prev_day:
            day_boundaries.append(len(labels))
            day_labels.append(f"{ts.strftime('%m-%d')} ({WEEKDAY_ZH[ts.weekday()]})")
            prev_day = day
        labels.append(ts.strftime("%m-%d %H:%M"))
        counts.append(round(float(val), 1))
    return {
        "labels": labels,
        "counts": counts,
        "day_boundaries": day_boundaries,
        "day_labels": day_labels,
    }


def get_profile(venue_id: str, days: int = 7) -> dict:
    """Average 'typical day': mean occupancy per 10-min time-of-day slot.

    Buckets every reading in the last `days` days to its 10-min slot of the day
    (08:00, 08:10, ...) and averages across days, so the chart shows one smooth
    daily curve aligned to the collector's 10-min cadence.
    """
    empty = {"slots": [], "counts": []}
    df = _occupancy_ok()
    if df.empty:
        return empty
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)
    df = df.loc[(df["venue_id"] == venue_id) & (df["scraped_at"] >= cutoff)].copy()
    if df.empty:
        return empty
    # Time-of-day slot label, floored to 10 minutes (e.g. 13:20).
    df["slot"] = df["local"].dt.floor("10min").dt.strftime("%H:%M")
    prof = df.groupby("slot")["current_count"].mean().reset_index()
    return {
        "slots": prof["slot"].tolist(),
        "counts": [round(float(c), 1) for c in prof["current_count"].tolist()],
    }


def get_heatmap(venue_id: str) -> dict:
    """Mean occupancy per (weekday, hour) cell for one venue, for an ECharts heatmap."""
    empty = {"data": [], "hours": [], "weekdays": WEEKDAY_LABELS, "max": 0}
    df = _occupancy_ok()
    if df.empty:
        return empty
    df = df.loc[df["venue_id"] == venue_id]
    if df.empty:
        return empty
    grouped = df.groupby(["weekday", "hour"])["current_count"].mean().reset_index()
    # ECharts heatmap wants [x_index, y_index, value] = [hour, weekday, value].
    data = [
        [int(h), int(wd), round(float(c), 1)]
        for wd, h, c in zip(
            grouped["weekday"], grouped["hour"], grouped["current_count"]
        )
    ]
    hours = sorted({int(h) for h in grouped["hour"]})
    return {
        "data": data,
        "hours": [f"{h:02d}" for h in hours],
        "weekdays": WEEKDAY_LABELS,
        "max": _to_float(df["current_count"].max()),
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


# --- small helpers ---------------------------------------------------------
# Params are intentionally untyped: pandas scalars come through as Any, which
# keeps the type checker quiet while these guard the None/format conversions.

def _int_or_none(v) -> int | None:
    return int(v) if pd.notna(v) else None


def _to_float(v) -> float:
    return round(float(v), 1)


def _mean(series) -> float | None:
    return round(float(series.mean()), 1) if not series.empty else None


def _pct(count, capacity) -> int | None:
    if pd.isna(capacity) or not capacity:
        return None
    return round(100 * count / capacity)


def _busyness(ratio: float | None) -> str:
    """Coarse 'vs typical' label used to colour the current-occupancy card."""
    if ratio is None:
        return "unknown"
    if ratio < 0.7:
        return "quiet"
    if ratio <= 1.3:
        return "normal"
    return "busy"
