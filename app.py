"""FastAPI web app: JSON API + server-rendered dashboard.

Run it (from the repo root) with:

    uv run uvicorn app:app --reload            # dev, auto-reloads on edits
    uv run uvicorn app:app --host 0.0.0.0 --port 8000   # serve (then Cloudflare Tunnel)

`app` is the ASGI application object uvicorn looks for (`app:app` = file:variable).

Two kinds of routes:
  * /api/*       -> JSON, the reusable API (also consumed by the page's charts).
  * / , /partials/*  -> HTML, the dashboard (Jinja2 templates + ECharts + HTMX).
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ntu_gym_tracker import data_access as data
from ntu_gym_tracker.hours import is_open, now_taipei, open_close

_STATIC_DIR = Path(__file__).resolve().parent / "static"


def _day_label(dt) -> str:
    """e.g. '7/1 (三)' — for the forecast day toggle."""
    return f"{dt.month}/{dt.day} ({data.WEEKDAY_ZH[dt.weekday()]})"


def _closure_notice() -> dict | None:
    """Today's closure banner for the dashboard, one of two kinds:

    - "scheduled": right now falls outside the fixed weekly hours table
      (`hours._HOURS`) — a definite fact, no heuristic needed, and takes
      priority since it also covers every ordinary night/off-day.
    - "suspected": we're inside opening hours, but `data.get_closure_notice()`
      flags an unscheduled closure today (e.g. a typhoon day) — see
      `data_access._suspected_closure_dates`.

    None the rest of the time.
    """
    now = now_taipei()
    if not is_open(now):
        open_t, close_t = open_close(now.weekday())
        return {"kind": "scheduled", "open": f"{open_t:%H:%M}", "close": f"{close_t:%H:%M}"}
    notice = data.get_closure_notice()
    return {"kind": "suspected", **notice} if notice else None


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

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# --- JSON API --------------------------------------------------------------

@app.get("/api/venues")
def api_venues() -> list[dict]:
    return data.list_venues()


@app.get("/api/current")
def api_current() -> dict:
    return {
        "venues": data.get_current(),
        "weather": data.get_current_weather(),
        "closure_notice": _closure_notice(),
    }


@app.get("/api/heatmap")
def api_heatmap(venue: str) -> dict:
    return data.get_heatmap(venue)


@app.get("/api/profile")
def api_profile(venue: str, days: int = 7) -> dict:
    return data.get_profile(venue, days=days)


@app.get("/api/forecast")
def api_forecast(venue: str, day: str = "today") -> dict:
    return data.get_forecast(venue, day=day)


# --- HTML dashboard --------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    now = now_taipei()
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "venues": data.list_venues(),
            "today_label": _day_label(now),
            "tomorrow_label": _day_label(now + timedelta(days=1)),
            "static_version": _static_version(),
        },
    )


@app.get("/partials/current", response_class=HTMLResponse)
def partial_current(request: Request):
    """HTML fragment HTMX swaps in every minute (the live occupancy cards)."""
    return templates.TemplateResponse(
        request,
        "partials/current.html",
        {
            "venues": data.get_current(),
            "weather": data.get_current_weather(),
            "closure_notice": _closure_notice(),
        },
    )
