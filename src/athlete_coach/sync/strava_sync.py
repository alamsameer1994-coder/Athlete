"""Pull activities from Strava and upsert them into the local DB."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from athlete_coach.coaching.training_load import estimate_tss
from athlete_coach.db import dumps, get_setting
from athlete_coach.strava_client import StravaClient

SPORT_MAP = {
    "Run": "run",
    "TrailRun": "run",
    "VirtualRun": "run",
    "Ride": "bike",
    "VirtualRide": "bike",
    "GravelRide": "bike",
    "MountainBikeRide": "bike",
    "Swim": "swim",
    "WeightTraining": "strength",
    "Workout": "strength",
    "Crossfit": "strength",
    "Yoga": "mobility",
    "Walk": "walk",
    "Hike": "hike",
}


def _match_key(start_time: str, sport: str, duration_s: float | None) -> str:
    day = start_time[:10]
    bucket = int((duration_s or 0) // 300)  # 5-minute buckets
    return f"{day}:{sport}:{bucket}"


def sync_strava_activities(conn: sqlite3.Connection, days_back: int = 90) -> dict[str, int]:
    client = StravaClient()
    if not client.authorized:
        return {"error": "not_authorized", "inserted": 0, "updated": 0}

    after_epoch = int((datetime.now() - timedelta(days=days_back)).timestamp())
    activities = client.list_activities(after_epoch=after_epoch)

    ftp = get_setting(conn, "ftp_watts")
    threshold_hr = get_setting(conn, "threshold_hr")
    ftp_watts = float(ftp) if ftp else None
    threshold_hr_val = float(threshold_hr) if threshold_hr else None

    inserted = 0
    updated = 0
    for a in activities:
        sport = SPORT_MAP.get(a.get("type", ""), a.get("type", "other").lower())
        start_time = a.get("start_date_local", a.get("start_date"))
        duration_s = a.get("elapsed_time")
        tss, load_source = estimate_tss(
            duration_s=a.get("moving_time") or duration_s,
            avg_power=a.get("average_watts"),
            normalized_power=a.get("weighted_average_watts"),
            ftp_watts=ftp_watts,
            avg_hr=a.get("average_heartrate"),
            threshold_hr=threshold_hr_val,
        )
        row_id = f"strava:{a['id']}"
        existing = conn.execute("SELECT id FROM activities WHERE id = ?", (row_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO activities (
                id, source, external_id, sport, name, start_time, duration_s,
                moving_time_s, distance_m, elevation_gain_m, avg_hr, max_hr,
                avg_power, normalized_power, avg_cadence, calories, tss,
                load_source, match_key, raw_json
            ) VALUES (?, 'strava', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, duration_s=excluded.duration_s,
                moving_time_s=excluded.moving_time_s, distance_m=excluded.distance_m,
                elevation_gain_m=excluded.elevation_gain_m, avg_hr=excluded.avg_hr,
                max_hr=excluded.max_hr, avg_power=excluded.avg_power,
                normalized_power=excluded.normalized_power, avg_cadence=excluded.avg_cadence,
                calories=excluded.calories, tss=excluded.tss, load_source=excluded.load_source,
                match_key=excluded.match_key, raw_json=excluded.raw_json
            """,
            (
                row_id,
                str(a["id"]),
                sport,
                a.get("name"),
                start_time,
                duration_s,
                a.get("moving_time"),
                a.get("distance"),
                a.get("total_elevation_gain"),
                a.get("average_heartrate"),
                a.get("max_heartrate"),
                a.get("average_watts"),
                a.get("weighted_average_watts"),
                a.get("average_cadence"),
                a.get("calories"),
                tss,
                load_source,
                _match_key(start_time, sport, duration_s),
                dumps(a),
            ),
        )
        if existing:
            updated += 1
        else:
            inserted += 1

    return {"inserted": inserted, "updated": updated, "total_fetched": len(activities)}
