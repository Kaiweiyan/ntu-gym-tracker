# NTU Gym Tracker

Collect, visualize, and (soon) forecast live occupancy of National Taiwan
University's gym (健身中心) and indoor pool (室內游泳池).

A lightweight collector scrapes the official occupancy page every 10 minutes and
appends to a CSV; a FastAPI app serves both a JSON API and a server-rendered
dashboard with live counts, a weekday × hour heatmap, an average-day profile, and
occupancy trends.

## How it works

```
┌──────────────────────────────────┐
│ collector (always-on, 24/7)      │
└──────────────────────────────────┘
                 │  scrape + parse, every 10 min
                 ▼
┌──────────────────────────────────┐
│ data/occupancy.csv (append-only) │
└──────────────────────────────────┘
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

- **Live occupancy** per venue with a "vs. typical" busyness indicator and live
  weather (auto-refreshing via HTMX).
- **Weekday × hour heatmap** of average occupancy.
- **Average-day profile**: mean occupancy per 10-minute slot over the last N days.
- **Occupancy trend** with selectable 1 / 3 / 7 / 30-day ranges; closed hours are
  skipped so nights don't show as gaps.
- **Opening-hours aware** collection: a single `count=0` marker is recorded at the
  open and close ticks (kept distinct via `source_status` so it can be excluded
  from training); failed fetches are recorded as rows, never as a misleading `0`.

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

Interactive API docs are at `/docs`. To expose the local server publicly without
opening firewall ports, point a [Cloudflare Tunnel](https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/)
at `http://localhost:8000`.

## API

| Endpoint        | Description                                          |
| --------------- | ---------------------------------------------------- |
| `GET /api/venues`  | Known venues (`id`, `name`).                      |
| `GET /api/current` | Latest count per venue + live weather.            |
| `GET /api/history` | Trend; params `venue`, `days`, `granularity`.     |
| `GET /api/heatmap` | Weekday × hour average matrix; param `venue`.     |
| `GET /api/profile` | Mean per 10-min slot; params `venue`, `days`.     |

## Project structure

```
ntu_gym_tracker/      # package: config, scraper, parser, hours, storage, data_access
app.py                # FastAPI app (JSON API + dashboard)
collector.py          # collector: always-on loop, or --once for cron
templates/ static/    # Jinja2 templates + CSS
scripts/              # cron wrapper + systemd unit
data/                 # occupancy.csv (the data store)
```

## Roadmap

Occupancy forecasting (calendar + weather + lag features). See `CHANGELOG.md` for
released versions and `spec.md` for implementation details.
