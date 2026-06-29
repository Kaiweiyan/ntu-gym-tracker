# NTU Gym Tracker — Implementation Spec

A running log of **what has actually been built**, the tools used, the
workflow, and the reasoning/implementation details behind each decision.
See `PLAN.md` for the high-level roadmap; this file tracks concrete progress.

- Language / runtime: **Python 3.12** (managed by **uv**)
- Status: **M1 (collector) — working end-to-end**

---

## M0 — Data source investigation

**Goal:** Determine how the live occupancy number reaches the page, so we know
which scraping technique is required.

### Tools
- Browser DevTools (conceptually) + `curl` to fetch raw HTML without a JS engine.

### Findings
- Source URL: `https://rent.pe.ntu.edu.tw/` (the site homepage).
- The occupancy numbers are **server-side rendered directly into the HTML**.
  A plain `curl` (no JavaScript execution) returns the numbers, so this is the
  simplest scraping case — **no Playwright / headless browser needed**.
- There is **no background AJAX / `setInterval`** refreshing the numbers; they
  only change on a full page reload. So we simply GET the homepage each cycle.
- `robots.txt` returns **HTTP 404** → no crawl restrictions declared. We still
  stay polite (low frequency + identifiable User-Agent).

### Page structure
A `.CMCList` container holds one `.CMCItem` per venue. (The `.CMCList` class is
**reused in empty placeholder blocks** elsewhere on the page, so the parser must
anchor on populated `.CMCItem` elements, not on `.CMCList`.)

```html
<div class="CMCList">
  <div class="CMCItem">
    <div class="IT">健身中心</div>
    <div class="IC">
      <div class="ICI"><span>87</span> 現在人數 </div>   <!-- current  -->
      <div class="ICI"><span>80</span> 最適人數 </div>   <!-- optimal  -->
      <div class="ICI"><span>161</span> 最大乘載人數 </div> <!-- capacity -->
    </div>
  </div>
  <div class="CMCItem"> ... 室內游泳池 ... </div>
</div>
```

### Data available per venue (better than originally planned)
- **current_count** (現在人數)
- **optimal_count** (最適人數 — the "comfortable" threshold)
- **max_capacity** (最大乘載人數 — hard capacity)
- Two venues exposed today: **健身中心 (fitness)** and **室內游泳池 (pool)**.

---

## M1 — Collector (scraper → SQLite)

**Goal:** A single command that fetches the page, parses every venue, and stores
one row per venue per run into SQLite. Designed to be run on a schedule.

### Tools / dependencies (added via `uv add`)
- **httpx** — HTTP client to GET the page.
- **beautifulsoup4** — HTML parser (using the stdlib `html.parser` backend, no
  extra C dependency). Chosen over `selectolax` for readability/ubiquity at this
  scale.
- **sqlite3** — stdlib; zero-install file database.

### Module layout (`ntu_gym_tracker/` package)
| File | Responsibility |
|------|----------------|
| `config.py` | Constants: source URL, User-Agent, timeout, venue-name→slug map, DB path. |
| `models.py` | `Observation` dataclass (one venue reading at one timestamp). |
| `parser.py` | `parse_observations(html, scraped_at)` → `list[Observation]`. |
| `scraper.py` | `fetch_html()` (httpx GET) + `scrape()` (fetch→parse, never raises). |
| `db.py` | `connect()` (creates schema) + `insert_observations()`. |
| `../main.py` | CLI entry: one `scrape()` cycle → store → print summary. |

### Workflow (one run)
1. `scrape()` records `scraped_at` = **current UTC time** (ISO-8601). We store
   UTC and convert to `Asia/Taipei` only at display time.
2. `fetch_html()` does an HTTP GET with an **identifiable User-Agent**
   (`ntu-gym-tracker/0.1 (... contact email ...)`), 15s timeout, follows
   redirects, raises on non-2xx.
3. `parse_observations()` selects populated `.CMCItem` blocks, reads the `.IT`
   name, maps it to a stable `venue_id` slug (`健身中心`→`fitness`,
   `室內游泳池`→`pool`; unknown names are slugified), and reads the three
   `.ICI span` numbers in order.
4. `insert_observations()` writes the rows in one transaction.

### Database schema (`data/occupancy.db`, table `observations`)
| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | autoincrement |
| `venue_id` | TEXT | stable slug (`fitness` / `pool`) |
| `venue_name` | TEXT | raw page name |
| `scraped_at` | TEXT | ISO-8601 **UTC**, time WE fetched |
| `current_count` | INTEGER | nullable |
| `optimal_count` | INTEGER | nullable |
| `max_capacity` | INTEGER | nullable |
| `source_status` | TEXT | `ok` / `parse_error` / `fetch_error: ...` |

Index: `(venue_id, scraped_at)` for time-range queries per venue.

**Design rule — failures become rows, not gaps:**
- Network/HTTP failure → one row with `venue_id="_fetch"`, numbers `NULL`,
  `source_status="fetch_error: <ExceptionType>"`.
- Page layout broke (no populated `.CMCItem`) → one row `venue_id="_parse"`,
  numbers `NULL`, `source_status` describing the parse error.
- We **never store 0 for a missing reading** — `NULL` keeps analysis honest.

### Implementation notes / gotchas
- **Bug found & fixed during M1:** first selected `.CMCList` (5 matches: 1 real
  + 4 empty reused blocks), which merged both venues into one block and produced
  4 spurious `parse_error` rows. Fix: anchor on populated `.CMCItem`.
- `_to_int()` strips non-digits defensively before `int()`.
- Number list is padded to length 3 so a missing field becomes `None` instead of
  raising `IndexError`.
- `main.py` exits 0 even on a recorded failure, so a scheduler does not flag an
  expected upstream outage as a crashed job.

### Run instructions
```bash
uv run main.py        # one scrape cycle; creates data/occupancy.db on first run
```

### Verified result (sample)
```
stored 2 observation(s):
  健身中心 (fitness): 89 now (optimal 80, max 161)
  室內游泳池 (pool):  23 now (optimal 50, max 130)
```

> Note: `venue_id` for 健身中心 is `gym` (current config).

---

## M1.x — Scheduled collection (GitHub Actions + git scraping)

**Goal:** Run the collector automatically every 10 min during opening hours and
accumulate data permanently, with zero external services.

### Persistence model — why CSV committed to the repo
GitHub Actions runners are **ephemeral**: the filesystem is wiped after each run,
so anything written to `data/` vanishes unless pushed somewhere external. We use
the **"git scraping"** pattern: each run appends to `data/occupancy.csv` and the
workflow **commits it back to the repo**. Benefits: free, no external DB, every
scrape is preserved in git history with clean human-readable diffs, and the CSV
loads trivially into pandas/SQLite later. (The local `*.db` is git-ignored; CSV
is the canonical committed store.)

- New module `storage.py`: `append_observations()` — append-only CSV, writes the
  header once, encodes `None` numbers as empty cells.
- `.gitignore`: ignores `*.db` but **not** `data/occupancy.csv`.

### Opening hours & the "don't zero-fill" decision
New module `hours.py` (`Asia/Taipei`, uses `zoneinfo`; added `tzdata` dep for
cross-platform reliability):

| Day | Hours |
|-----|-------|
| Mon–Fri | 08:00–22:00 |
| Sat | 09:00–22:00 |
| Sun | 09:00–18:00 |

`main.py` calls `is_open(now_taipei())` first and **skips entirely while closed**.

**Decision — do NOT zero-fill closed periods.** A closed venue is not "open with
0 people"; storing 0 would corrupt time-of-day averages and mislead the
forecaster (closed hours are never prediction targets anyway). To align days of
different lengths (e.g. Sun closes at 18:00, weekdays at 22:00), generate a
regular time grid **at analysis time** with an `is_open` flag and mask closed
slots — never impute 0 into the raw data.

### Workflow — `.github/workflows/scrape.yml`
- Triggers: three `schedule` crons (UTC) covering the Taipei opening windows
  (all map to 00:00–14:00 UTC same weekday, so no day-of-week shift), plus
  `workflow_dispatch` for manual runs.
  - `*/10 0-13 * * 1-5` (Mon–Fri), `*/10 1-13 * * 6` (Sat), `*/10 1-9 * * 0` (Sun).
- `permissions: contents: write` to push data; `concurrency` group serializes runs.
- Steps: checkout → `astral-sh/setup-uv` → `uv run main.py` → commit
  `data/occupancy.csv` and push (only if it changed).
- `main.py` re-checks `is_open()`, so cron drift never records closed-time data.

### CSV schema (`data/occupancy.csv`)
`venue_id, venue_name, scraped_at, current_count, optimal_count, max_capacity, source_status`
(same fields as the SQLite schema; `scraped_at` is ISO-8601 UTC).

### Verified locally
- `is_open()` boundary checks pass (07:30 closed / 08:00 open / 21:50 open /
  22:00 closed; Sun 08:30 closed / 09:00 open / 18:30 closed).
- Forced two scrape cycles → CSV got 1 header + 4 rows, `None` → empty cells.
- Real `uv run main.py` at 22:45 Taipei correctly printed "closed — skipping".

### What the user must do to go live
1. Push this repo to GitHub.
2. Actions tab → enable workflows (scheduled workflows need the default-branch
   workflow to exist; first run can be triggered via **Run workflow**).
3. Data begins accumulating in `data/*.csv` on the next open-hours tick.

---

## M1.y — Weather logging (Open-Meteo)

**Goal:** Record campus weather each cycle as a predictive feature for later
forecasting. Weather is "now-or-never" external state, so it must be logged live
(Open-Meteo's historical archive can also backfill gaps for training).

### Why Open-Meteo
- Free, **no API key**, give it lat/lon (NTU 大安: 25.017, 121.540).
- Forecast **and** historical-archive APIs → gaps backfillable later.
- Hourly/current fields: temperature, apparent temp, humidity, precipitation,
  WMO weather code, wind.

### Storage decision — separate file, one row per cycle
Occupancy stays a single long-format `occupancy.csv` (one row per venue; a third
venue would just add a `venue_id`, not a new file). Weather is **campus-wide, not
per-venue**, so it lives in its own `data/weather.csv` (one row per cycle) and
**joins to occupancy on `scraped_at`**. To make that join exact, `main.py` now
generates one `scraped_at` (UTC) per cycle and passes it to both `scrape()` and
`fetch_weather()`.

### New code
- `config.py`: `NTU_LATITUDE/LONGITUDE`, `OPEN_METEO_URL`,
  `OPEN_METEO_CURRENT_FIELDS`, `WEATHER_CSV_PATH`.
- `models.py`: `WeatherObservation` dataclass.
- `weather.py`: `fetch_weather(scraped_at)` → `WeatherObservation`; failures
  become a row (`source_status="fetch_error: ..."`), never an exception.
- `storage.py`: `append_weather()` + shared `_blank()` None→"" helper.
- `scraper.py`: `scrape()` now takes an optional shared `scraped_at`.
- `main.py`: after occupancy, fetch + append weather and print a summary.
- Workflow: commit step changed `git add data/occupancy.csv` → `git add data/`
  so both CSVs are committed.

### weather CSV schema (`data/weather.csv`)
`scraped_at, observed_at, temperature_c, apparent_temperature_c,
relative_humidity, precipitation_mm, weather_code, wind_speed_kmh, source_status`
- `scraped_at`: UTC, **identical** to the cycle's occupancy rows (join key).
- `observed_at`: weather valid time (Taipei) from the API (~15 min resolution).

### Verified locally
- One cycle wrote `occupancy.csv` (2 rows) and `weather.csv` (1 row) with the
  **same `scraped_at`**; weather parsed: 26.9°C / feels 33.3°C / 90% RH / 0mm /
  code 3 / 1.8 km/h.

---

## Next steps (not yet implemented)
- Add light unit tests for `parser.py` using a saved HTML fixture (guards
  against silent layout changes).
- Observe how often the numbers actually change to infer the real update period.
- **M2+**: data-quality report, then web/API (FastAPI) and forecasting.
