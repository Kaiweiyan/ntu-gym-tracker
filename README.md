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
(no headless browser) is enough. Weather is shown **live** from Open-Meteo and is
not stored — historical weather can be backfilled from its archive for training.

## Features

- **Live occupancy** per venue with an absolute busyness level (vs. the
  venue's own optimal_count) and live weather (auto-refreshing via HTMX).
- **Occupancy forecast**: today/tomorrow, actual readings meeting a baseline
  forecast line (configurable strategy, see `config.FORECAST_METHOD`).
- **Average-day profile**: mean occupancy per 10-minute slot over the last N days.
- **Weekday × hour heatmap** of average occupancy.
- **Opening-hours aware** collection: a single `count=0` marker is recorded at the
  open and close ticks (kept distinct via `source_status` so it can be excluded
  from training); failed fetches are recorded as rows, never as a misleading `0`.
- **Suspected ad-hoc closure detection**: an unscheduled closure (e.g. a
  typhoon day) isn't in the fixed weekly hours table, so it's inferred from
  the data — all venues reading 0 together for long enough flags a dashboard
  banner and excludes that day from the historical averages.

## Tech stack

| Area       | Tools                                              |
| ---------- | -------------------------------------------------- |
| Runtime    | Python 3.12, managed with [uv](https://docs.astral.sh/uv/) |
| Scraping   | `httpx`, `beautifulsoup4`                          |
| Data       | `pandas`, CSV (long format)                        |
| Web / API  | `FastAPI`, `uvicorn`, `Jinja2`                     |
| Charts     | Apache ECharts + HTMX (via CDN)                    |
| Weather    | [Open-Meteo](https://open-meteo.com/) (no API key) |

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
| `GET /api/current` | Latest count per venue + live weather.            |
| `GET /api/forecast` | Actual + forecast curve; params `venue`, `day`.  |
| `GET /api/profile` | Mean per 10-min slot; params `venue`, `days`.     |
| `GET /api/heatmap` | Weekday × hour average matrix; param `venue`.     |

## Project structure

```
ntu_gym_tracker/      # package: config, scraper, parser, hours, storage, data_access, forecast_model
app.py                # FastAPI app (JSON API + dashboard)
collector.py          # collector: always-on loop, or --once for cron
templates/ static/    # Jinja2 templates + CSS
scripts/              # cron wrapper + systemd unit
data/                 # occupancy.csv (the data store)
```

## Roadmap

Occupancy forecasting (calendar + weather + lag features). See `CHANGELOG.md` for
released versions and `spec.md` for implementation details.
