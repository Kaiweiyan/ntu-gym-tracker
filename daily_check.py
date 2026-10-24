"""Daily automated closure check and data reconciliation — meant for cron.

    .venv/bin/python daily_check.py             # check, reconcile, commit
    .venv/bin/python daily_check.py --dry-run   # report only, change nothing

1. Announcements. The official homepage lists 新訊與公告, each with a stable
   id (`/news/?K=163`). Ids already handled live in
   `data/announcements_seen.json`; when nothing is new, Claude is never
   called. Each new announcement's text and images (a monthly notice is
   usually just a calendar image) go to a headless `claude -p` whose only
   tool is Read, confined to a temp dir holding those images. It answers
   with JSON, which this script validates before appending to
   `data/closures.csv` — Claude itself can't write anything.
2. Reconcile. On a declared closure day within `RECONCILE_DAYS`, any
   occupancy row for that venue that isn't a `venue_closed` marker was
   scraped before the closure was registered. Those rows are replaced by
   one marker at that day's opening tick — what the collector would have
   written had it known in time. `occupancy.csv` is backed up first.
3. If closures or occupancy data changed, commit the data files (never
   pushes), so every automated edit is reviewable and revertible.
"""

from __future__ import annotations

import argparse
import csv
import glob
import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from ntu_gym_tracker.closures import closures_on
from ntu_gym_tracker.config import (
    CLOSURES_PATH,
    CSV_PATH,
    REQUEST_TIMEOUT_SECONDS,
    SOURCE_URL,
    USER_AGENT,
    VENUE_ID_BY_NAME,
)
from ntu_gym_tracker.hours import TAIPEI, now_taipei, open_close
from ntu_gym_tracker.storage import CSV_FIELDS

REPO = Path(__file__).resolve().parent
SEEN_PATH = REPO / "data" / "announcements_seen.json"
DATA_FILES = ["data/closures.csv", "data/occupancy.csv", "data/announcements_seen.json"]

# How far back reconciliation looks. Older days are left alone so a late
# closure entry can't silently rewrite long-settled history.
RECONCILE_DAYS = 7
CLAUDE_TIMEOUT_SECONDS = 300

VENUE_IDS = set(VENUE_ID_BY_NAME.values())
VENUE_NAMES = {vid: name for name, vid in VENUE_ID_BY_NAME.items()}
NEWS_ID = re.compile(r"/news/\?K=(\d+)")

PROMPT = """You are reading new announcements from National Taiwan University's sports
center (臺大綜合體育館) website. Today is {today} (Asia/Taipei). ROC year 115 = 2026.

Find every WHOLE-DAY closure of these indoor venues:
- 健身中心 (gym) -> venue_id "gym"
- 室內游泳池 / 溫水游泳池 (indoor heated pool) -> venue_id "pool"
- the whole 綜合體育館 / NTU Sports Center (both venues at once) -> venue_id ""

Ignore outdoor facilities (戶外游泳池, 網球場, outdoor fields, ...), fees and
policies, reopening notices, days marked 正常開館, and partial-day changes
(e.g. closed until 17:00, shortened hours) — describe partial-day changes in
"notes" instead. Only report what an announcement states explicitly; never guess.

Some announcements are only an image (often a monthly calendar). Use the Read
tool on every image file listed below and read it carefully.

Reply with ONLY one JSON object and nothing else:
{{"closures": [{{"venue_id": "", "date": "YYYY-MM-DD", "end_date": "YYYY-MM-DD", "reason": "颱風假"}}], "notes": []}}
- end_date equals date for a single day; consecutive closed days with the same reason form one range.
- reason: a short Traditional Chinese label (at most 10 characters), e.g. 颱風假, 國慶連假, 設備維修.
- No qualifying closure: {{"closures": [], "notes": []}}

Announcements:
{announcements}"""


def log(msg: str) -> None:
    print(f"[{now_taipei():%Y-%m-%d %H:%M}] {msg}", flush=True)


def _get(path: str) -> httpx.Response:
    resp = httpx.get(
        urljoin(SOURCE_URL, path),
        headers={"User-Agent": USER_AGENT},
        timeout=REQUEST_TIMEOUT_SECONDS,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return resp


# --- 1. announcements -------------------------------------------------------


def list_announcements() -> dict[int, str]:
    """{id: title} for every announcement linked from the homepage."""
    soup = BeautifulSoup(_get("/").text, "html.parser")
    out: dict[int, str] = {}
    for a in soup.find_all("a", href=True):
        m = NEWS_ID.search(str(a["href"]))
        if m:
            out[int(m.group(1))] = a.get_text(" ", strip=True)
    return out


def fetch_announcement(k: int, img_dir: Path) -> tuple[str, list[str]]:
    """(body text, image file names saved into img_dir) for one announcement."""
    soup = BeautifulSoup(_get(f"/news/?K={k}").text, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    lines = [ln.strip() for ln in soup.get_text("\n").splitlines() if ln.strip()]
    # The body sits between the search form's 清除 button and 相關連結.
    start = lines.index("清除") + 1 if "清除" in lines else 0
    end = next((i for i in range(start, len(lines)) if lines[i] == "相關連結"), len(lines))
    body = "\n".join(lines[start:end])[:6000]

    images = []
    for img in soup.find_all("img", src=True):
        src = str(img["src"])
        if not src.startswith("/edimg/"):
            continue
        name = f"K{k}_{len(images)}{Path(src).suffix or '.jpg'}"
        (img_dir / name).write_bytes(_get(src).content)
        images.append(name)
    return body, images


def _version_key(path: str) -> tuple[int, ...]:
    m = re.search(r"claude-code-(\d+(?:\.\d+)*)", path)
    return tuple(int(x) for x in m.group(1).split(".")) if m else ()


def find_claude() -> str:
    """`claude` on PATH, else the newest CLI bundled with the VS Code extension
    (its path carries a version number that changes on every update)."""
    found = shutil.which("claude")
    if found:
        return found
    bundled = glob.glob(os.path.expanduser(
        "~/.vscode-server/extensions/anthropic.claude-code-*/resources/native-binary/claude"
    ))
    if not bundled:
        raise RuntimeError("no claude CLI found on PATH or in ~/.vscode-server/extensions")
    return max(bundled, key=_version_key)


def ask_claude(today: date, items: list[tuple[int, str, str, list[str]]], img_dir: Path) -> dict:
    blocks = [
        f"--- Announcement K={k}: {title}\nText:\n{body or '(no text)'}\n"
        f"Images to read: {', '.join(images) or '(none)'}"
        for k, title, body, images in items
    ]
    prompt = PROMPT.format(today=today.isoformat(), announcements="\n\n".join(blocks))
    cmd = [
        find_claude(), "-p", prompt,
        "--restricted", "--tools", "Read", "--allowedTools", "Read",
        "--strict-mcp-config", "--no-session-persistence",
        "--model", "sonnet", "--output-format", "json",
    ]
    res = subprocess.run(cmd, cwd=img_dir, capture_output=True, text=True,
                         timeout=CLAUDE_TIMEOUT_SECONDS)
    if res.returncode != 0:
        raise RuntimeError(f"claude exited {res.returncode}: {(res.stderr or res.stdout)[:500]}")
    envelope = json.loads(res.stdout)
    log(f"claude call cost ${envelope.get('total_cost_usd', 0):.4f}")
    m = re.search(r"\{.*\}", envelope.get("result", ""), re.S)
    if not m:
        raise RuntimeError(f"no JSON in claude's reply: {envelope.get('result', '')[:500]}")
    return json.loads(m.group(0))


def validate(proposals: list, today: date) -> list[tuple[str, date, date, str]]:
    """Keep only well-formed, plausible closures — Claude's output is never
    trusted blindly before it can stop the collector for a day."""
    ok = []
    for p in proposals:
        try:
            vid = str(p.get("venue_id") or "").strip()
            start = date.fromisoformat(p["date"])
            end = date.fromisoformat(p.get("end_date") or p["date"])
            reason = " ".join(str(p.get("reason") or "").split())[:20] or "未說明原因"
        except (KeyError, ValueError, TypeError, AttributeError):
            log(f"  rejected malformed closure: {p!r}")
            continue
        if vid and vid not in VENUE_IDS:
            log(f"  rejected unknown venue_id: {p!r}")
        elif end < start or (end - start).days > 60:
            log(f"  rejected implausible date range: {p!r}")
        elif end < today - timedelta(days=RECONCILE_DAYS) or start > today + timedelta(days=366):
            log(f"  skipped closure outside the useful window: {p!r}")
        else:
            ok.append((vid, start, end, reason))
    return ok


def _already_covered(vid: str, start: date, end: date) -> bool:
    venues = [vid] if vid else sorted(VENUE_IDS)
    d = start
    while d <= end:
        if any(v not in closures_on(d) for v in venues):
            return False
        d += timedelta(days=1)
    return True


def append_closures(rows: list[tuple[str, date, date, str]]) -> None:
    text = CLOSURES_PATH.read_text(encoding="utf-8") if CLOSURES_PATH.exists() else ""
    with CLOSURES_PATH.open("a", newline="", encoding="utf-8") as f:
        if text and not text.endswith("\n"):
            f.write("\n")
        writer = csv.writer(f, lineterminator="\n")
        for vid, start, end, reason in rows:
            writer.writerow([vid, start.isoformat(), "" if end == start else end.isoformat(), reason])


def check_announcements(today: date, dry_run: bool) -> list[str]:
    """Returns a description of each closure added to closures.csv."""
    seen = set(json.loads(SEEN_PATH.read_text())["seen"]) if SEEN_PATH.exists() else set()
    current = list_announcements()
    new = sorted(k for k in current if k not in seen)
    if not new:
        log("no new announcements")
        return []
    log(f"new announcements: {', '.join(f'K={k} {current[k][:40]}' for k in new)}")

    with tempfile.TemporaryDirectory(prefix="ntu_gym_news_") as tmp:
        img_dir = Path(tmp)
        items = [(k, current[k], *fetch_announcement(k, img_dir)) for k in new]
        answer = ask_claude(today, items, img_dir)

    for note in answer.get("notes") or []:
        log(f"  note: {note}")
    added = []
    for vid, start, end, reason in validate(answer.get("closures") or [], today):
        label = f"{vid or '兩館'} {start}{'' if end == start else f'~{end}'} {reason}"
        if _already_covered(vid, start, end):
            log(f"  already registered: {label}")
        else:
            log(f"  {'would add' if dry_run else 'adding'} closure: {label}")
            added.append((vid, start, end, reason))
    if not dry_run:
        if added:
            append_closures(added)
        SEEN_PATH.write_text(json.dumps({"seen": sorted(seen | set(current))}, indent=2) + "\n")
    return [f"{vid or '兩館'} {s}{'' if e == s else f'~{e}'} {r}" for vid, s, e, r in added]


# --- 2. reconcile -----------------------------------------------------------


def _local_date(scraped_at: str) -> date:
    return datetime.fromisoformat(scraped_at).astimezone(TAIPEI).date()


def _opening_tick_utc(d: date) -> str:
    open_t, _ = open_close(d.weekday())
    return datetime.combine(d, open_t, tzinfo=TAIPEI).astimezone(timezone.utc).isoformat()


def reconcile(today: date, dry_run: bool) -> list[str]:
    """Replace rows scraped on a since-declared closure day with one marker.
    Returns a description of each venue-day fixed."""
    closed: dict[tuple[str, date], str] = {}
    for offset in range(RECONCILE_DAYS + 1):
        d = today - timedelta(days=offset)
        for vid, closure in closures_on(d).items():
            closed[(vid, d)] = closure.reason
    if not closed or not CSV_PATH.exists():
        return []

    mtime = CSV_PATH.stat().st_mtime
    with CSV_PATH.open(newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    def key(row: dict) -> tuple[str, date]:
        return (row["venue_id"], _local_date(row["scraped_at"]))

    stale: dict[tuple[str, date], int] = {}
    for row in rows:
        k = key(row)
        if k in closed and not row["source_status"].startswith("venue_closed:"):
            stale[k] = stale.get(k, 0) + 1
    if not stale:
        return []

    fixes = [f"{vid} {d} {closed[(vid, d)]}（{n} 筆）" for (vid, d), n in sorted(stale.items())]
    for fix in fixes:
        log(f"  {'would replace' if dry_run else 'replacing'} rows scraped on a closure day: {fix}")
    if dry_run:
        return fixes

    new_rows, done = [], set()
    for row in rows:
        k = key(row)
        if k not in stale:
            new_rows.append(row)
        elif k not in done:  # the marker takes the place of the day's first row
            done.add(k)
            vid, d = k
            new_rows.append({
                "venue_id": vid,
                "venue_name": row["venue_name"] or VENUE_NAMES.get(vid, ""),
                "scraped_at": _opening_tick_utc(d),
                "current_count": "",
                "source_status": f"venue_closed: {closed[k]}",
            })

    if CSV_PATH.stat().st_mtime != mtime:
        log("  occupancy.csv changed while reconciling (collector write?) — skipped, will retry next run")
        return []
    backup = CSV_PATH.with_name(f"{CSV_PATH.name}.bak.{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}")
    shutil.copy2(CSV_PATH, backup)
    tmp = CSV_PATH.with_name(CSV_PATH.name + ".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(new_rows)
    os.replace(tmp, CSV_PATH)
    log(f"  backed up to {backup.name}")
    return fixes


# --- 3. commit --------------------------------------------------------------


def commit(added: list[str], fixed: list[str]) -> None:
    parts = []
    if added:
        parts.append("add closures: " + "; ".join(added))
    if fixed:
        parts.append("replace data scraped on closure days: " + "; ".join(fixed))
    message = "[Auto] " + " | ".join(parts)
    existing = [p for p in DATA_FILES if (REPO / p).exists()]
    subprocess.run(["git", "add", "--", *existing], cwd=REPO, check=True)
    res = subprocess.run(["git", "commit", "-m", message, "--", *existing],
                         cwd=REPO, capture_output=True, text=True)
    log(f"git commit: {(res.stdout or res.stderr).strip().splitlines()[0] if (res.stdout or res.stderr) else res.returncode}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0] if __doc__ else None)
    parser.add_argument("--dry-run", action="store_true", help="report what would change, change nothing")
    args = parser.parse_args()

    today = now_taipei().date()
    log(f"daily check start{' (dry run)' if args.dry_run else ''}")
    added: list[str] = []
    try:
        added = check_announcements(today, args.dry_run)
    except Exception as exc:  # network/CLI trouble must not block reconciliation
        log(f"announcement check failed: {type(exc).__name__}: {exc}")
    fixed = reconcile(today, args.dry_run)
    if (added or fixed) and not args.dry_run:
        commit(added, fixed)
    log("daily check done")


if __name__ == "__main__":
    main()
