"""Always-on collector loop for a 24/7 workstation.

Runs one scrape cycle every INTERVAL_MIN minutes, aligned to the wall clock
(:00, :10, :20, ...). `run_once()` already skips closed hours, so closed cycles
are cheap no-ops. Each cycle is wrapped in try/except so a single failure never
kills the loop.

Data persists directly to data/*.csv on the workstation's disk — no git-scraping
needed. Run this under tmux / nohup / systemd so it survives logout:

    uv run python collector_loop.py

Stop with Ctrl-C (or stop the systemd service).
"""

from __future__ import annotations

import time
import traceback
from datetime import datetime, timezone

from main import run_once

INTERVAL_MIN = 10


def seconds_until_next_slot(interval_min: int = INTERVAL_MIN) -> float:
    """Seconds from now until the next wall-clock slot boundary (UTC-based)."""
    now = datetime.now(timezone.utc)
    elapsed = (now.minute % interval_min) * 60 + now.second + now.microsecond / 1e6
    return interval_min * 60 - elapsed


def main() -> None:
    print(f"collector loop started (interval={INTERVAL_MIN} min); Ctrl-C to stop", flush=True)
    while True:
        try:
            run_once()
        except Exception:  # never let one bad cycle kill the loop
            print("cycle failed:\n" + traceback.format_exc(), flush=True)
        time.sleep(seconds_until_next_slot())


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\ncollector loop stopped", flush=True)
