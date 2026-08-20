"""SQL-like CLI for inspecting/editing data/occupancy.csv by hand.

Loads the CSV into an in-memory SQLite table (`occupancy`) so `query` runs
any real read-only SELECT, and `exec` runs a single UPDATE/DELETE/INSERT that
gets diffed and previewed *before* anything touches disk — nothing is
written until you type `y` at the confirmation prompt. `occupancy.csv` is
only ever written after copying it to a timestamped `.bak` file first.

    uv run csv_tool.py schema
    uv run csv_tool.py query "SELECT * FROM occupancy WHERE source_status='parse_error'"
    uv run csv_tool.py exec "DELETE FROM occupancy WHERE scraped_at LIKE '2026-07-10%'"

See README.md's "Manual CSV editing (csv_tool.py)" section for more examples.
"""

from __future__ import annotations

import argparse
import csv
import shutil
import sqlite3
import sys
import textwrap
from datetime import datetime, timezone
from pathlib import Path

from ntu_gym_tracker.config import CSV_PATH
from ntu_gym_tracker.storage import CSV_FIELDS, _blank

TABLE = "occupancy"

_SCHEMA_SQL = f"""
CREATE TABLE {TABLE} (
    venue_id TEXT,
    venue_name TEXT,
    scraped_at TEXT,
    current_count INTEGER,
    source_status TEXT
);
"""

_SCHEMA_HELP = """\
Table: occupancy  (backed by {path})

  venue_id       TEXT     stable slug, e.g. 'gym' / 'pool' (or '_fetch' /
                          '_parse' on a whole-cycle fetch/parse failure)
  venue_name     TEXT     raw name from the page, e.g. '健身中心'
  scraped_at     TEXT     ISO-8601 UTC, e.g. '2026-07-01T12:10:00+00:00'
                          (NOT Taipei local time — see README for the gotcha)
  current_count  INTEGER  NULL for a fetch/parse error, or a manually
                          declared closure (see closures.py)
  source_status  TEXT     'ok' | 'open' | 'closed' |
                          'fetch_error: ...' | 'parse_error: ...' |
                          'venue_closed: <reason>'
"""


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _load(conn: sqlite3.Connection, path: Path) -> None:
    conn.execute(_SCHEMA_SQL)
    if not path.exists():
        return
    with path.open(newline="", encoding="utf-8") as f:
        rows = [
            (
                r["venue_id"],
                r["venue_name"],
                r["scraped_at"],
                int(r["current_count"]) if r["current_count"] else None,
                r["source_status"],
            )
            for r in csv.DictReader(f)
        ]
    conn.executemany(f"INSERT INTO {TABLE} VALUES (?, ?, ?, ?, ?)", rows)
    conn.commit()


def _write(conn: sqlite3.Connection, path: Path) -> None:
    """Overwrite `path` with the table's current contents, in rowid order
    (insertion order — existing rows keep their original position; a manual
    INSERT lands at the end, not necessarily in chronological order)."""
    cur = conn.execute(f"SELECT venue_id, venue_name, scraped_at, current_count, "
                        f"source_status FROM {TABLE} ORDER BY rowid")
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for venue_id, venue_name, scraped_at, current_count, source_status in cur:
            writer.writerow(
                {
                    "venue_id": venue_id,
                    "venue_name": venue_name,
                    "scraped_at": scraped_at,
                    "current_count": _blank(current_count),
                    "source_status": source_status,
                }
            )


def _snapshot(conn: sqlite3.Connection) -> dict[int, tuple]:
    cur = conn.execute(f"SELECT rowid, * FROM {TABLE}")
    return {row[0]: row[1:] for row in cur}


def _fmt_row(row: tuple) -> str:
    venue_id, venue_name, scraped_at, current_count, source_status = row
    return f"{venue_id:<8} {venue_name:<10} {scraped_at}  count={current_count!r:<6} {source_status}"


def _diff(before: dict[int, tuple], after: dict[int, tuple]):
    inserted = [row for rid, row in after.items() if rid not in before]
    deleted = [row for rid, row in before.items() if rid not in after]
    changed = [
        (before[rid], after[rid])
        for rid in before
        if rid in after and before[rid] != after[rid]
    ]
    return inserted, deleted, changed


def _print_diff(inserted: list, deleted: list, changed: list) -> int:
    for row in deleted:
        print(f"  - DELETE  {_fmt_row(row)}")
    for old, new in changed:
        print(f"  ~ UPDATE  {_fmt_row(old)}")
        print(f"         -> {_fmt_row(new)}")
    for row in inserted:
        print(f"  + INSERT  {_fmt_row(row)}")
    total = len(inserted) + len(deleted) + len(changed)
    print(f"\n{total} row(s) affected.")
    return total


def cmd_schema(_args: argparse.Namespace) -> None:
    print(_SCHEMA_HELP.format(path=CSV_PATH))


def cmd_query(args: argparse.Namespace, path: Path = CSV_PATH) -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    _load(conn, path)
    try:
        cur = conn.execute(args.sql)
    except sqlite3.Error as exc:
        _fail(f"SQL error: {exc}")
        return
    rows = cur.fetchall()
    if not rows:
        print("(0 rows)")
        return
    cols = rows[0].keys()
    widths = [max(len(c), *(len(str(r[c])) for r in rows)) for c in cols]
    print("  ".join(c.ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  ".join(str(r[c]).ljust(w) for c, w in zip(cols, widths)))
    print(f"\n({len(rows)} row(s))")


def cmd_exec(args: argparse.Namespace, path: Path = CSV_PATH) -> None:
    sql = args.sql.strip().rstrip(";")
    verb = sql.split(None, 1)[0].upper() if sql else ""
    if verb not in ("UPDATE", "DELETE", "INSERT"):
        _fail("exec only accepts a single UPDATE, DELETE, or INSERT statement "
              "(use `query` for SELECT).")
    if ";" in sql:
        _fail("exec accepts exactly one statement at a time (no ';'-separated batches).")
    if not path.exists():
        _fail(f"{path} does not exist yet.")

    mtime_before = path.stat().st_mtime
    conn = sqlite3.connect(":memory:")
    _load(conn, path)
    before = _snapshot(conn)
    try:
        conn.execute(sql)
    except sqlite3.Error as exc:
        _fail(f"SQL error: {exc}")
        return
    after = _snapshot(conn)

    inserted, deleted, changed = _diff(before, after)
    if not (inserted or deleted or changed):
        print("No rows affected — nothing to do.")
        return
    _print_diff(inserted, deleted, changed)

    reply = input(f"\nApply and write back to {path}? [y/N] ").strip().lower()
    if reply != "y":
        print("Aborted — nothing written.")
        return

    # Checked *after* the confirmation prompt, not before: the real danger
    # window is however long a human sits at the y/N prompt, not the brief
    # moment spent loading + diffing.
    if path.stat().st_mtime != mtime_before:
        _fail(
            f"{path} changed on disk while this ran (the collector likely "
            "appended a row) — re-run to avoid clobbering it."
        )

    backup = path.parent / f"{path.name}.bak.{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"
    shutil.copy2(path, backup)
    _write(conn, path)
    print(f"Backed up previous file to {backup}")
    print(f"Wrote changes to {path}")


class _Parser(argparse.ArgumentParser):
    """Prints full help (not just usage) on any argument error."""

    def error(self, message: str) -> None:
        self.print_help(sys.stderr)
        sys.stderr.write(f"\nerror: {message}\n")
        raise SystemExit(2)


def _build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="csv_tool.py",
        description=(__doc__ or "").split("\n\n")[0],
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
            examples:
              uv run csv_tool.py schema
              uv run csv_tool.py query "SELECT * FROM occupancy WHERE source_status='parse_error'"
              uv run csv_tool.py exec "UPDATE occupancy SET current_count=45, source_status='ok'
                  WHERE scraped_at='2026-07-01T12:10:00+00:00' AND venue_id='_parse'"
              uv run csv_tool.py exec "DELETE FROM occupancy WHERE scraped_at LIKE '2026-07-10%'"

            See README.md's "Manual CSV editing (csv_tool.py)" section for more.
        """),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("schema", help="print the occupancy table's columns/types").set_defaults(func=cmd_schema)

    p_query = sub.add_parser("query", help="run a read-only SELECT and print the results")
    p_query.add_argument("sql", help='a SELECT statement, e.g. "SELECT * FROM occupancy LIMIT 5"')
    p_query.set_defaults(func=cmd_query)

    p_exec = sub.add_parser(
        "exec", help="run one UPDATE/DELETE/INSERT, with a preview + confirmation before writing"
    )
    p_exec.add_argument("sql", help="a single UPDATE, DELETE, or INSERT statement")
    p_exec.set_defaults(func=cmd_exec)

    return parser


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
