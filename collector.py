"""Occupancy collector: scrape NTU gym/pool and append to data/occupancy.csv.

Two modes:

    uv run collector.py            # always-on loop (default), for a 24/7 host
    uv run collector.py --once     # a single cycle then exit, for cron (*/10)

`run_once()` skips closed hours (recording one count=0 marker at the open/close
ticks) and never raises, so a scheduler/loop is not killed by an upstream outage.
The loop runs one cycle every INTERVAL_MIN minutes, aligned to the wall clock
(:00, :10, :20, ...), wrapping each cycle so a single failure never kills it.

Run the loop under tmux / nohup / systemd so it survives logout; stop with Ctrl-C.
"""

from __future__ import annotations

import argparse
import time
import traceback
from datetime import datetime, timezone

from ntu_gym_tracker.hours import is_closing_tick, is_open, is_opening_tick, now_taipei
from ntu_gym_tracker.scraper import scrape, utc_now_iso, zero_observations
from ntu_gym_tracker.storage import append_observations

INTERVAL_MIN = 10


def run_once() -> None:
    """One collection cycle: check hours -> scrape/mark -> append CSV -> report."""
    now = now_taipei()
    scraped_at = utc_now_iso()

    if not is_open(now):
        # At the exact closing tick, record a single count=0 row per venue so the
        # curve returns to 0; otherwise we're fully closed, so skip.
        if is_closing_tick(now):
            written = append_observations(zero_observations(scraped_at, "closed"))
            print(f"closing at {now:%Y-%m-%d %H:%M %Z} — recorded {written} zero row(s)")
        else:
            print(f"closed at {now:%Y-%m-%d %H:%M %Z} — skipping scrape")
        return

    # Open. The first open tick is forced to 0 (the site can show a stale
    # non-zero right at open); every other tick scrapes the real value.
    if is_opening_tick(now):
        observations = zero_observations(scraped_at, "open")
        written = append_observations(observations)
        print(f"opening at {now:%Y-%m-%d %H:%M %Z} — recorded {written} zero row(s)")
    else:
        observations = scrape(scraped_at)
        written = append_observations(observations)
        print(f"appended {written} observation(s):")
        for o in observations:
            if o.source_status == "ok":
                print(
                    f"  [{o.scraped_at}] {o.venue_name} ({o.venue_id}): "
                    f"{o.current_count} now "
                    f"(optimal {o.optimal_count}, max {o.max_capacity})"
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
