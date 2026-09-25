"""Pull logged nutrition from MyFitnessPal and upsert into `nutrition_logs`.

Never overwrites a 'manual' entry (logged directly via `log_nutrition`) —
manual entries win, same as Garmin weigh-ins never overwrite a manual
body-log entry. One HTTP request per day (the library scrapes MFP's diary
page), so keep `days_back` modest — this is meant for "catch up the last
couple of weeks", not a full history backfill.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from athlete_coach.mfp_client import MyFitnessPalClient


def sync_myfitnesspal_nutrition(conn: sqlite3.Connection, days_back: int = 14) -> dict:
    client = MyFitnessPalClient()
    if not client.authorized:
        return {"error": "not_authorized", "days_synced": 0}

    end = date.today()
    start = end - timedelta(days=days_back - 1)

    synced = 0
    skipped_manual = 0
    for entry in client.get_range(start, end):
        existing = conn.execute(
            "SELECT source FROM nutrition_logs WHERE date = ?", (entry["date"],)
        ).fetchone()
        if existing and existing["source"] == "manual":
            skipped_manual += 1
            continue

        conn.execute(
            """
            INSERT INTO nutrition_logs (date, calories, protein_g, carbs_g, fat_g, source, complete, updated_at)
            VALUES (?, ?, ?, ?, ?, 'myfitnesspal', ?, datetime('now'))
            ON CONFLICT(date) DO UPDATE SET
                calories=excluded.calories, protein_g=excluded.protein_g, carbs_g=excluded.carbs_g,
                fat_g=excluded.fat_g, source='myfitnesspal', complete=excluded.complete,
                updated_at=datetime('now')
            """,
            (
                entry["date"],
                entry["calories"],
                entry["protein_g"],
                entry["carbs_g"],
                entry["fat_g"],
                1 if entry["complete"] else 0,
            ),
        )
        synced += 1

    return {"days_synced": synced, "skipped_manual": skipped_manual}
