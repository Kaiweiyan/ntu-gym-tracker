# NTU Gym Tracker

Collect, visualize, and (soon) forecast live occupancy of National Taiwan
University's gym (健身中心) and indoor pool (室內游泳池).

A lightweight collector scrapes the official occupancy page every 10 minutes and
appends to a CSV; a FastAPI app serves both a JSON API and a server-rendered
dashboard with live counts, an occupancy forecast, a weekday × hour heatmap, and
an average-day profile.

## How it works

```
┌──────────────────────────────────┐
│ collector (always-on, 24/7)      │
└──────────────────────────────────┘
                 │
                 │  scrape + parse, every 10 min
                 ▼
┌──────────────────────────────────┐
│ data/occupancy.csv (append-only) │
└──────────────────────────────────┘
                 │
                 │  read with pandas (cached)
                 ▼
┌──────────────────────────────────┐
│ FastAPI — JSON API + dashboard   │
│ (Jinja2 · ECharts · HTMX)        │
└──────────────────────────────────┘
```

The occupancy numbers are server-rendered in the page's HTML, so a plain HTTP GET
(no headless browser) is enough.

## Features

- **Live occupancy** per venue with an absolute busyness level (vs. the
  venue's own optimal_count), auto-refreshing via HTMX.
- **Occupancy forecast**: today/tomorrow, actual readings meeting a baseline
  forecast line (configurable strategy, see `config.FORECAST_METHOD`).
- **Average-day profile**: mean occupancy per 10-minute slot over the last N days.
- **Weekday × hour heatmap** of average occupancy.
- **Opening-hours aware** collection: a single `count=0` marker is recorded at the
  open and close ticks (kept distinct via `source_status` so it can be excluded
  from training); failed fetches are recorded as rows, never as a misleading `0`.
- **Manually declared closures** (holidays, typhoon days, maintenance), per
  venue: a hand-maintained calendar (`data/closures.csv`) the collector
  checks before every scrape, so a declared venue is never scraped that day
  — independent of the other venue, which keeps its normal schedule. The
  reason (and date range, if multi-day) shows right on that venue's card.

## Tech stack

| Area       | Tools                                              |
| ---------- | -------------------------------------------------- |
| Runtime    | Python 3.12, managed with [uv](https://docs.astral.sh/uv/) |
| Scraping   | `httpx`, `beautifulsoup4`                          |
| Data       | `pandas`, CSV (long format)                        |
| Web / API  | `FastAPI`, `uvicorn`, `Jinja2`                     |
| Charts     | Apache ECharts + HTMX (via CDN)                    |

## Getting started

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/getting-started/installation/).

```bash
git clone https://github.com/Kaiweiyan/ntu-gym-tracker.git
cd ntu-gym-tracker
uv sync                      # create the venv and install dependencies
```

### Run the collector

```bash
uv run collector.py          # always-on loop, slot-aligned to :00/:10/:20…
uv run collector.py --once   # a single cycle (e.g. from cron, every 10 min)
```

For a 24/7 deployment use cron, a `systemd --user` service
(`scripts/ntu-gym-collector.service`), or tmux — see `spec.md`.

### Run the web app

```bash
uv run uvicorn app:app --reload                       # dev (http://localhost:8000)
uv run uvicorn app:app --host 0.0.0.0 --port 8000     # serve
```

## API

| Endpoint        | Description                                          |
| --------------- | ---------------------------------------------------- |
| `GET /api/venues`  | Known venues (`id`, `name`).                      |
| `GET /api/current` | Latest count per venue.                           |
| `GET /api/forecast` | Actual + forecast curve; params `venue`, `day`.  |
| `GET /api/profile` | Mean per 10-min slot; params `venue`, `days`.     |
| `GET /api/heatmap` | Weekday × hour average matrix; param `venue`.     |

## Manual CSV editing (`csv_tool.py`)

For quick manual fixes to `data/occupancy.csv` — correcting a bad reading, backfilling a
value you know from memory, purging a bad scrape run — `csv_tool.py` loads the CSV into an
in-memory SQLite table so you can query/edit it with real SQL, instead of hand-editing the
file. Nothing touches disk until you review a preview and confirm; the previous file is
always backed up first (`data/occupancy.csv.bak.<timestamp>`).

```bash
uv run csv_tool.py schema                             # column names/types
uv run csv_tool.py query "<a SELECT statement>"        # read-only, prints a table
uv run csv_tool.py exec "<one UPDATE/DELETE/INSERT>"   # previewed, then confirmed, before writing
```

Examples:

```bash
# List every fetch/parse failure so far
uv run csv_tool.py query "SELECT * FROM occupancy WHERE source_status LIKE '%error%'"

# Fix a row you know the real count for
uv run csv_tool.py exec "UPDATE occupancy SET current_count=45, source_status='ok'
    WHERE scraped_at='2026-07-01T12:10:00+00:00' AND venue_id='_parse'"

# Delete every row for one day (e.g. purging a bad scrape run)
uv run csv_tool.py exec "DELETE FROM occupancy WHERE scraped_at LIKE '2026-07-10%'"
```

`scraped_at` is stored in UTC, not Taipei local time — but since the venues never open
before 08:00 Taipei (= 00:00 UTC, see `hours.py`), a plain `LIKE '<date>%'` prefix still
lines up exactly with the intended Taipei calendar day.

`exec` only accepts one statement at a time (no `;`-separated batches), refuses to write if
the file changed on disk since it was loaded (the collector may have appended a row while
you were reading the preview), and always makes a backup before touching the real file — so
a bad edit is a `mv occupancy.csv.bak.<timestamp> occupancy.csv` away from undone.

## Declaring a closure (`data/closures.csv`)

Gym and pool are independent venues with independent schedules, so closures are declared
per venue. For a holiday, typhoon day, or maintenance closure — known in advance or
announced same-day — add a row to `data/closures.csv` by hand:

```csv
venue_id,date,end_date,reason
gym,2026-07-10,2026-07-11,颱風假
pool,2026-09-01,,單日清潔
,2026-12-25,,館內全面消毒
```

`venue_id` is `gym` or `pool` for a closure specific to that venue, or left blank to close
*every* venue (a whole-building closure) — the two calendars are otherwise independent, so
declaring gym closed has no effect on pool. `end_date` blank means a single day.

The collector checks this file before every scrape, per venue — a declared venue (today or a
future date registered ahead of time) is never scraped, even if the other venue is open and
gets scraped normally. It records one `venue_closed: <reason>` marker instead and skips that
venue for the rest of the day. The dashboard shows the reason (and the date range, for a
multi-day closure) right on that venue's card, as soon as the file is updated — no need to
wait for the collector's next cycle.

If a day was already scraped normally *before* you declared it a closure, fix up the
already-recorded rows by hand with `csv_tool.py`, e.g.:

```bash
uv run csv_tool.py exec "UPDATE occupancy SET current_count=NULL,
    source_status='venue_closed: 颱風假' WHERE scraped_at LIKE '2026-07-10%' AND venue_id='gym'"
```

## Project structure

```
ntu_gym_tracker/      # package: config, scraper, parser, hours, closures, storage, data_access, forecast_model
app.py                # FastAPI app (JSON API + dashboard)
collector.py          # collector: always-on loop, or --once for cron
csv_tool.py           # SQL-like CLI for manually inspecting/editing data/occupancy.csv
templates/ static/    # Jinja2 templates + CSS
scripts/              # cron wrapper + systemd unit
data/                 # occupancy.csv (the data store), closures.csv (manual closure calendar)
```

## Roadmap

Occupancy forecasting (calendar + lag features). See `CHANGELOG.md` for
released versions and `spec.md` for implementation details.
