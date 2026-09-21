"""Pull activities, daily wellness metrics, and body weight from Garmin
Connect and upsert into the local DB. Activities that look like a
duplicate of an already-synced Strava activity (same day, same sport,
same ~duration — common since most Garmin devices auto-upload to Strava)
are kept but flagged via `duplicate_of` so load calculations don't
double-count them.
"""
from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta

from athlete_coach.coaching.training_load import estimate_tss
from athlete_coach.db import dumps, get_setting
from athlete_coach.garmin_client import GarminClient

SPORT_MAP = {
    "running": "run",
    "trail_running": "run",
    "treadmill_running": "run",
    "cycling": "bike",
    "road_biking": "bike",
    "mountain_biking": "bike",
    "indoor_cycling": "bike",
    "virtual_ride": "bike",
    "lap_swimming": "swim",
    "open_water_swimming": "swim",
    "strength_training": "strength",
    "yoga": "mobility",
    "walking": "walk",
    "hiking": "hike",
}


def _match_key(start_time: str, sport: str, duration_s: float | None) -> str:
    day = start_time[:10]
    bucket = int((duration_s or 0) // 300)
    return f"{day}:{sport}:{bucket}"


def sync_garmin_activities(conn: sqlite3.Connection, days_back: int = 90) -> dict[str, int]:
    client = GarminClient()
    if not client.authorized:
        return {"error": "not_authorized", "inserted": 0, "updated": 0}

    cutoff = datetime.now() - timedelta(days=days_back)
    activities = client.get_activities(0, 200)

    ftp = get_setting(conn, "ftp_watts")
    threshold_hr = get_setting(conn, "threshold_hr")
    ftp_watts = float(ftp) if ftp else None
    threshold_hr_val = float(threshold_hr) if threshold_hr else None

    inserted = 0
    updated = 0
    skipped_old = 0
    for a in activities:
        start_time = a.get("startTimeLocal")
        if not start_time:
            continue
        start_dt = datetime.fromisoformat(start_time.replace(" ", "T"))
        if start_dt < cutoff:
            skipped_old += 1
            continue

        type_key = (a.get("activityType") or {}).get("typeKey", "other")
        sport = SPORT_MAP.get(type_key, type_key)
        duration_s = a.get("duration")
        match_key = _match_key(start_time, sport, duration_s)

        dup = conn.execute(
            "SELECT id FROM activities WHERE match_key = ? AND source = 'strava'",
            (match_key,),
        ).fetchone()
        duplicate_of = dup["id"] if dup else None

        tss, load_source = estimate_tss(
            duration_s=duration_s,
            avg_power=a.get("averagePower"),
            normalized_power=a.get("normPower"),
            ftp_watts=ftp_watts,
            avg_hr=a.get("averageHR"),
            threshold_hr=threshold_hr_val,
        )

        row_id = f"garmin:{a['activityId']}"
        existing = conn.execute("SELECT id FROM activities WHERE id = ?", (row_id,)).fetchone()
        conn.execute(
            """
            INSERT INTO activities (
                id, source, external_id, sport, name, start_time, duration_s,
                moving_time_s, distance_m, elevation_gain_m, avg_hr, max_hr,
                avg_power, normalized_power, avg_cadence, calories, tss,
                load_source, duplicate_of, match_key, raw_json
            ) VALUES (?, 'garmin', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name=excluded.name, duration_s=excluded.duration_s,
                distance_m=excluded.distance_m, elevation_gain_m=excluded.elevation_gain_m,
                avg_hr=excluded.avg_hr, max_hr=excluded.max_hr, avg_power=excluded.avg_power,
                normalized_power=excluded.normalized_power, calories=excluded.calories,
                tss=excluded.tss, load_source=excluded.load_source,
                duplicate_of=excluded.duplicate_of, match_key=excluded.match_key,
                raw_json=excluded.raw_json
            """,
            (
                row_id,
                str(a["activityId"]),
                sport,
                a.get("activityName"),
                start_time,
                duration_s,
                a.get("movingDuration") or duration_s,
                a.get("distance"),
                a.get("elevationGain"),
                a.get("averageHR"),
                a.get("maxHR"),
                a.get("averagePower"),
                a.get("normPower"),
                a.get("averageRunningCadenceInStepsPerMinute") or a.get("averageBikingCadenceInRevPerMinute"),
                a.get("calories"),
                tss,
                load_source,
                duplicate_of,
                match_key,
                dumps(a),
            ),
        )
        if existing:
            updated += 1
        else:
            inserted += 1

    return {"inserted": inserted, "updated": updated, "skipped_old": skipped_old}


def sync_garmin_daily_metrics(conn: sqlite3.Connection, days_back: int = 30) -> dict[str, int]:
    client = GarminClient()
    if not client.authorized:
        return {"error": "not_authorized", "days_synced": 0}

    today = date.today()
    synced = 0
    for i in range(days_back):
        d = today - timedelta(days=i)
        metrics = client.get_daily_metrics(d)
        conn.execute(
            """
            INSERT INTO daily_metrics (
                date, resting_hr, hrv_ms, body_battery_max, body_battery_min,
                sleep_score, sleep_duration_s, stress_avg, steps, source, raw_json, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'garmin', ?, datetime('now'))
            ON CONFLICT(date) DO UPDATE SET
                resting_hr=excluded.resting_hr, hrv_ms=excluded.hrv_ms,
                body_battery_max=excluded.body_battery_max, body_battery_min=excluded.body_battery_min,
                sleep_score=excluded.sleep_score, sleep_duration_s=excluded.sleep_duration_s,
                stress_avg=excluded.stress_avg, steps=excluded.steps, source='garmin',
                raw_json=excluded.raw_json, updated_at=datetime('now')
            """,
            (
                d.isoformat(),
                metrics.get("resting_hr"),
                metrics.get("hrv_ms"),
                metrics.get("body_battery_max"),
                metrics.get("body_battery_min"),
                metrics.get("sleep_score"),
                metrics.get("sleep_duration_s"),
                metrics.get("stress_avg"),
                metrics.get("steps"),
                dumps(metrics.get("raw", {})),
            ),
        )
        synced += 1
    return {"days_synced": synced}


def sync_garmin_body(conn: sqlite3.Connection, days_back: int = 30) -> dict[str, int]:
    client = GarminClient()
    if not client.authorized:
        return {"error": "not_authorized", "entries": 0}

    end = date.today()
    start = end - timedelta(days=days_back)
    weigh_ins = client.get_weigh_ins(start, end)

    count = 0
    for w in weigh_ins:
        ts = w.get("date") or w.get("calendarDate")
        if not ts:
            continue
        d = datetime.fromtimestamp(ts / 1000).date().isoformat() if isinstance(ts, (int, float)) else ts[:10]
        existing = conn.execute("SELECT source FROM body_logs WHERE date = ?", (d,)).fetchone()
        if existing and existing["source"] == "manual":
            continue  # never overwrite a manual entry
        weight_kg = w.get("weight")
        if weight_kg and weight_kg > 1000:  # Garmin returns grams sometimes
            weight_kg = weight_kg / 1000.0
        conn.execute(
            """
            INSERT INTO body_logs (date, weight_kg, body_fat_pct, source, updated_at)
            VALUES (?, ?, ?, 'garmin', datetime('now'))
            ON CONFLICT(date) DO UPDATE SET
                weight_kg=excluded.weight_kg, body_fat_pct=excluded.body_fat_pct,
                source='garmin', updated_at=datetime('now')
            """,
            (d, weight_kg, w.get("bodyFat")),
        )
        count += 1
    return {"entries": count}
