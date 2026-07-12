# Changelog

All notable changes to this project are documented here.
Format follows [Keep a Changelog](https://keepachangelog.com/), and the project
uses [Semantic Versioning](https://semver.org/). Detailed implementation notes
live in `spec.md`.

## [Unreleased]

## [0.4.1] - 2026-07-12 — Ad-hoc closure detection, retry fix, forecast_model split

### Added
- **Suspected ad-hoc closure detection**: if every venue reads a real 0 for
  3+ consecutive 10-min slots (e.g. a typhoon day), the dashboard now shows
  a "可能臨時休館" banner, and that date is excluded from the historical
  averages behind `/api/profile`, `/api/heatmap`, and the forecast baseline
  so it doesn't drag them down. These days aren't in the fixed weekly hours
  table (`hours._HOURS`) — they're inferred from the data itself, not a
  maintained calendar.
- The closure banner also covers ordinary non-opening hours now (every
  night, Sunday evening, ...), not just the ad-hoc/typhoon case —
  `app._closure_notice()` checks `hours.is_open()` first (a definite fact)
  before falling back to the ad-hoc heuristic.
- Live occupancy cards no longer show a misleading "0 人" while
  `closure_notice` is set — the count/percent/busyness block is replaced
  with a plain "休館中" label (venue name and last-updated time still show).

### Changed
- **Forecast logic moved to `ntu_gym_tracker/forecast_model.py`**: baselines,
  the strategy registry, gap-fill, and rounding all moved out of
  `data_access.py` into their own module, which takes plain DataFrames and
  owns no CSV/loading logic — the intended home for a learned model later.
  `data_access.get_forecast()` is now a thin wrapper that resolves the raw +
  closure-excluded DataFrames and delegates to `forecast_model.forecast()`.
  No behavior change.

### Fixed
- **Retry didn't cover parse failures**: `fetch_html()`'s retry only wrapped
  the HTTP GET, so a fetch that succeeded (200 OK) but then failed to parse
  (e.g. a momentarily incomplete page render) was recorded as a permanent
  `parse_error` with zero retries. `scrape()` now retries the fetch+parse
  pair as one unit, up to `FETCH_RETRIES` times with backoff, reusing the
  cycle's original `scraped_at` unchanged on every attempt.
- **pyright errors in `_suspected_closure_dates`**: iterating a pandas
  `Series.items()` types the index label as plain `Hashable`, so `slot -
  prev_slot` and `slot.date()` didn't type-check. Cast each label to
  `pd.Timestamp` explicitly before use.

## [0.4.0] - 2026-07-09 — Forecast strategies, heatmap fixes, dashboard cleanup

### Added
- **Configurable forecast baseline** (`config.FORECAST_METHOD`):
  `same_weekday_mean` (default — mean per slot over historical days sharing
  the target weekday) or `recent_mean` (rolling N-day window). Implemented as
  a strategy registry (`data_access._FORECAST_STRATEGIES`) so a model-based
  strategy can be added later without touching `get_forecast()`.
- Forecast chart now draws the baseline for the **entire day**, not just from
  "now" onward, so the actual and predicted lines overlap and forecast
  accuracy is visible at a glance.
- Cache-busting for static assets (`/static/style.css?v=<mtime>`) so CSS/JS
  edits are picked up on a normal refresh instead of requiring a manual
  hard-refresh.

### Changed
- **Busyness level** (`get_current`) is now an absolute level vs. the venue's
  own `optimal_count` (`quiet` <50%, `normal` 50–100%, `busy` >100%), not a
  comparison to the historical average for that time slot.
- Forecast and profile values are rounded to whole numbers for display.
- Heatmap hour labels are shown as ranges (`"08-09"`) instead of a single
  number, and the redundant `"時"` axis title was removed.
- Dashboard section order: 現在人數 → 場館選擇 → 人數預測 → 各時段平均人數 →
  熱力圖. The venue selector is now a standalone, visually distinct global
  control (bigger, differently shaped) instead of living inside the heatmap
  section.
- `data_access.py` internal reorganization (function order matches route
  order, private helpers consolidated) — no behavior change beyond the fixes
  below.
- `spec.md` rewritten as an architecture-organized reference instead of a
  chronological change log.

### Removed
- **Breaking:** `GET /api/history` endpoint and the 人數趨勢 (occupancy
  trend) chart.

### Fixed
- Heatmap cells were rendered under the wrong hour label (an indexing bug —
  raw hour numbers were used as chart-axis indices instead of positions), and
  cells for the last several opening hours didn't render at all.
- Heatmap averages no longer include the opening/closing boundary `count=0`
  markers, which had faked an always-empty "22:00" column and diluted the
  opening hour's average.

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
