"""Run one scrape cycle: check hours -> fetch -> parse -> append CSV -> report.

`run_once()` performs a single cycle and is reused both by this CLI
(`uv run main.py`, e.g. from cron) and by `collector_loop.py` (the always-on
workstation loop). It skips entirely while the venues are closed so we never
store a misleading 0 for a closed venue, and never raises so a scheduler/loop is
not killed by an expected upstream outage.
"""

from ntu_gym_tracker.hours import is_open, now_taipei
from ntu_gym_tracker.scraper import scrape, utc_now_iso
from ntu_gym_tracker.storage import append_observations, append_weather
from ntu_gym_tracker.weather import fetch_weather


def run_once() -> None:
    now = now_taipei()
    if not is_open(now):
        print(f"closed at {now:%Y-%m-%d %H:%M %Z} — skipping scrape")
        return

    # One timestamp shared by occupancy + weather so the two CSVs join exactly.
    scraped_at = utc_now_iso()

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

    weather = fetch_weather(scraped_at)
    append_weather(weather)
    if weather.source_status == "ok":
        print(
            f"weather: {weather.temperature_c}°C "
            f"(feels {weather.apparent_temperature_c}°C), "
            f"rain {weather.precipitation_mm}mm, code {weather.weather_code}"
        )
    else:
        print(f"weather: {weather.source_status}")


if __name__ == "__main__":
    run_once()
