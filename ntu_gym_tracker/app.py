"""FastAPI web app: JSON API + server-rendered dashboard.

Run it with:

    uv run uvicorn ntu_gym_tracker.app:app --reload   # dev, auto-reloads on edits
    uv run uvicorn ntu_gym_tracker.app:app --host 0.0.0.0 --port 8000   # serve (then Cloudflare Tunnel)

`app` is the ASGI application object uvicorn looks for (`ntu_gym_tracker.app:app` = module:variable).

Two kinds of routes:
  * /api/*       -> JSON, the reusable API (also consumed by the page's charts).
  * / , /partials/*  -> HTML, the dashboard (Jinja2 templates + ECharts + HTMX).
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ntu_gym_tracker import data_access as data
from ntu_gym_tracker import i18n
from ntu_gym_tracker.closures import closures_between, closures_on
from ntu_gym_tracker.config import VENUE_ID_BY_NAME
from ntu_gym_tracker.hours import is_open, now_taipei, open_close

# Repo root: templates/ and static/ live there, next to (not inside) the package.
_REPO = Path(__file__).resolve().parent.parent
_STATIC_DIR = _REPO / "static"


def _ui(request: Request) -> dict:
    """Template context every page needs: the language (from the `lang`
    cookie, see `i18n`) and its strings. The theme is client-side only."""
    lang = i18n.get_lang(request.cookies)
    return {"lang": lang, "t": i18n.STRINGS[lang]}


def _venue_closures() -> dict[str, dict]:
    """Today's manual closures per venue (see `closures.py`), for the
    per-card closed-with-reason display: `{venue_id: {"reason": ...,
    "range": "8/17–9/20", "multi_day": True}}`. `range` is only meant to be
    shown when `multi_day` — for a single declared day, "today" already
    says it, so a date would be redundant. Independent per venue: gym being
    closed doesn't imply anything about pool.
    """
    out = {}
    for vid, closure in closures_on(now_taipei().date()).items():
        start, end = closure.start, closure.end
        out[vid] = {
            "reason": closure.reason,
            "range": f"{start.month}/{start.day}–{end.month}/{end.day}",
            "multi_day": closure.multi_day,
        }
    return out


def _scheduled_closure() -> dict | None:
    """Page-level banner for ordinary non-opening hours (every night,
    Sunday evening, ...) — from the fixed weekly hours table
    (`hours._HOURS`), shared by every venue alike, unlike the per-venue
    manual closures above.
    """
    now = now_taipei()
    if is_open(now):
        return None
    open_t, close_t = open_close(now.weekday())
    return {"open": f"{open_t:%H:%M}", "close": f"{close_t:%H:%M}"}


def _static_version() -> str:
    """Cache-busting token for /static assets, appended as `?v=` on asset URLs.

    Without this, a browser (or an intermediate proxy like a Cloudflare
    Tunnel) that already cached style.css keeps serving the stale copy after
    a CSS change, even on a plain refresh — the URL never changed, so there's
    nothing telling the cache to refetch. Using the file's own mtime means
    the query string changes exactly when the file does.
    """
    return str(int((_STATIC_DIR / "style.css").stat().st_mtime))

app = FastAPI(title="NTU Gym Tracker")

templates = Jinja2Templates(directory=_REPO / "templates")
app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


# --- JSON API --------------------------------------------------------------

@app.get("/api/venues")
def api_venues() -> list[dict]:
    return data.list_venues()


@app.get("/api/current")
def api_current() -> dict:
    return {
        "venues": data.get_current(),
        "venue_closures": _venue_closures(),
        "scheduled_closure": _scheduled_closure(),
    }


@app.get("/api/profile")
def api_profile(venue: str, days: int = 7) -> dict:
    return data.get_profile(venue, days=days)


@app.get("/api/forecast")
def api_forecast(venue: str, day: str = "today") -> dict:
    return data.get_forecast(venue, day=day)


# --- HTML dashboard --------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    ui = _ui(request)
    lang = ui["lang"]
    now = now_taipei()
    venues = [
        {**v, "name": i18n.venue_name(v["id"], v["name"], lang)}
        for v in data.list_venues()
    ]
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            **ui,
            "t_js": i18n.js_strings(lang),
            "venues": venues,
            "today_label": i18n.day_label(now, lang),
            "tomorrow_label": i18n.day_label(now + timedelta(days=1), lang),
            "static_version": _static_version(),
        },
    )


@app.get("/partials/current", response_class=HTMLResponse)
def partial_current(request: Request):
    """HTML fragment HTMX swaps in every minute (the live occupancy cards)."""
    ui = _ui(request)
    venues = [
        {**v, "venue_name": i18n.venue_name(v["venue_id"], v["venue_name"], ui["lang"])}
        for v in data.get_current()
    ]
    return templates.TemplateResponse(
        request,
        "partials/current.html",
        {
            **ui,
            "venues": venues,
            "venue_closures": _venue_closures(),
            "scheduled_closure": _scheduled_closure(),
        },
    )


@app.get("/partials/calendar", response_class=HTMLResponse)
def partial_calendar(request: Request):
    """This month's declared closures (see `closures.py`) as a month grid,
    plus a list of them under it. Loaded once by HTMX with the page."""
    ui = _ui(request)
    lang = ui["lang"]
    today = now_taipei().date()
    first = today.replace(day=1)
    # Sunday-first full weeks, including the neighbouring months' days that
    # pad out the first and last rows (shown dimmed, never tagged).
    weeks = calendar.Calendar(firstweekday=6).monthdatescalendar(first.year, first.month)
    found = closures_between(first, weeks[-1][-1])
    all_venues = set(VENUE_ID_BY_NAME.values())

    def tags(d: date) -> list[dict]:
        day = found.get(d, {}) if d.month == first.month else {}
        reasons = {c.reason for c in day.values()}
        # Every venue closed for the same reason reads as one "全館" tag.
        if day and set(day) == all_venues and len(reasons) == 1:
            return [{"label": i18n.venue_short("all", lang), "reason": reasons.pop()}]
        return [
            {"label": i18n.venue_short(vid, lang), "reason": c.reason}
            for vid, c in sorted(day.items())
        ]

    def cell(d: date) -> dict:
        day = found.get(d, {}) if d.month == first.month else {}
        return {
            "day": d.day,
            "in_month": d.month == first.month,
            "today": d == today,
            "tags": tags(d),
            # Solid fill only when *every* venue is closed; one venue closed
            # gets a lighter fill so the two aren't confused at a glance.
            "closed": "full" if set(day) == all_venues else ("part" if day else None),
        }

    cells = [[cell(d) for d in week] for week in weeks]

    # The same closures as a list, consecutive days with identical tags merged
    # into one range ("9/1–9/29 健身 整修") — readable on a phone, where the
    # day cells are too narrow for much text.
    listing: list[dict] = []
    d = first
    while d.month == first.month:
        day_tags = tags(d)
        prev = listing[-1] if listing else None
        if day_tags and prev and prev["tags"] == day_tags and prev["end"] == d - timedelta(days=1):
            prev["end"] = d
        elif day_tags:
            listing.append({"start": d, "end": d, "tags": day_tags})
        d += timedelta(days=1)
    for item in listing:
        start, end = item.pop("start"), item.pop("end")
        item["dates"] = (f"{start.month}/{start.day}" if start == end
                         else f"{start.month}/{start.day}–{end.month}/{end.day}")

    return templates.TemplateResponse(
        request,
        "partials/calendar.html",
        {
            **ui,
            "weeks": cells,
            "weekday_names": i18n.calendar_weekdays(lang),
            "month_title": i18n.month_title(first, lang),
            "listing": listing,
        },
    )
