"""SQLite schema and connection helpers.

Single-user, single-file database. All dates are ISO-8601 strings
(`YYYY-MM-DD`), all timestamps are ISO-8601 with timezone offset.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

from athlete_coach.config import get_config

SCHEMA = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activities (
    id TEXT PRIMARY KEY,               -- "{source}:{external_id}"
    source TEXT NOT NULL,              -- strava | garmin | manual
    external_id TEXT NOT NULL,
    sport TEXT NOT NULL,               -- run | bike | swim | strength | other
    name TEXT,
    start_time TEXT NOT NULL,          -- ISO8601 local
    duration_s REAL,
    moving_time_s REAL,
    distance_m REAL,
    elevation_gain_m REAL,
    avg_hr REAL,
    max_hr REAL,
    avg_power REAL,
    normalized_power REAL,
    avg_cadence REAL,
    calories REAL,
    rpe REAL,                          -- 1-10 perceived exertion, if logged
    tss REAL,                          -- computed training stress score
    load_source TEXT,                  -- power | hr | rpe | duration
    duplicate_of TEXT,                 -- id of the activity this duplicates, if any
    match_key TEXT,                    -- date+sport+duration bucket, for dedupe
    raw_json TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_activities_start ON activities(start_time);
CREATE INDEX IF NOT EXISTS idx_activities_match_key ON activities(match_key);

CREATE TABLE IF NOT EXISTS daily_metrics (
    date TEXT PRIMARY KEY,
    resting_hr REAL,
    hrv_ms REAL,
    body_battery_max REAL,
    body_battery_min REAL,
    sleep_score REAL,
    sleep_duration_s REAL,
    stress_avg REAL,
    steps REAL,
    source TEXT,
    raw_json TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS body_logs (
    date TEXT PRIMARY KEY,
    weight_kg REAL,
    body_fat_pct REAL,
    source TEXT,                       -- manual | garmin
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS nutrition_logs (
    date TEXT PRIMARY KEY,
    calories REAL,
    protein_g REAL,
    carbs_g REAL,
    fat_g REAL,
    source TEXT,                       -- manual | myfitnesspal
    complete INTEGER,                  -- 1 if the MFP diary was marked complete for the day
    notes TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS race_targets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    race_date TEXT NOT NULL,
    race_type TEXT NOT NULL,
    priority TEXT NOT NULL DEFAULT 'A',   -- A | B | C
    notes TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    race_target_id INTEGER REFERENCES race_targets(id),
    name TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    goal_description TEXT,
    status TEXT NOT NULL DEFAULT 'active', -- active | archived
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS plan_weeks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_id INTEGER NOT NULL REFERENCES plans(id),
    week_start_date TEXT NOT NULL,
    block_type TEXT NOT NULL,          -- base | build | peak | taper | recovery
    target_weekly_hours REAL,
    target_weekly_tss REAL,
    notes TEXT,
    UNIQUE(plan_id, week_start_date)
);

CREATE TABLE IF NOT EXISTS planned_workouts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    plan_week_id INTEGER NOT NULL REFERENCES plan_weeks(id),
    date TEXT NOT NULL,
    sport TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT,
    target_type TEXT,                  -- duration | distance | tss
    target_value REAL,
    intensity TEXT,                    -- e.g. Z2, Threshold, VO2max, Sweet Spot
    status TEXT NOT NULL DEFAULT 'planned', -- planned | completed | skipped | modified
    linked_activity_id TEXT REFERENCES activities(id),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_planned_workouts_date ON planned_workouts(date);

CREATE TABLE IF NOT EXISTS coaching_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL DEFAULT (date('now')),
    category TEXT NOT NULL,            -- training | nutrition | body_comp | strength
    note TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


def _row_factory(cursor: sqlite3.Cursor, row: tuple) -> dict[str, Any]:
    fields = [col[0] for col in cursor.description]
    return dict(zip(fields, row))


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    path = db_path or get_config().db_path
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = _row_factory
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Columns added after the initial release: (table, column, DDL type). Applied
# to existing databases that predate them, since `CREATE TABLE IF NOT EXISTS`
# only helps on a fresh DB.
_COLUMN_MIGRATIONS = [
    ("nutrition_logs", "source", "TEXT"),
    ("nutrition_logs", "complete", "INTEGER"),
]


def _apply_column_migrations(conn: sqlite3.Connection) -> None:
    for table, column, ddl_type in _COLUMN_MIGRATIONS:
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {ddl_type}")


def init_db(db_path: Path | None = None) -> None:
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        _apply_column_migrations(conn)
        conn.commit()
    finally:
        conn.close()


@contextmanager
def get_conn(db_path: Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def dumps(obj: Any) -> str:
    return json.dumps(obj, default=str)


def get_setting(conn: sqlite3.Connection, key: str, default: str | None = None) -> str | None:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def main() -> None:
    init_db()
    print(f"Initialized database at {get_config().db_path}")


if __name__ == "__main__":
    main()
