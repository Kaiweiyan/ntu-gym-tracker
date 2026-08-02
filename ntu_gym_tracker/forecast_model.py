"""Occupancy forecast: baseline strategies today, a learned model later.

Takes plain DataFrames prepared by `data_access` (already filtered to one
venue; the historical one has suspected ad-hoc closure days excluded) — this
module owns no CSV/loading logic of its own, so it stays testable and
swappable independent of the data layer. `forecast()` is the entry point
`data_access.get_forecast()` delegates to; `slot_means()` is also reused by
`data_access.get_profile()`'s (non-forecast) average-day chart.

Adding a model-based strategy later: write a `(df, target_date) -> {slot:
mean}` function and register it in `_STRATEGIES` — `forecast()` itself
doesn't need to change.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd

from .config import FORECAST_METHOD, FORECAST_RECENT_DAYS
from .hours import SLOT_MINUTES, now_taipei, open_close


def empty_forecast() -> dict:
    return {"slots": [], "actual": [], "forecast": [], "now_slot": None}


def forecast(df: pd.DataFrame, hist: pd.DataFrame, day: str = "today") -> dict:
    """Baseline occupancy forecast for `day` ('today' | 'tomorrow'), 10-min slots.

    `df` is one venue's raw "ok" readings (unfiltered) — used for `actual`, so
    a suspected-closure day still shows what was actually recorded. `hist` is
    the same venue's readings with suspected ad-hoc closure days excluded —
    used for the baseline, so a typhoon day doesn't drag down the average for
    that weekday/slot (see `data_access._historical_ok`).

    Baseline = `config.FORECAST_METHOD` strategy (see `_STRATEGIES`). The
    forecast line covers the *whole* day (open→close), including the
    already-elapsed part, so the dashed baseline sits next to the solid
    `actual` line wherever both exist — that overlap is what lets a user
    judge how accurate the forecast has been today, not just what it
    predicts ahead. `actual` holds real readings up to now (short gaps from a
    missed scrape are interpolated, see `_fill_short_gaps`); for tomorrow
    it's empty.
    """
    if df.empty:
        return empty_forecast()

    now = now_taipei()
    target_date = now.date() if day == "today" else now.date() + timedelta(days=1)
    slots = _day_slots(*open_close(target_date.weekday()))

    baseline = _baseline(hist, target_date)
    now_slot = f"{now.hour:02d}:{now.minute // SLOT_MINUTES * SLOT_MINUTES:02d}"
    actual_by_slot = (
        slot_means(df.loc[df["local"].dt.date == target_date]) if day == "today" else {}
    )

    actual: list[float | None] = []
    fc: list[float | None] = []
    for s in slots:
        a = actual_by_slot.get(s)
        past_or_now = day == "today" and s <= now_slot
        actual.append(a if (past_or_now and a is not None) else None)
        fc.append(baseline.get(s))

    return {
        "slots": slots,
        "actual": _round_ints(_fill_short_gaps(actual)),
        "forecast": _round_ints(fc),
        "now_slot": now_slot if day == "today" else None,
    }


def slot_means(df: pd.DataFrame) -> dict[str, float]:
    """Mean current_count per 10-min time-of-day slot, over the given rows.

    Shared by the baselines below and `data_access.get_profile()`'s
    (non-forecast) average-day chart — both are "bucket by time-of-day, then
    average across days" over a different slice of rows.
    """
    if df.empty:
        return {}
    tmp = df.copy()
    tmp["slot"] = tmp["local"].dt.floor("10min").dt.strftime("%H:%M")
    g = tmp.groupby("slot")["current_count"].mean().reset_index()
    return {s: round(float(c), 1) for s, c in zip(g["slot"].tolist(), g["current_count"].tolist())}


def _day_slots(open_t, close_t) -> list[str]:
    """10-min slot labels 'HH:MM' from open to close inclusive."""
    start = open_t.hour * 60 + open_t.minute
    end = close_t.hour * 60 + close_t.minute
    return [f"{m // 60:02d}:{m % 60:02d}" for m in range(start, end + 1, SLOT_MINUTES)]


def _baseline_same_weekday_mean(df: pd.DataFrame, target_date) -> dict[str, float]:
    """Mean per slot over all historical days sharing `target_date`'s weekday."""
    return slot_means(df.loc[df["weekday"] == target_date.weekday()])


def _baseline_recent_mean(df: pd.DataFrame, target_date) -> dict[str, float]:
    """Mean per slot over the last `FORECAST_RECENT_DAYS` days, any weekday."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=FORECAST_RECENT_DAYS)
    return slot_means(df.loc[df["scraped_at"] >= cutoff])


# Registry of forecast baseline strategies, selected by config.FORECAST_METHOD.
_STRATEGIES = {
    "same_weekday_mean": _baseline_same_weekday_mean,
    "recent_mean": _baseline_recent_mean,
}


def _baseline(df: pd.DataFrame, target_date) -> dict[str, float]:
    try:
        strategy = _STRATEGIES[FORECAST_METHOD]
    except KeyError:
        raise ValueError(
            f"config.FORECAST_METHOD={FORECAST_METHOD!r} is not a known "
            f"strategy; expected one of {list(_STRATEGIES)}"
        ) from None
    return strategy(df, target_date)


# A single missed scrape (fetch/parse failure) leaves an isolated None in
# `actual`; bridge runs up to this many consecutive slots by interpolating
# between the real readings on either side. Longer runs are a real outage
# (site down, closed venue, ...) and are left as None rather than papered
# over.
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
            assert left is not None and right is not None
            for k in range(gap_len):
                filled[i + k] = round(left + (right - left) * (k + 1) / (gap_len + 1), 1)
        i = j
    return filled


def _round_ints(values: list[float | None]) -> list[int | None]:
    """Round a list of people-counts to whole numbers for display."""
    return [round(v) if v is not None else None for v in values]
