# NTU Gym Tracker — Implementation Spec

What's actually built and the non-obvious reasoning behind it, organized by
current architecture rather than a change-by-change log. See `plan.md` for the
original roadmap and the repo root's `CHANGELOG.md` for release-by-release
history.

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

## Collector (`collector.py`, `ntu_gym_tracker/{scraper,parser,storage,hours,closures}.py`)

- Always-on loop, slot-aligned to wall-clock `:00/:10/:20…`
  (`seconds_until_next_slot`); also runnable as a single cycle (`--once`) via
  cron (`scripts/run_collector.sh`) or `systemd --user`
  (`scripts/ntu-gym-collector.service`, survives logout via `loginctl
  enable-linger`).
- **Manually declared closures** (`closures.py`, `data/closures.csv`:
  `venue_id, date, end_date, reason`) are checked first, before the hours
  table — holidays/typhoon days/maintenance are unscheduled and can't live
  in a fixed weekly table, and (an earlier attempt at this) can't be
  reliably guessed from the occupancy data either, so they're
  hand-maintained instead. **Independent per venue**: gym and pool have
  separate calendars, so one can be closed (e.g. for renovation) while the
  other keeps its normal schedule; a row with a blank `venue_id` applies to
  every known venue at once (e.g. a whole-building closure), rather than
  needing one row per venue. `closures_on(d) -> dict[venue_id, Closure]`
  resolves both cases in one pass — every known venue is checked against
  its own rows plus any blank-`venue_id` row.
  - On a declared closure day, `run_once()` writes one
    `current_count=None, source_status="venue_closed: <reason>"` row for
    each closed venue at the would-be opening tick (`is_opening_tick()` —
    the same "write once, at the boundary" pattern as the open/close
    0-markers below) and skips that venue for every other tick that day; a
    closed venue is **never scraped**, today or a pre-registered future
    date, while any other venue that's still open keeps its normal
    every-10-min scraping. Since a single page fetch returns every venue at
    once, a scrape still happens whenever *any* venue is open — the closed
    venue's Observation is then filtered out of the result (dropped even if
    the page happens to show a number for it) rather than skipping the
    fetch. `scrape()` is skipped entirely only when *every* known venue is
    closed that day.
  - `current_count=None` (not `0`) means these rows fall out of
    `data_access._occupancy_ok()` automatically — same mechanism as a
    fetch/parse error — so no separate filtering logic was needed anywhere
    downstream (profile/forecast).
  - A day that's already been scraped normally *before* being
    retroactively declared a closure is a manual fix with `csv_tool.py`
    (`UPDATE occupancy SET current_count=NULL, source_status='venue_closed: ...' WHERE ... AND venue_id='...'`),
    not automated — rare enough, and human judgment enough, not to warrant
    a scheduled reconciliation job.
  - A blank `reason` cell is normalized to a placeholder ("未說明原因")
    while the CSV is loaded (`closures._load_rows()`) rather than passed
    through empty into a bare `venue_closed: ` status or an empty
    closed-card detail line.
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
  source_status`. No SQLite.
- `data/closures.csv` — the manually-maintained closure calendar (see
  "Manually declared closures" above); small enough to commit and hand-edit
  directly, unlike the occupancy log.

---

## Automated closure check (`daily_check.py`, `scripts/run_daily_check.sh`)

Cron on the collector's host (ws3) runs it at 07:30 and 22:30 Taipei — before
opening, and after closing (a same-day/next-day closure is usually posted the
evening before, e.g. the typhoon notice went up 10/22 for 10/23–24).

- **Only new announcements cost anything.** The homepage's 新訊與公告 list
  links each announcement as `/news/?K=<id>`, a stable id. Handled ids live
  in `data/announcements_seen.json` (committed); a run with nothing new
  makes zero AI calls. An id is marked seen only after Claude's answer was
  parsed, so a failed call is retried next run. The homepage only lists the
  latest ~7, which is plenty at two runs a day.
- **Claude reads, the script writes.** Body text (between the search
  form's 清除 button and 相關連結) and every `/edimg/` image are fetched by
  Python; images matter because monthly 營業公告 are a calendar picture with
  no text at all. They go to a headless `claude -p` with `--restricted
  --tools Read`, cwd = a temp dir holding only those images — it can read
  them and nothing else, and its only output is a JSON
  `{"closures": [...], "notes": [...]}`. `validate()` then checks it before
  anything touches `closures.csv`: known `venue_id` (or blank), parseable
  dates, `end >= start`, range ≤ 60 days, not older than the reconcile
  window, not more than a year out. A closure already fully covered is not
  re-added. Asymmetric risk is why this matters: a wrongly added closure
  stops the collector for that venue all day and that data can never be
  recovered, whereas a missed one only leaves zeros to clean up later.
- **Partial-day changes are logged, not applied** (`notes`): `closures.csv`
  only models whole days.
- **Reconcile** (deterministic, no AI): for each declared closure day in the
  last `RECONCILE_DAYS` (7), any row for that venue that isn't a
  `venue_closed` marker was scraped before the closure was registered; all
  of that venue-day's rows are replaced by one marker at the day's opening
  tick, placed where the first removed row was (keeps chronological order).
  Days with no rows at all are left alone — in the 07:30 run today has no
  rows yet, and the collector writes today's marker itself at the opening
  tick. Backup first (`occupancy.csv.bak.*`), temp file + `os.replace`, and
  an mtime check right before replacing (a collector write in between →
  skip, retry next run). Idempotent.
- **Commit**: if closures or occupancy changed, `git add` + `git commit --
  <the three data files>` as `[Auto] ...`. The pathspec commits only those
  files even if other changes are staged; never pushes.
- **The CLI** is `claude` on PATH, else the newest
  `~/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude`
  (the extension's versioned path changes on every update, so it's globbed
  per run, not hard-coded). The wrapper sets PATH and a UTF-8 locale since
  cron's environment is minimal; log at `logs/daily_check.log` (gitignored).

---

## Manual CSV editing (`csv_tool.py`)

- SQL-like CLI for hand-fixing `data/occupancy.csv` (backfilling a known
  value, purging a bad scrape run) without touching the file directly.
  `schema` / `query` (read-only `SELECT`) / `exec` (a single
  `UPDATE`/`DELETE`/`INSERT`). Deliberately **not** a general SQL shell:
  `exec` refuses multiple `;`-separated statements so its diff preview stays
  trustworthy, and `query` can never write since its SQLite connection is
  never passed to `_write()`.
- Loads the whole CSV into an **in-memory SQLite table** (small enough —
  thousands of rows — that this costs nothing) rather than doing string-level
  CSV surgery, so `exec` gets real `WHERE` semantics for free instead of a
  hand-rolled filter DSL.
- `exec`'s flow: load → snapshot the table (keyed by SQLite `rowid`, our only
  stand-in for a primary key) → run the statement → snapshot again → diff the
  two snapshots by `rowid` (present-only-before = deleted, present-only-after
  = inserted, present-both-but-changed = updated) → print the diff → ask
  `y/N`. **Nothing touches `occupancy.csv` until that confirmation returns
  `y`** — an aborted or crashed run leaves the file exactly as it was, no
  transaction/rollback machinery needed because the CSV was never opened for
  writing in the first place.
- The disk-mtime check (was the file appended to by the collector while we
  were deciding?) runs **after** the `y/N` prompt returns, not before —
  the real race window is however long a human sits at the prompt, not the
  brief load+diff before it. (Caught by testing: an earlier version checked
  before the prompt and missed exactly this case.)
- A confirmed write always makes a `<file>.name.bak.<UTC timestamp>` copy
  first (`.gitignore`d — local recovery only, not meant for git history) via
  `shutil.copy2` before `_write()` overwrites the original, so a bad edit is
  one `mv` away from undone.
- `_write()` reuses `storage.CSV_FIELDS` and `storage._blank()` (the same
  column order and `None`→`""` convention the collector's own writer uses),
  so a row edited by hand and a row appended by the collector are
  byte-for-byte indistinguishable in the file.
- Rows keep the CSV's original order after a write (`ORDER BY rowid` on
  write-back preserves insertion order); a manually `INSERT`ed row lands at
  the end regardless of its `scraped_at`, since SQLite assigns `rowid`s by
  insertion order, not by any column's value — fine for a one-off backfill,
  but worth knowing if you insert several out-of-order rows in one session.

---

## Web app (`app.py`, `ntu_gym_tracker/data_access.py`, `ntu_gym_tracker/forecast_model.py`)

FastAPI + Jinja2 + ECharts + HTMX. Routes: `/api/venues`, `/api/current`,
`/api/profile`, `/api/forecast`; `/` (dashboard), `/partials/current` (HTMX
fragment, refetched every 60s) and `/partials/calendar` (HTMX fragment, the
closure calendar).

- `_load_csv()` caches the parsed DataFrame keyed on the file's mtime, so the
  expensive part (disk read, full CSV parse, UTC→Taipei conversion,
  weekday/hour derivation) only reruns after the collector appends new data.
  Per-request aggregation downstream (dropna/groupby/resample) is *not*
  cached, but is sub-millisecond at the current data volume — revisit with
  pre-aggregated hourly buckets only if that stops being true.
- `_occupancy_ok()` keeps real `"ok"` readings **and** the open/close
  0-markers (both have a non-null count) — used by charts that want a line to
  return to 0.
  **Known gap:** `get_profile()`/`get_forecast()` therefore also average
  those markers in, so each day's very first and last 10-min slot is
  structurally always 0 in both — left unfixed by the user's choice (not in
  scope when found). (The since-removed heatmap filtered strictly to
  `source_status == "ok"` for exactly this reason.)
- **Busyness** (`get_current`): an absolute level vs. the venue's own
  `optimal_count` — `quiet` (<50%), `normal` (50–100%), `busy` (>100%) — not a
  comparison to the historical average for that time slot, so it answers "is
  it crowded right now" rather than "busier than usual for this hour".
- **Forecast** (`forecast_model.py`): all forecast logic — baselines, the
  strategy registry, gap-fill, rounding — lives here, not in `data_access.py`.
  `data_access.get_forecast()` is a thin wrapper: it resolves the venue's
  readings (`df`) and calls `forecast_model.forecast(df, day)`, which owns no
  CSV/loading logic of its own — kept swappable/testable independent of the
  data layer, and the natural home for a learned model later. Baseline is
  picked via a swappable strategy registry — `config.FORECAST_METHOD`
  selects a key in `forecast_model._STRATEGIES`. Current strategies:
  `same_weekday_mean` (default — mean per slot over historical days sharing
  the target weekday) and `recent_mean` (rolling `FORECAST_RECENT_DAYS`-day
  window, any weekday). Adding a model later means writing one more
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
- **Closure display** is split across two independent pieces, because manual
  closures are per-venue but the weekly hours table is shared:
  - `app._venue_closures()` returns `{venue_id: {reason, range, multi_day}}`
    for today (via `closures.closures_on()`) — rendered on *that venue's
    card* (`休館中` + reason, plus the date range only when `multi_day`, per
    `partials/current.html`), since it's meaningful even during what would
    otherwise be business hours (a holiday at 2pm should say so, not defer
    to "well it's within business hours" clock logic).
  - `app._scheduled_closure()` is the page-level `🌙 目前非開放時間` banner
    for ordinary non-opening hours (`hours.is_open()`) — shared by every
    venue alike, so it stays a single banner rather than per-card.
  - A card with no `_venue_closures()` entry falls back to the generic
    "休館中" (no reason line, no date) whenever `_scheduled_closure()` is
    set, and to its normal count/busyness otherwise.
  - (A prior version instead tried inferring *unscheduled* closures from
    the data itself — every venue reading a real 0 for several consecutive
    slots; that heuristic was removed for being both unreliable and more
    indirect than just recording the known date, which is what
    `closures.py` replaced it with.)

---

## Frontend (`templates/`)

`index.html` section order, top to bottom: 現在人數 (live cards,
HTMX-refreshed) → 場館選擇 (one global venue toggle, drives every chart below
it) → 人數預測 → 各時段平均人數 → 休館行事曆 (closure calendar). Two charts
were removed for not pulling their weight, each along with its API route and
data function: the trend/history chart (`/api/history`, `get_history()`), and
later the weekday × hour heatmap (`/api/heatmap`, `get_heatmap()`), whose
spot the closure calendar took.

**Closure calendar** (`partials/calendar.html`, `app.partial_calendar`): an
HTMX fragment loaded once into `#calendar`, **this month only** (no month
navigation — it's for "is the gym open on day X this month", not history).
Server-rendered for the same reason as the cards — the language cookie.
Sunday-first full weeks (`calendar.Calendar(firstweekday=6)`), the
neighbouring months' padding days dimmed and never tagged; closures come from
one `closures.closures_between()` call (the CSV is read once, not per day).
It shows **both venues** regardless of the global venue toggle, as short tags
(健身 / 泳池), collapsing to one 全館 tag when every venue closes for the same
reason. Closed days are red (`--closed`, as holidays are on Taiwanese
calendars), and the fill says how much is closed: **solid** when every venue
is closed (even for different reasons), a **light** fill when only one is —
the blue accent was tried first and read as "selected" rather than "closed".
A legend explains the two, and a list under the grid repeats the closures
with consecutive identical days merged ("9/1–9/29 健身 整修"), since phone
cells (~45px) fit little text. "Today" is an `outline`, not the border, so a
closed day's border can't hide it.

**Language and theme** are two independent header toggles, both remembered
by the browser but stored differently on purpose:
- **Language → `lang` cookie** (1 year, SameSite=Lax), set by JS then a
  reload. The text is rendered server-side — including the HTMX
  `/partials/current` fragment, refreshed every 60s — and only a cookie
  reaches the server on those requests; `localStorage` never would.
  `app._ui()` reads it (invalid/missing → zh) and passes `t` =
  `i18n.STRINGS[lang]`; chart labels go to the page JS as
  `i18n.js_strings(lang)`. Venue names are scraped in Chinese, so English
  mode maps them by `venue_id`. Closure reasons are free text from
  `closures.csv` and are shown as written. The JSON API is language-neutral.
- **Theme → `localStorage`**, purely client-side. An inline script in
  `<head>` sets `<html data-theme>` before the stylesheet paints (no light
  flash on a dark page); never chosen → follows `prefers-color-scheme`.
  CSS swaps the color variables under `:root[data-theme="dark"]` (the blue
  is `--accent` now, not hard-coded). ECharts bakes its theme in at
  `init()`, so the toggle fires a `themechange` event and `index.html`
  disposes and re-creates the charts with the built-in `dark` theme,
  `backgroundColor: "transparent"` so the card background shows through.

The forecast and profile charts use a **fixed y-axis per venue**, `0` to
`config.CHART_Y_MAX[venue]` or else the venue's `max_capacity` (gym 161,
pool 80; carried on each venue button as `data-y-max`, from
`list_venues()`), instead of ECharts' auto-scaling. With auto-scaling, a
day peaking at 70 and one peaking at 120 drew identically tall curves when
toggling today/tomorrow, which misread as equal crowding. Capacity is the
default because it never changes and doubles as a "how close to full"
reference; the pool overrides it to 80 because it peaks ~80 of 130 and
would otherwise only use the bottom half of its chart. The axis `max` is a
function, `Math.max(ceiling, dataMax)`, so a day that ever exceeds the
ceiling stretches the axis rather than clipping the curve. Both line
charts are taller (`.chart-tall`, 480px / 400px on phones) for the same
reason.

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
- A real `"ok"` scrape can still be wrong: gym's on-site counter reported a
  flat/low `0` for ~6 days (2026-09-30–10-05) while the venue was actually open
  with real foot traffic — a hardware/counter fault, not a scraper bug (the
  fetch+parse succeeded every time) and not a closure (`venue_closed`
  wouldn't be honest either). Fixed up by hand with `csv_tool.py`
  (`current_count=NULL, source_status='sensor_error: ...'`) — the same
  null-count-means-excluded mechanism as every other bad-data status, just
  under a label that says *why* without claiming it was a fetch/parse
  failure or a declared closure. Nothing automated catches this; it only
  surfaces as a visibly-wrong multi-day flat `0` on the dashboard.

---

## Known limitations / next steps

- `get_profile`/`get_forecast` baseline still includes the open/close
  0-markers (see "Known gap" above) — not yet fixed.
- `same_weekday_mean` can return sparse/empty slots for weekdays with little
  history yet; expected to fill in as more weeks of data accumulate.
- No automated tests yet (`parser.py` / API, using fixtures).
- Pre-aggregation isn't needed yet — revisit if per-request query time becomes
  noticeable at a much larger data volume.
