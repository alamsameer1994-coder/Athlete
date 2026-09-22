"""Local web dashboard for athlete-coach.

Reuses the same SQLite DB and coaching modules as the MCP server — this
is a read-mostly view onto your data (plus a "Sync now" action), not a
separate source of truth. Run with `athlete-coach-web` (requires the
`web` extra: `pip install -e ".[web]"`).
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from athlete_coach.coaching import nutrition, plan_adjuster, strength, training_load
from athlete_coach.config import get_config
from athlete_coach.db import get_conn, get_setting, init_db
from athlete_coach.sync.garmin_sync import sync_garmin_activities, sync_garmin_body, sync_garmin_daily_metrics
from athlete_coach.sync.strava_sync import sync_strava_activities

BASE_DIR = Path(__file__).parent

init_db()

app = FastAPI(title="Athlete Coach")
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
templates = Jinja2Templates(directory=BASE_DIR / "templates")

SETTINGS_KEYS = [
    "ftp_watts", "threshold_hr", "max_hr", "height_cm", "age", "sex",
    "nutrition_goal", "target_weekly_rate_pct",
]


def _monday(d: date) -> date:
    return d - timedelta(days=d.weekday())


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "dashboard.html", {})


@app.get("/api/dashboard")
def api_dashboard() -> dict:
    today = date.today()
    week_start = _monday(today)

    with get_conn() as conn:
        settings = {k: get_setting(conn, k) for k in SETTINGS_KEYS}

        trend_start = today - timedelta(days=90)
        load_lookback = trend_start - timedelta(days=45)
        rows = conn.execute(
            """
            SELECT start_time, tss FROM activities
            WHERE duplicate_of IS NULL AND tss IS NOT NULL AND date(start_time) BETWEEN ? AND ?
            """,
            (load_lookback.isoformat(), today.isoformat()),
        ).fetchall()
        daily: dict[date, float] = {}
        for r in rows:
            d = date.fromisoformat(r["start_time"][:10])
            daily[d] = daily.get(d, 0.0) + (r["tss"] or 0.0)
        series = training_load.compute_ctl_atl_series(daily, load_lookback, today)
        visible = [p for p in series if p.day >= trend_start]
        fitness_series = [
            {"date": p.day.isoformat(), "ctl": p.ctl, "atl": p.atl, "tsb": p.tsb} for p in visible
        ]
        snapshot = plan_adjuster.current_fitness_snapshot(conn, as_of=today)

        activities = conn.execute(
            """
            SELECT id, source, sport, name, start_time, duration_s, distance_m, avg_hr, calories, tss
            FROM activities WHERE duplicate_of IS NULL AND date(start_time) >= ?
            ORDER BY start_time DESC LIMIT 20
            """,
            ((today - timedelta(days=21)).isoformat(),),
        ).fetchall()

        plan = conn.execute(
            "SELECT * FROM plans WHERE status='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        week = None
        sessions: list = []
        if plan:
            week = conn.execute(
                "SELECT * FROM plan_weeks WHERE plan_id=? AND week_start_date=?",
                (plan["id"], week_start.isoformat()),
            ).fetchone()
            if week:
                sessions = conn.execute(
                    "SELECT * FROM planned_workouts WHERE plan_week_id=? ORDER BY date", (week["id"],)
                ).fetchall()

        races = conn.execute(
            "SELECT * FROM race_targets WHERE race_date >= ? ORDER BY race_date LIMIT 3", (today.isoformat(),)
        ).fetchall()

        body_trend = nutrition.body_comp_trend(conn, 60)
        body_series = conn.execute(
            "SELECT date, weight_kg, body_fat_pct FROM body_logs WHERE date >= ? ORDER BY date",
            ((today - timedelta(days=90)).isoformat(),),
        ).fetchall()

        nutrition_targets = None
        weight_row = conn.execute(
            "SELECT weight_kg FROM body_logs WHERE weight_kg IS NOT NULL ORDER BY date DESC LIMIT 1"
        ).fetchone()
        if weight_row and settings["height_cm"] and settings["age"] and settings["sex"]:
            bmr = nutrition.bmr_mifflin_st_jeor(
                weight_row["weight_kg"], float(settings["height_cm"]), int(float(settings["age"])), settings["sex"]
            )
            tdee_info = nutrition.estimate_tdee(conn, bmr, 14)
            goal = settings["nutrition_goal"] or "maintenance"
            targets = nutrition.calorie_and_macro_targets(weight_row["weight_kg"], tdee_info["tdee"], goal)
            nutrition_targets = {"weight_kg": weight_row["weight_kg"], "goal": goal, **tdee_info, **targets}

        strength_info = strength.strength_consistency(conn, 28)
        last_sync_row = conn.execute("SELECT MAX(created_at) as t FROM activities").fetchone()

    cfg = get_config()
    sync_status = {
        "strava_authorized": (cfg.home_dir / "strava_token.json").exists(),
        "garmin_configured": bool(cfg.garmin_email and cfg.garmin_password) or (cfg.home_dir / "garmin_tokens").exists(),
        "last_activity_synced_at": last_sync_row["t"] if last_sync_row else None,
    }

    return {
        "today": today.isoformat(),
        "week_start": week_start.isoformat(),
        "settings": settings,
        "fitness": {"snapshot": snapshot, "series": fitness_series},
        "activities": [dict(a) for a in activities],
        "plan": dict(plan) if plan else None,
        "week": dict(week) if week else None,
        "sessions": [dict(s) for s in sessions],
        "races": [dict(r) for r in races],
        "body_trend": body_trend,
        "body_series": [dict(b) for b in body_series],
        "nutrition_targets": nutrition_targets,
        "strength": strength_info,
        "sync_status": sync_status,
    }


@app.post("/api/sync")
def api_sync(days_back: int = 90) -> dict:
    with get_conn() as conn:
        strava = sync_strava_activities(conn, days_back)
        garmin_activities = sync_garmin_activities(conn, days_back)
        garmin_metrics = sync_garmin_daily_metrics(conn, min(days_back, 60))
        garmin_body = sync_garmin_body(conn, min(days_back, 60))
    return {
        "strava": strava,
        "garmin_activities": garmin_activities,
        "garmin_daily_metrics": garmin_metrics,
        "garmin_body": garmin_body,
    }


def main() -> None:
    import uvicorn

    uvicorn.run("athlete_coach.web.app:app", host="127.0.0.1", port=8787, reload=False)


if __name__ == "__main__":
    main()
