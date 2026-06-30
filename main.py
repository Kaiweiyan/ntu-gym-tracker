"""Run one scrape cycle: check hours -> fetch -> parse -> append CSV -> report.

`run_once()` performs a single cycle and is reused both by this CLI
(`uv run main.py`, e.g. from cron) and by `collector.py` (the always-on
workstation loop). It skips entirely while the venues are closed so we never
store a misleading 0 for a closed venue, and never raises so a scheduler/loop is
not killed by an expected upstream outage.
"""

from ntu_gym_tracker.hours import is_closing_tick, is_open, is_opening_tick, now_taipei
from ntu_gym_tracker.scraper import scrape, utc_now_iso, zero_observations
from ntu_gym_tracker.storage import append_observations


def run_once() -> None:
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


if __name__ == "__main__":
    run_once()
