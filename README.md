# NTU Gym Tracker

English | [繁體中文](README.zh-TW.md)

Collect, visualize, and (soon) forecast live occupancy of National Taiwan
University's gym (健身中心) and indoor pool (室內游泳池).

A lightweight collector scrapes the official occupancy page every 10 minutes and
appends to a CSV; a FastAPI app serves both a JSON API and a server-rendered
dashboard with live counts, an occupancy forecast, an average-day profile, and a
monthly closure calendar. The dashboard has Chinese/English and light/dark modes.

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
- **Closure calendar**: this month's declared closures per venue, as a month grid.
- **中文 / English and light / dark modes**, switchable from the header and
  remembered by the browser.
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
uv run collector            # always-on loop, slot-aligned to :00/:10/:20…
uv run collector --once     # a single cycle (e.g. from cron, every 10 min)
```

For a 24/7 deployment use cron, a `systemd --user` service
(`scripts/ntu-gym-collector.service`), or tmux — see `docs/spec.md`.

### Run the web app

```bash
uv run uvicorn ntu_gym_tracker.app:app --reload                     # dev (http://localhost:8000)
uv run uvicorn ntu_gym_tracker.app:app --host 0.0.0.0 --port 8000   # serve
```

## API

| Endpoint        | Description                                          |
| --------------- | ---------------------------------------------------- |
| `GET /api/venues`  | Known venues (`id`, `name`, chart ceiling `y_max`). |
| `GET /api/current` | Latest count per venue.                           |
| `GET /api/forecast` | Actual + forecast curve; params `venue`, `day`.  |
| `GET /api/profile` | Mean per 10-min slot; params `venue`, `days`.     |

## Manual CSV editing (`csv-tool`)

For quick manual fixes to `data/occupancy.csv` — correcting a bad reading, backfilling a
value you know from memory, purging a bad scrape run — `csv-tool` loads the CSV into an
in-memory SQLite table so you can query/edit it with real SQL, instead of hand-editing the
file. Nothing touches disk until you review a preview and confirm; the previous file is
always backed up first (`data/occupancy.csv.bak.<timestamp>`).

```bash
uv run csv-tool schema                             # column names/types
uv run csv-tool query "<a SELECT statement>"        # read-only, prints a table
uv run csv-tool exec "<one UPDATE/DELETE/INSERT>"   # previewed, then confirmed, before writing
```

Examples:

```bash
# List every fetch/parse failure so far
uv run csv-tool query "SELECT * FROM occupancy WHERE source_status LIKE '%error%'"

# Fix a row you know the real count for
uv run csv-tool exec "UPDATE occupancy SET current_count=45, source_status='ok'
    WHERE scraped_at='2026-07-01T12:10:00+00:00' AND venue_id='_parse'"

# Delete every row for one day (e.g. purging a bad scrape run)
uv run csv-tool exec "DELETE FROM occupancy WHERE scraped_at LIKE '2026-07-10%'"
```

`scraped_at` is stored in UTC, not Taipei local time — but since the venues never open
before 08:00 Taipei (= 00:00 UTC, see `ntu_gym_tracker/hours.py`), a plain `LIKE '<date>%'` prefix still
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

If a day was already scraped normally *before* you declared it a closure, the daily check
below cleans it up automatically (within 7 days). To fix one by hand, use `csv-tool`, e.g.:

```bash
uv run csv-tool exec "UPDATE occupancy SET current_count=NULL,
    source_status='venue_closed: 颱風假' WHERE scraped_at LIKE '2026-07-10%' AND venue_id='gym'"
```

## Automated closure check (`daily-check`)

Runs from cron twice a day — 07:30 (before opening) and 22:30 (after closing):

```bash
scripts/run_daily_check.sh               # what cron runs; appends to logs/daily_check.log
uv run daily-check --dry-run             # report what it would do, change nothing
```

1. **Announcements**: reads the official homepage's 新訊與公告 list. Each announcement has a
   stable id (`/news/?K=163`); ids already handled are in `data/announcements_seen.json`, so
   on a day with nothing new, no AI call is made at all. New announcements (text *and* images
   — a monthly notice is usually just a calendar picture) go to a headless Claude Code run
   that can only read those files and must answer in JSON; the script validates its answer
   before adding any closure to `data/closures.csv`.
2. **Reconcile**: rows scraped on a declared closure day (within the last 7 days) — i.e. before
   the closure was registered — are replaced by one `venue_closed` marker, exactly what the
   collector would have written had it known in time. `occupancy.csv` is backed up first.
3. **Commit**: if anything changed, the data files are committed as `[Auto] ...` (never pushed),
   so every automated edit can be reviewed and reverted.

Setup: the crontab entries are in `scripts/run_daily_check.sh`'s header. It needs the
`claude` CLI — on `PATH`, or the one bundled with the VS Code extension (found automatically).

## Project structure

```
ntu_gym_tracker/      # all Python code:
                      #   app (FastAPI: JSON API + dashboard), collector, daily_check, csv_tool,
                      #   config, scraper, parser, hours, closures, storage, models,
                      #   data_access, forecast_model, i18n
templates/ static/    # Jinja2 templates, CSS, favicon
scripts/              # cron wrappers + systemd unit
data/                 # occupancy.csv (the data store), closures.csv (closure calendar),
                      # announcements_seen.json (announcements daily-check has read)
docs/                 # 中文: spec.md (current design + project rules), plan.md (open proposals),
                      # CHANGELOG.md (release history), settings.md (settings overview),
                      # git-style.md (commit format)
CLAUDE.md             # working rules for Claude Code (points to docs/spec.md)
```

The three command-line tools are registered in `pyproject.toml` (`[project.scripts]`), so
`uv sync` installs them into the venv and they run as `uv run collector`, `uv run daily-check`
and `uv run csv-tool`.

## Roadmap

Open proposals (e.g. a learned forecast with academic-calendar and lag features)
are in `docs/plan.md`. See `docs/CHANGELOG.md` (中文) for released versions and
`docs/spec.md` for the current design.
