"""Occupancy collector: scrape NTU gym/pool and append to data/occupancy.csv.

Two modes:

    uv run collector.py            # always-on loop (default), for a 24/7 host
    uv run collector.py --once     # a single cycle then exit, for cron (*/10)

`run_once()` skips closed hours (recording one count=0 marker at the open/close
ticks) and never raises, so a scheduler/loop is not killed by an upstream outage.
It also checks `closures.py`'s manually-maintained calendar first, so a
declared closure (holiday, typhoon, maintenance) skips that venue for the day,
regardless of what the hours table says — gym and pool are checked
independently, so one can be closed while the other keeps its normal schedule.
The loop runs one cycle every INTERVAL_MIN minutes, aligned to the wall clock
(:00, :10, :20, ...), wrapping each cycle so a single failure never kills it.

Run the loop under tmux / nohup / systemd so it survives logout; stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import time
import traceback
from datetime import datetime, timezone

from ntu_gym_tracker.closures import closures_on
from ntu_gym_tracker.config import VENUE_CAPACITY, VENUE_ID_BY_NAME
from ntu_gym_tracker.hours import is_closing_tick, is_open, is_opening_tick, now_taipei
from ntu_gym_tracker.scraper import closed_observation, scrape, utc_now_iso, zero_observation
from ntu_gym_tracker.storage import append_observations

INTERVAL_MIN = 10


def run_once() -> None:
    """One collection cycle: check the closure calendar -> check hours ->
    scrape/mark -> append CSV -> report."""
    now = now_taipei()
    scraped_at = utc_now_iso()
    closures = closures_on(now.date())  # {venue_id: Closure}, independent per venue

    if not is_open(now):
        # At the exact closing tick, record a count=0 row per venue that was
        # actually open today so its curve returns to 0; a venue under
        # manual closure never had a session, so it gets no closing marker.
        # Otherwise we're fully closed, so skip.
        if is_closing_tick(now):
            observations = [
                zero_observation(vid, name, scraped_at, "closed")
                for name, vid in VENUE_ID_BY_NAME.items()
                if vid not in closures
            ]
            if observations:
                written = append_observations(observations)
                print(f"closing at {now:%Y-%m-%d %H:%M %Z} — recorded {written} zero row(s)")
            else:
                print(f"closing at {now:%Y-%m-%d %H:%M %Z} — every venue under manual closure today")
        else:
            print(f"closed at {now:%Y-%m-%d %H:%M %Z} — skipping scrape")
        return

    # Open. The first open tick is forced to 0 for a venue that's actually
    # open (the site can show a stale non-zero right at open) or a
    # venue_closed marker for one under manual closure; every other tick
    # scrapes the real value for whichever venues are open.
    if is_opening_tick(now):
        observations = [
            closed_observation(vid, name, scraped_at, closures[vid].reason)
            if vid in closures
            else zero_observation(vid, name, scraped_at, "open")
            for name, vid in VENUE_ID_BY_NAME.items()
        ]
        written = append_observations(observations)
        print(f"opening at {now:%Y-%m-%d %H:%M %Z} — recorded {written} row(s)")
    elif len(closures) == len(VENUE_ID_BY_NAME):
        print(f"all venues under manual closure — skipping scrape")
    else:
        # A single page fetch returns every venue at once, so we still hit
        # the site even if only some venues are open — then drop whichever
        # venues are under manual closure (they already got their one-time
        # marker at the opening tick, and shouldn't get a real reading now
        # even if the page happens to show one).
        observations = [o for o in scrape(scraped_at) if o.venue_id not in closures]
        written = append_observations(observations)
        print(f"appended {written} observation(s):")
        for o in observations:
            if o.source_status == "ok":
                cap = VENUE_CAPACITY.get(o.venue_id, {})
                print(
                    f"  [{o.scraped_at}] {o.venue_name} ({o.venue_id}): "
                    f"{o.current_count} now "
                    f"(optimal {cap.get('optimal_count')}, max {cap.get('max_capacity')})"
                )
            else:
                print(f"  [{o.scraped_at}] {o.venue_id}: {o.source_status}")


def seconds_until_next_slot(interval_min: int = INTERVAL_MIN) -> float:
    """Seconds from now until the next wall-clock slot boundary (UTC-based)."""
    now = datetime.now(timezone.utc)
    elapsed = (now.minute % interval_min) * 60 + now.second + now.microsecond / 1e6
    return interval_min * 60 - elapsed


def loop() -> None:
    print(f"collector loop started (interval={INTERVAL_MIN} min); Ctrl-C to stop", flush=True)
    while True:
        try:
            run_once()
        except Exception:  # never let one bad cycle kill the loop
            print("cycle failed:\n" + traceback.format_exc(), flush=True)
        time.sleep(seconds_until_next_slot())


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--once", action="store_true", help="run a single cycle then exit (for cron)"
    )
    args = parser.parse_args()
    if args.once:
        run_once()
    else:
        try:
            loop()
        except KeyboardInterrupt:
            print("\ncollector loop stopped", flush=True)


if __name__ == "__main__":
    main()
