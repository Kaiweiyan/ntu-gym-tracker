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

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from ntu_gym_tracker import data_access as data

app = FastAPI(title="NTU Gym Tracker")

templates = Jinja2Templates(directory="templates")
app.mount("/static", StaticFiles(directory="static"), name="static")


# --- JSON API --------------------------------------------------------------

@app.get("/api/venues")
def api_venues() -> list[dict]:
    return data.list_venues()


@app.get("/api/current")
def api_current() -> dict:
    return {"venues": data.get_current(), "weather": data.get_current_weather()}


@app.get("/api/history")
def api_history(venue: str, days: int = 7, granularity: str = "hour") -> dict:
    return data.get_history(venue, days=days, granularity=granularity)


@app.get("/api/heatmap")
def api_heatmap(venue: str) -> dict:
    return data.get_heatmap(venue)


@app.get("/api/profile")
def api_profile(venue: str, days: int = 7) -> dict:
    return data.get_profile(venue, days=days)


# --- HTML dashboard --------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def index(request: Request):
    venues = data.list_venues()
    return templates.TemplateResponse(request, "index.html", {"venues": venues})


@app.get("/partials/current", response_class=HTMLResponse)
def partial_current(request: Request):
    """HTML fragment HTMX swaps in every minute (the live occupancy cards)."""
    return templates.TemplateResponse(
        request,
        "partials/current.html",
        {"venues": data.get_current(), "weather": data.get_current_weather()},
    )
