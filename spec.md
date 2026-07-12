# NTU Gym Tracker — Implementation Spec

What's actually built and the non-obvious reasoning behind it, organized by
current architecture rather than a change-by-change log. See `PLAN.md` for the
original roadmap and `CHANGELOG.md` for release-by-release history.

- Runtime: **Python 3.12**, managed by **uv**.
- Status: collector + web dashboard/API done (M1–M2), baseline forecast
  shipped (M4, v0.3.0+), ops hardening in progress (M5).

---

## Data source

- `https://rent.pe.ntu.edu.tw/` — occupancy numbers are **server-rendered
  directly in the HTML**, no JS execution needed, so a plain `httpx` GET is
  enough (confirmed via `curl` during initial investigation).
- No AJAX/`setInterval` refresh — numbers only change on a full reload, so the
  collector just re-fetches the homepage every cycle.
- `robots.txt` 404s (no declared restrictions); stay polite anyway via a low
  frequency (10 min) and an identifiable `User-Agent`.
- Page structure: a `.CMCList` wraps one `.CMCItem` per venue, each with an
  `.IT` name and three `.ICI > span` numbers (current / optimal / max
  capacity), in that order. `.CMCList` is **reused for empty placeholder
  blocks** elsewhere on the page, so the parser anchors on populated
  `.CMCItem` elements, never on `.CMCList`.
- `optimal_count`/`max_capacity` are confirmed **fixed per venue** (unchanged
  across every scrape so far) — they live in `config.VENUE_CAPACITY` (gym:
  80/161, pool: 50/130), not scraped or stored per row.

---

## Collector (`collector.py`, `ntu_gym_tracker/{scraper,parser,storage,hours}.py`)

- Always-on loop, slot-aligned to wall-clock `:00/:10/:20…`
  (`seconds_until_next_slot`); also runnable as a single cycle (`--once`) via
  cron (`scripts/run_collector.sh`) or `systemd --user`
  (`scripts/ntu-gym-collector.service`, survives logout via `loginctl
  enable-linger`).
- **Failures become rows, not gaps.** A fetch or parse failure records one row
  with `current_count=NULL` and a descriptive `source_status`
  (`fetch_error: ...` / `parse_error: ...`) — never a fabricated `0`.
- `scrape()` retries the fetch+parse pair together, up to `config.FETCH_RETRIES`
  (3) times with exponential backoff (`FETCH_RETRY_BACKOFF_SECONDS`), before
  giving up for the cycle — a transient HTTP failure (timeout/connection/5xx)
  and a transient parse failure (e.g. the page briefly rendering an
  incomplete body) get the same second chance, since neither can be told
  apart from a genuine outage without simply trying again. Every retry reuses
  the cycle's original `scraped_at` unchanged — a retry re-samples the same
  scheduled tick, it does not shift the tick to whenever the retry actually
  runs. This only rescues *this cycle's* attempt — occupancy is a live,
  time-varying number, so a retry can never recover a reading that was
  genuinely never taken.
- **Opening-hours aware** (`hours.py`, `Asia/Taipei`, per-weekday table:
  Mon–Fri 08–22, Sat 09–22, Sun 09–18). Closed ticks are skipped entirely,
  *except* the exact opening and closing tick, which record one `count=0` row
  per venue instead (`source_status` `"open"`/`"closed"`) — this lets a chart
  line return to 0 at close (and avoid a stale non-zero right at open)
  **without** zero-filling every closed slot, which would corrupt averages.
- `scraped_at` is stored as UTC, truncated to **whole-second** precision
  (unnecessary detail at a 10-min cadence).

---

## Data store

- `data/occupancy.csv` — append-only, long format (one row per venue per
  cycle). Schema: `venue_id, venue_name, scraped_at, current_count,
  source_status`. No SQLite, no separate weather CSV (both removed).
- Weather is fetched **live** for the dashboard (Open-Meteo, no API key,
  ~10 min server-side TTL cache) and never stored; historical weather (for
  future model training) would be backfilled from Open-Meteo's archive
  on-demand against the occupancy timestamps.

---

## Web app (`app.py`, `ntu_gym_tracker/data_access.py`, `ntu_gym_tracker/forecast_model.py`)

FastAPI + Jinja2 + ECharts + HTMX. Routes: `/api/venues`, `/api/current`,
`/api/profile`, `/api/heatmap`, `/api/forecast`; `/` (dashboard) and
`/partials/current` (HTMX fragment, refetched every 60s).

- `_load_csv()` caches the parsed DataFrame keyed on the file's mtime, so the
  expensive part (disk read, full CSV parse, UTC→Taipei conversion,
  weekday/hour derivation) only reruns after the collector appends new data.
  Per-request aggregation downstream (dropna/groupby/resample) is *not*
  cached, but is sub-millisecond at the current data volume — revisit with
  pre-aggregated hourly buckets only if that stops being true.
- `_occupancy_ok()` keeps real `"ok"` readings **and** the open/close
  0-markers (both have a non-null count) — used by charts that want a line to
  return to 0. `get_heatmap()` instead filters strictly to `source_status ==
  "ok"`, since averaging in the 0-markers would fake an always-empty closing
  hour and drag down the opening hour's average.
  **Known gap:** `get_profile()`/`get_forecast()` still include those
  markers, so each day's very first and last 10-min slot is structurally
  always 0 in both — same root cause as the heatmap bug, left unfixed by the
  user's choice (not in scope when found).
- **Busyness** (`get_current`): an absolute level vs. the venue's own
  `optimal_count` — `quiet` (<50%), `normal` (50–100%), `busy` (>100%) — not a
  comparison to the historical average for that time slot, so it answers "is
  it crowded right now" rather than "busier than usual for this hour".
- **Forecast** (`forecast_model.py`): all forecast logic — baselines, the
  strategy registry, gap-fill, rounding — lives here, not in `data_access.py`.
  `data_access.get_forecast()` is a thin wrapper: it resolves the venue's raw
  readings (`df`, for `actual`) and closure-excluded historical readings
  (`hist`, for the baseline, via `_historical_ok()`), then calls
  `forecast_model.forecast(df, hist, day)`, which owns no CSV/loading logic
  of its own — kept swappable/testable independent of the data layer, and
  the natural home for a learned model later. Baseline is picked via a
  swappable strategy registry — `config.FORECAST_METHOD` selects a key in
  `forecast_model._STRATEGIES`. Current strategies: `same_weekday_mean`
  (default — mean per slot over historical days sharing the target weekday)
  and `recent_mean` (rolling `FORECAST_RECENT_DAYS`-day window, any
  weekday). Adding a model later means writing one more
  `(df, target_date) -> {slot: mean}` function and registering it —
  `forecast_model.forecast()` itself doesn't change. The baseline is drawn
  for the **entire** day (not just from "now" on), so actual and forecast
  overlap and a user can judge the forecast's accuracy directly. Short (≤2
  slot / 20 min) gaps in `actual` from a missed scrape are linearly
  interpolated (`_fill_short_gaps`); longer gaps are left alone (a real
  outage, not papered over). All displayed counts are rounded to whole
  numbers (`_round_ints`). `slot_means()` (the per-10-min-slot averaging
  used by the baselines) is also reused by `data_access.get_profile()`'s
  non-forecast average-day chart — the one function shared back across the
  data_access → forecast_model dependency direction.
- **Heatmap** x-axis cells are labeled by hour range (`"08-09"`) rather than a
  bare hour number — ECharts always centers one label per category band, so
  there's no clean way to put independent labels at each cell's two edges
  without custom canvas rendering; the range label conveys the same
  information without fighting the library.
- **Suspected ad-hoc closures** (`data_access._suspected_closure_dates`):
  typhoon days etc. have no entry in the fixed weekly hours table
  (`hours._HOURS`) since they're unscheduled, so they're inferred from the
  data instead — if every known venue reads a real (`source_status == "ok"`)
  `0` for `config.SUSPECTED_CLOSURE_MIN_SLOTS` (3) or more *consecutive*
  10-min slots, that date is flagged. Requiring **all** venues to be zero
  *together*, for a run this long, rules out an ordinary quiet slot at one
  venue (single-venue, brief) without needing a maintained calendar of
  ad-hoc closures. Flagged dates are excluded from `_historical_ok()`, the
  df every historical aggregate (`get_profile`, `get_heatmap`, the forecast
  baseline) reads from, so one typhoon day doesn't drag down a weekday/slot's
  average; `get_current`/`get_forecast`'s `actual` line intentionally still
  read from the raw (unfiltered) data, so a suspected-closure day still shows
  what was actually recorded — only the *historical average* treats it as
  noise. `data_access.get_closure_notice()` surfaces today's flag (if any);
  `app._closure_notice()` is what the dashboard actually reads from — it
  layers this ad-hoc heuristic under a first, *definite* check against the
  fixed weekly hours table (`hours.is_open()`), so the same banner also
  covers ordinary non-opening hours (every night, Sunday evening, ...), not
  just the ad-hoc/typhoon case. `{"kind": "scheduled", ...}` (outside the
  table; takes priority) vs. `{"kind": "suspected", ...}` (inside the table,
  heuristic fired) — `partials/current.html` renders a different message for
  each.

---

## Frontend (`templates/`)

`index.html` section order, top to bottom: 現在人數 (live cards + weather,
HTMX-refreshed) → 場館選擇 (one global venue toggle, drives every chart below
it) → 人數預測 → 各時段平均人數 → 各時段熱門程度 (heatmap). There is no
trend/history chart — it was removed for not pulling its weight, along with
`get_history()`, the `/api/history` route, and all related frontend code.

The global venue picker (`.venue-select`) is deliberately styled bigger and
shaped differently (rounded rect + shadow, `font-weight: 600`) than the
per-chart `.venue-toggle` buttons (forecast 今天/明天, profile day-range) —
same underlying `wireToggle()` pattern, but visually distinct so it doesn't
read as "just another chart's option."

`base.html` links `/static/style.css?v={{ static_version }}`, where
`app._static_version()` returns the file's own mtime. Without this, a
browser (or an intermediate proxy, e.g. a Cloudflare Tunnel) that already
cached the old stylesheet keeps serving it after a CSS change — the URL
never changed, so nothing tells the cache to refetch. Bumping the query
string on every edit forces a fresh fetch instead of relying on a manual
hard-refresh.

---

## Deployment

Collection runs on an always-on university workstation, not GitHub Actions —
an ephemeral CI runner can't persist local files without a commit-back
workflow (tried first, then dropped once a 24/7 host was available). Three
supported run modes: cron, `systemd --user` (preferred — auto-restart,
survives logout), or tmux/nohup. The web app runs under `uvicorn`, exposed
publicly via a Cloudflare Tunnel.

---

## Gotchas worth remembering

- Starlette's `TemplateResponse` takes `(request, name, context)` in that
  order — passing `name` first silently makes it treat the context dict as
  the template name.
- pandas + pyright: prefer `df.loc[mask, col]` / `.query(...)` over
  `df[mask]` (typed `DataFrame | Series`, noisy under a type checker), and
  route pandas scalars through small untyped helpers (`_to_float`, `_mean`)
  rather than inlining arithmetic on them.
- CSV timestamps have mixed precision across historical rows (some with
  microseconds, some without) — always parse with `pd.to_datetime(...,
  format="ISO8601")`, which tolerates the mix; a fixed format string will
  fail on one or the other.

---

## Known limitations / next steps

- `get_profile`/`get_forecast` baseline still includes the open/close
  0-markers (see "Known gap" above) — same fix as the heatmap's, not yet
  applied.
- `same_weekday_mean` can return sparse/empty slots for weekdays with little
  history yet; expected to fill in as more weeks of data accumulate.
- No automated tests yet (`parser.py` / API, using fixtures).
- Pre-aggregation isn't needed yet — revisit if per-request query time becomes
  noticeable at a much larger data volume.
