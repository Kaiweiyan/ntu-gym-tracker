"""SQLite storage for occupancy observations.

One file holds the whole DB (easy to back up). Schema keeps raw readings
only; derived features (weekday, hour, holiday...) are computed later at
analysis time so the raw table stays clean.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .config import DB_PATH
from .models import Observation

_SCHEMA = """
CREATE TABLE IF NOT EXISTS observations (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    venue_id      TEXT    NOT NULL,
    venue_name    TEXT    NOT NULL,
    scraped_at    TEXT    NOT NULL,            -- ISO-8601 UTC
    current_count INTEGER,
    optimal_count INTEGER,
    max_capacity  INTEGER,
    source_status TEXT    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_obs_venue_time
    ON observations (venue_id, scraped_at);
"""


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    """Open (creating parent dirs + schema if needed) the SQLite database."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def insert_observations(conn: sqlite3.Connection, observations: Iterable[Observation]) -> int:
    """Insert rows; returns the number written."""
    rows = [
        (
            o.venue_id,
            o.venue_name,
            o.scraped_at,
            o.current_count,
            o.optimal_count,
            o.max_capacity,
            o.source_status,
        )
        for o in observations
    ]
    with conn:  # transaction
        conn.executemany(
            """
            INSERT INTO observations
                (venue_id, venue_name, scraped_at,
                 current_count, optimal_count, max_capacity, source_status)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
    return len(rows)
