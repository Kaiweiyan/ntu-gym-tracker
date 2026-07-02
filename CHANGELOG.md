# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/), and the project
uses [Semantic Versioning](https://semver.org/). Detailed implementation notes
live in `spec.md`.

## [Unreleased]

## [0.3.1] - 2026-07-02 — Reliability & schema cleanup

### Added
- **Fetch retry with backoff**: `scraper.fetch_html()` retries transient HTTP
  failures (timeout / connection error / 5xx) up to 3 times with exponential
  backoff before the cycle records a `fetch_error`. This only rescues a
  transient failure within the same cycle — it can't recover a reading that
  was genuinely never taken.
- **Forecast gap interpolation**: `get_forecast()`'s "actual" series now
  linearly interpolates isolated short gaps (up to 2 consecutive missed
  10-min slots) between real readings on both sides; longer outages are left
  as gaps rather than papered over.

### Changed
- **optimal_count / max_capacity moved to config**: fixed per venue
  (confirmed unchanging since day one), so they now live in
  `config.VENUE_CAPACITY` instead of being scraped and stored on every row.
  `data/occupancy.csv` schema is now
  `venue_id, venue_name, scraped_at, current_count, source_status`; the
  existing file was migrated in place (columns dropped, row data unchanged).
- **scraped_at truncated to whole-second precision** (was microseconds) —
  more precision than a 10-minute collection cadence needs.

## [0.3.0] - 2026-07-01 — Occupancy forecast

### Added
- **Occupancy forecast** chart below the live counts (`GET /api/forecast`):
  今天/明天 toggle (with each day's date + weekday), solid actual (open→now)
  meeting a dashed forecast (now→close), and a "現在" marker. Baseline = historical
  mean per 10-min slot across all days; swappable for a model with no frontend
  change.
- Every venue-dependent chart heading now shows the current venue name, so it's
  clear which venue each chart displays.
- Charts auto-refresh every 5 minutes (the forecast's "now" advances over time).

### Changed
- Dashboard order: current → heatmap → forecast → average profile → trend (the
  venue selector now sits above the charts it controls).

### Removed
- Dead `db.py` / `DB_PATH` (the data store is CSV-only).

## [0.2.1] - 2026-07-01 — Mobile-friendly charts

### Added
- Emoji favicon (inline SVG; removes the `/favicon.ico` 404).

### Changed
- Mobile: line charts rotate/thin their axis labels on narrow screens, and the
  heatmap scrolls horizontally so cells stay readable.

## [0.2.0] - 2026-07-01 — Web dashboard & API

### Added
- **FastAPI web app** (`app.py`): JSON API (`/api/venues`, `/api/current`,
  `/api/history`, `/api/heatmap`, `/api/profile`) plus a server-rendered
  dashboard (Jinja2 + ECharts + HTMX).
- **Dashboard charts**: live occupancy cards with "vs typical" busyness
  (HTMX auto-refresh), weekday×hour heatmap, average-day profile, and an
  occupancy trend with 1 / 3 / 7 / 30-day ranges (weekday-tagged date labels).
- **Data-access layer** (`data_access.py`): pandas aggregation with an mtime
  cache; closed-hour gaps dropped from the trend; weekday-tagged day labels.
- **Boundary 0 markers**: the collector records one `count=0` row per venue at
  the opening and closing ticks (`source_status` `open`/`closed`) so curves
  return to 0; these are excluded from training via the `ok` filter.
- **Live weather** on the dashboard (Open-Meteo, ~10-min server cache).
- Editor/type-checker config: `pyrightconfig.json`, `.vscode/settings.json`.

### Changed
- Renamed `collector_loop.py` → `collector.py`, and merged `main.py` into it:
  `uv run collector.py` runs the always-on loop, `--once` runs a single cycle.
- **Weather is no longer stored** in `data/` — shown live instead; historical
  weather for training will be backfilled from Open-Meteo's archive.
- Dashboard section order: current → heatmap → average profile → trend.

### Removed
- `data/weather.csv`, `storage.append_weather`, and the weather CSV config path.

### Fixed
- Clipped y-axis title on the line charts.
- pandas/pyright type warnings; Starlette `TemplateResponse` signature; mixed
  ISO-8601 precision parsing.

## [0.1.1] - 2026-06-30 — Collector on the workstation

### Added
- `collector.py` always-on loop (slot-aligned) with cron / systemd / tmux
  deployment options.

### Removed
- GitHub Actions collector / git-scraping workflow (collection moved to a 24/7
  university workstation with persistent disk).

## [0.1.0] - 2026-06-29 — Scheduled collector

### Added
- Scraper for NTU gym & pool live occupancy (`rent.pe.ntu.edu.tw`) and campus
  weather (Open-Meteo), appending to CSV.
- Opening-hours guard, failures-recorded-as-rows, SQLite schema.
- GitHub Actions cron (git-scraping) running every 10 min during opening hours.
