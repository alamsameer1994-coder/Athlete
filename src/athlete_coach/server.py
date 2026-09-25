"""MCP server: exposes the athlete-coach data + coaching tools to Claude.

Run directly (`athlete-coach`, after `pip install -e .`) or point a
Claude Desktop / Claude Code MCP config at this module. See README.md
for setup (Strava OAuth app, Garmin credentials, first sync).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer

from athlete_coach.coaching import nutrition, plan_adjuster, plan_builder, strength, training_load
from athlete_coach.db import get_conn, get_setting, init_db, set_setting
from athlete_coach.sync.garmin_sync import sync_garmin_activities, sync_garmin_body, sync_garmin_daily_metrics
from athlete_coach.sync.myfitnesspal_sync import sync_myfitnesspal_nutrition
from athlete_coach.sync.strava_sync import sync_strava_activities

mcp = MCPServer("athlete-coach")


# ---------------------------------------------------------------------------
# Sync
# ---------------------------------------------------------------------------

@mcp.tool()
def sync_strava(days_back: int = 90) -> dict:
    """Pull recent activities from Strava into the local database."""
    with get_conn() as conn:
        return sync_strava_activities(conn, days_back)


@mcp.tool()
def sync_garmin(days_back: int = 90, include_wellness: bool = True) -> dict:
    """Pull recent activities (and optionally sleep/HRV/body-battery/weight)
    from Garmin Connect into the local database."""
    with get_conn() as conn:
        result = {"activities": sync_garmin_activities(conn, days_back)}
        if include_wellness:
            result["daily_metrics"] = sync_garmin_daily_metrics(conn, min(days_back, 60))
            result["body"] = sync_garmin_body(conn, min(days_back, 60))
        return result


@mcp.tool()
def sync_myfitnesspal(days_back: int = 14) -> dict:
    """Pull logged nutrition (calories/macros) from MyFitnessPal into the
    local database. Never overwrites a day logged manually via log_nutrition.
    One request per day, so keep days_back modest — this is for catching up
    recent days, not a full history backfill."""
    with get_conn() as conn:
        return sync_myfitnesspal_nutrition(conn, days_back)


@mcp.tool()
def sync_all(days_back: int = 90) -> dict:
    """Sync Strava, Garmin (activities + wellness/body data), and MyFitnessPal
    (last 14 days of logged nutrition, regardless of days_back)."""
    with get_conn() as conn:
        strava = sync_strava_activities(conn, days_back)
        garmin_activities = sync_garmin_activities(conn, days_back)
        garmin_metrics = sync_garmin_daily_metrics(conn, min(days_back, 60))
        garmin_body = sync_garmin_body(conn, min(days_back, 60))
        myfitnesspal = sync_myfitnesspal_nutrition(conn, 14)
    return {
        "strava": strava,
        "garmin_activities": garmin_activities,
        "garmin_daily_metrics": garmin_metrics,
        "garmin_body": garmin_body,
        "myfitnesspal": myfitnesspal,
    }


# ---------------------------------------------------------------------------
# Settings (athlete profile used by TSS/nutrition math)
# ---------------------------------------------------------------------------

SETTINGS_KEYS = [
    "ftp_watts", "threshold_hr", "max_hr", "height_cm", "age", "sex",
    "nutrition_goal", "target_weekly_rate_pct",
]


@mcp.tool()
def get_settings() -> dict:
    """Get the athlete profile settings used for training-load and nutrition math
    (FTP, threshold HR, height, age, sex, nutrition goal, target weekly weight-change rate)."""
    with get_conn() as conn:
        return {k: get_setting(conn, k) for k in SETTINGS_KEYS}


@mcp.tool()
def update_settings(
    ftp_watts: float | None = None,
    threshold_hr: float | None = None,
    max_hr: float | None = None,
    height_cm: float | None = None,
    age: int | None = None,
    sex: str | None = None,
    nutrition_goal: str | None = None,
    target_weekly_rate_pct: float | None = None,
) -> dict:
    """Update athlete profile settings. Only provided fields are changed.
    sex: 'male' or 'female' (used for BMR calc). nutrition_goal: 'fat_loss' |
    'maintenance' | 'muscle_gain'. target_weekly_rate_pct: e.g. -0.5 for a
    0.5%-of-bodyweight-per-week loss target."""
    updates = {
        "ftp_watts": ftp_watts, "threshold_hr": threshold_hr, "max_hr": max_hr,
        "height_cm": height_cm, "age": age, "sex": sex,
        "nutrition_goal": nutrition_goal, "target_weekly_rate_pct": target_weekly_rate_pct,
    }
    with get_conn() as conn:
        for k, v in updates.items():
            if v is not None:
                set_setting(conn, k, str(v))
        return {k: get_setting(conn, k) for k in SETTINGS_KEYS}


# ---------------------------------------------------------------------------
# Activities / training load
# ---------------------------------------------------------------------------

@mcp.tool()
def list_recent_activities(days: int = 14) -> list[dict]:
    """List activities (Strava + Garmin, de-duplicated) from the last N days."""
    start = (date.today() - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, source, sport, name, start_time, duration_s, distance_m,
                   elevation_gain_m, avg_hr, avg_power, calories, tss, load_source
            FROM activities
            WHERE duplicate_of IS NULL AND date(start_time) >= ?
            ORDER BY start_time DESC
            """,
            (start,),
        ).fetchall()
        return [dict(r) for r in rows]


@mcp.tool()
def get_fitness_trend(days: int = 90) -> dict:
    """CTL (fitness), ATL (fatigue), TSB (form) time series over the last N days,
    plus the current snapshot and 7-day ramp rate."""
    with get_conn() as conn:
        end = date.today()
        start = end - timedelta(days=days)
        rows = conn.execute(
            "SELECT start_time, tss FROM activities WHERE duplicate_of IS NULL AND tss IS NOT NULL AND date(start_time) BETWEEN ? AND ?",
            ((start - timedelta(days=45)).isoformat(), end.isoformat()),
        ).fetchall()
        daily: dict[date, float] = {}
        for r in rows:
            d = date.fromisoformat(r["start_time"][:10])
            daily[d] = daily.get(d, 0.0) + (r["tss"] or 0.0)
        series = training_load.compute_ctl_atl_series(daily, start - timedelta(days=45), end)
        visible = [p for p in series if p.day >= start]
        snapshot = plan_adjuster.current_fitness_snapshot(conn, as_of=end)
    return {
        "snapshot": snapshot,
        "series": [
            {"date": p.day.isoformat(), "ctl": p.ctl, "atl": p.atl, "tsb": p.tsb, "daily_tss": p.daily_tss}
            for p in visible
        ],
    }


@mcp.tool()
def get_week_summary(week_start: str) -> dict:
    """Analyze a specific week (Monday date, e.g. '2025-06-02'): planned vs
    actual hours/TSS, session adherence, HRV/resting-HR/sleep trends, and
    weight change during the week."""
    with get_conn() as conn:
        return plan_adjuster.analyze_week(conn, date.fromisoformat(week_start))


# ---------------------------------------------------------------------------
# Race targets & plans
# ---------------------------------------------------------------------------

@mcp.tool()
def create_race_target(name: str, race_date: str, race_type: str, priority: str = "A", notes: str = "") -> dict:
    """Register a race/event target. race_type examples: 5k, 10k, half_marathon,
    marathon, 70.3, ironman, road_race, criterium, gran_fondo, olympic_triathlon
    (used to pick a sensible taper length; any string is accepted)."""
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO race_targets (name, race_date, race_type, priority, notes) VALUES (?, ?, ?, ?, ?)",
            (name, race_date, race_type, priority, notes),
        )
        return {"id": cur.lastrowid, "name": name, "race_date": race_date, "race_type": race_type}


@mcp.tool()
def list_race_targets() -> list[dict]:
    """List all registered race targets."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM race_targets ORDER BY race_date").fetchall()]


@mcp.tool()
def create_plan(
    name: str,
    end_date: str,
    current_weekly_hours: float,
    peak_weekly_hours: float,
    race_target_id: int | None = None,
    race_type: str = "road_race",
    start_date: str | None = None,
    avg_tss_per_hour: float = 60.0,
    sport_focus: str = "run",
) -> dict:
    """Build a new periodized training plan (base/build/peak/taper blocks,
    with a deload every 4th week) from `start_date` (default: today) through
    `end_date` (typically the race date), ramping weekly volume from
    current_weekly_hours to peak_weekly_hours. Writes week-by-week volume/TSS
    targets and a default session skeleton (refine sessions afterwards with
    `upsert_planned_workout` based on the athlete's real context)."""
    start = date.fromisoformat(start_date) if start_date else date.today()
    end = date.fromisoformat(end_date)
    if end <= start:
        return {"error": "end_date_not_after_start_date", "start_date": start.isoformat(), "end_date": end.isoformat()}

    specs = plan_builder.compute_block_structure(
        start, end, race_type, current_weekly_hours, peak_weekly_hours, avg_tss_per_hour
    )

    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO plans (race_target_id, name, start_date, end_date, goal_description) VALUES (?, ?, ?, ?, ?)",
            (race_target_id, name, start.isoformat(), end.isoformat(), f"Ramp {current_weekly_hours}h -> {peak_weekly_hours}h/wk for {race_type}"),
        )
        plan_id = cur.lastrowid

        week_ids = []
        for spec in specs:
            wcur = conn.execute(
                "INSERT INTO plan_weeks (plan_id, week_start_date, block_type, target_weekly_hours, target_weekly_tss) VALUES (?, ?, ?, ?, ?)",
                (plan_id, spec.week_start.isoformat(), spec.block_type, spec.target_weekly_hours, spec.target_weekly_tss),
            )
            week_id = wcur.lastrowid
            week_ids.append(week_id)
            if sport_focus in ("run", "bike", "triathlon", "general") or True:
                for s in plan_builder.default_sessions_for_week(spec):
                    conn.execute(
                        """
                        INSERT INTO planned_workouts (plan_week_id, date, sport, title, description, target_type, target_value, intensity)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (week_id, s["date"], s["sport"], s["title"], s["description"], s["target_type"], s["target_value"], s["intensity"]),
                    )

    return {"plan_id": plan_id, "weeks": len(specs), "start_date": start.isoformat(), "end_date": end.isoformat()}


@mcp.tool()
def list_plans() -> list[dict]:
    """List all training plans."""
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM plans ORDER BY start_date DESC").fetchall()]


@mcp.tool()
def get_plan_week(plan_id: int, week_start: str) -> dict:
    """Get a plan's week (block type, targets) and its planned sessions."""
    with get_conn() as conn:
        week = conn.execute(
            "SELECT * FROM plan_weeks WHERE plan_id = ? AND week_start_date = ?",
            (plan_id, week_start),
        ).fetchone()
        if not week:
            return {"error": "week_not_found"}
        sessions = conn.execute(
            "SELECT * FROM planned_workouts WHERE plan_week_id = ? ORDER BY date", (week["id"],)
        ).fetchall()
        return {"week": dict(week), "sessions": [dict(s) for s in sessions]}


@mcp.tool()
def upsert_planned_workout(
    plan_id: int,
    date_: str,
    sport: str,
    title: str,
    description: str = "",
    target_type: str = "duration",
    target_value: float | None = None,
    intensity: str = "",
    workout_id: int | None = None,
    status: str = "planned",
) -> dict:
    """Create or update a planned workout. Pass `workout_id` to update an
    existing session (e.g. after the coach decides to change it); omit it to
    create a new one for the plan_week matching `date_`'s Monday. This is how
    the AI coach should write real, specific session prescriptions on top of
    the default skeleton `create_plan` generates."""
    with get_conn() as conn:
        if workout_id:
            conn.execute(
                """
                UPDATE planned_workouts SET sport=?, title=?, description=?, target_type=?,
                    target_value=?, intensity=?, status=?, updated_at=datetime('now')
                WHERE id = ?
                """,
                (sport, title, description, target_type, target_value, intensity, status, workout_id),
            )
            return {"workout_id": workout_id, "updated": True}

        d = date.fromisoformat(date_)
        week_start = (d - timedelta(days=d.weekday())).isoformat()
        week = conn.execute(
            "SELECT id FROM plan_weeks WHERE plan_id = ? AND week_start_date = ?", (plan_id, week_start)
        ).fetchone()
        if not week:
            return {"error": "no_plan_week_for_date", "expected_week_start": week_start}
        cur = conn.execute(
            """
            INSERT INTO planned_workouts (plan_week_id, date, sport, title, description, target_type, target_value, intensity, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (week["id"], date_, sport, title, description, target_type, target_value, intensity, status),
        )
        return {"workout_id": cur.lastrowid, "created": True}


@mcp.tool()
def delete_planned_workout(workout_id: int) -> dict:
    """Delete a planned workout."""
    with get_conn() as conn:
        conn.execute("DELETE FROM planned_workouts WHERE id = ?", (workout_id,))
        return {"deleted": workout_id}


@mcp.tool()
def mark_workout_status(workout_id: int, status: str, linked_activity_id: str | None = None) -> dict:
    """Mark a planned workout as 'completed' | 'skipped' | 'modified' | 'planned',
    optionally linking it to the matching activity id (from list_recent_activities)."""
    with get_conn() as conn:
        conn.execute(
            "UPDATE planned_workouts SET status=?, linked_activity_id=?, updated_at=datetime('now') WHERE id=?",
            (status, linked_activity_id, workout_id),
        )
        return {"workout_id": workout_id, "status": status}


@mcp.tool()
def suggest_week_adjustment(upcoming_week_start: str) -> dict:
    """Compute fatigue/adherence/recovery signals (TSB, acute:chronic ratio,
    last week's adherence, sleep trend) and a rule-of-thumb suggested volume
    scale factor for the upcoming week. The AI coach should weigh this
    together with how the athlete says they're feeling before deciding
    whether to call apply_week_adjustment."""
    with get_conn() as conn:
        return plan_adjuster.suggest_adjustment(conn, date.fromisoformat(upcoming_week_start))


@mcp.tool()
def apply_week_adjustment(plan_week_id: int, scale: float, note: str = "") -> dict:
    """Scale a plan week's target hours/TSS by `scale` (e.g. 0.85 to cut 15%)
    and log the reasoning as a coaching note. Follow up with
    upsert_planned_workout calls to adjust the individual sessions to match."""
    with get_conn() as conn:
        return plan_adjuster.apply_week_adjustment(conn, plan_week_id, scale, note or None)


@mcp.tool()
def export_plan_ics(plan_id: int, out_path: str | None = None) -> dict:
    """Export a plan's sessions as an .ics calendar file (importable into
    TrainingPeaks, Google/Apple/Outlook Calendar, etc). Returns the file path."""
    with get_conn() as conn:
        plan = conn.execute("SELECT * FROM plans WHERE id = ?", (plan_id,)).fetchone()
        if not plan:
            return {"error": "plan_not_found"}
        sessions = conn.execute(
            """
            SELECT pw.* FROM planned_workouts pw
            JOIN plan_weeks plw ON plw.id = pw.plan_week_id
            WHERE plw.plan_id = ? ORDER BY pw.date
            """,
            (plan_id,),
        ).fetchall()

    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//athlete-coach//EN"]
    for s in sessions:
        day = s["date"].replace("-", "")
        desc = (s["description"] or "").replace("\n", "\\n")
        lines += [
            "BEGIN:VEVENT",
            f"UID:workout-{s['id']}@athlete-coach",
            f"DTSTART;VALUE=DATE:{day}",
            f"DTEND;VALUE=DATE:{day}",
            f"SUMMARY:{s['sport'].title()}: {s['title']}",
            f"DESCRIPTION:{desc} ({s['target_type']}: {s['target_value']}, {s['intensity']})",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")

    path = Path(out_path) if out_path else Path.home() / ".athlete_coach" / f"plan_{plan_id}.ics"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\r\n".join(lines))
    return {"path": str(path), "sessions_exported": len(sessions)}


# ---------------------------------------------------------------------------
# Body composition & nutrition
# ---------------------------------------------------------------------------

@mcp.tool()
def log_body_metrics(date_: str, weight_kg: float | None = None, body_fat_pct: float | None = None, notes: str = "") -> dict:
    """Manually log weight/body-fat for a date. Manual entries are never
    overwritten by a later Garmin sync."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO body_logs (date, weight_kg, body_fat_pct, source, notes, updated_at)
            VALUES (?, ?, ?, 'manual', ?, datetime('now'))
            ON CONFLICT(date) DO UPDATE SET
                weight_kg=COALESCE(excluded.weight_kg, body_logs.weight_kg),
                body_fat_pct=COALESCE(excluded.body_fat_pct, body_logs.body_fat_pct),
                source='manual', notes=excluded.notes, updated_at=datetime('now')
            """,
            (date_, weight_kg, body_fat_pct, notes),
        )
        return {"date": date_, "weight_kg": weight_kg, "body_fat_pct": body_fat_pct}


@mcp.tool()
def get_body_comp_trend(lookback_days: int = 28) -> dict:
    """Smoothed weight trend and weekly rate of change over the lookback window."""
    with get_conn() as conn:
        return nutrition.body_comp_trend(conn, lookback_days)


@mcp.tool()
def log_nutrition(date_: str, calories: float | None = None, protein_g: float | None = None,
                   carbs_g: float | None = None, fat_g: float | None = None, notes: str = "") -> dict:
    """Log actual intake for a date (for adherence tracking). Manual entries
    are never overwritten by a later MyFitnessPal sync."""
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO nutrition_logs (date, calories, protein_g, carbs_g, fat_g, source, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, 'manual', ?, datetime('now'))
            ON CONFLICT(date) DO UPDATE SET
                calories=excluded.calories, protein_g=excluded.protein_g, carbs_g=excluded.carbs_g,
                fat_g=excluded.fat_g, source='manual', notes=excluded.notes, updated_at=datetime('now')
            """,
            (date_, calories, protein_g, carbs_g, fat_g, notes),
        )
        return {"date": date_, "calories": calories}


@mcp.tool()
def get_nutrition_targets(goal: str | None = None, aggressiveness: str = "moderate", lookback_days: int = 14) -> dict:
    """Calorie/macro targets from BMR (Mifflin-St Jeor) + NEAT baseline + real
    logged training calories, plus how actual logged intake (manual or
    MyFitnessPal, over lookback_days) compares to that target. goal:
    'fat_loss' | 'maintenance' | 'muscle_gain' (defaults to the athlete's
    saved nutrition_goal setting). Requires height_cm, age, sex
    (update_settings) and a recent weight (log_body_metrics)."""
    with get_conn() as conn:
        height = get_setting(conn, "height_cm")
        age = get_setting(conn, "age")
        sex = get_setting(conn, "sex")
        goal = goal or get_setting(conn, "nutrition_goal", "maintenance")

        weight_row = conn.execute(
            "SELECT weight_kg FROM body_logs WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1"
        ).fetchone()

        missing = [
            name for name, val in [("height_cm", height), ("age", age), ("sex", sex)] if not val
        ]
        if not weight_row:
            missing.append("weight_kg (log_body_metrics)")
        if missing:
            return {"error": "missing_profile_data", "missing": missing}

        weight_kg = weight_row["weight_kg"]
        bmr = nutrition.bmr_mifflin_st_jeor(weight_kg, float(height), int(float(age)), sex)  # type: ignore[arg-type]
        tdee_info = nutrition.estimate_tdee(conn, bmr, lookback_days)
        targets = nutrition.calorie_and_macro_targets(weight_kg, tdee_info["tdee"], goal, aggressiveness)  # type: ignore[arg-type]
        actual = nutrition.intake_vs_target(conn, targets["target_calories"], targets["protein_g"], lookback_days)
        return {"weight_kg": weight_kg, "goal": goal, **tdee_info, **targets, "actual_intake": actual}


@mcp.tool()
def review_calorie_adherence(goal: str | None = None, target_weekly_rate_pct: float | None = None, lookback_days: int = 28) -> dict:
    """Compare actual weight-trend rate vs. target and suggest a calorie-target
    adjustment (adaptive-TDEE feedback loop). Falls back to saved settings for
    goal/target_weekly_rate_pct if not provided."""
    with get_conn() as conn:
        goal = goal or get_setting(conn, "nutrition_goal", "maintenance")
        if target_weekly_rate_pct is None:
            saved = get_setting(conn, "target_weekly_rate_pct")
            target_weekly_rate_pct = float(saved) if saved else (-0.5 if goal == "fat_loss" else (0.25 if goal == "muscle_gain" else 0.0))
        return nutrition.review_calorie_adherence(conn, goal, target_weekly_rate_pct, lookback_days)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Strength
# ---------------------------------------------------------------------------

@mcp.tool()
def get_strength_consistency(lookback_days: int = 28) -> dict:
    """Strength-session frequency/consistency over the lookback window."""
    with get_conn() as conn:
        return strength.strength_consistency(conn, lookback_days)


@mcp.tool()
def get_strength_phase_guidance(block_type: str) -> dict:
    """Reference strength-focus guidance (rep range, frequency, notes) for a
    given periodization block: base | build | peak | taper | recovery."""
    return strength.phase_guidance(block_type)


def main() -> None:
    init_db()
    mcp.run()


if __name__ == "__main__":
    main()
