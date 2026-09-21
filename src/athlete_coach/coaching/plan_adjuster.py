"""Compare actual training/recovery data against the plan and surface the
signals a coach would use to decide whether to adjust upcoming weeks.

Like plan_builder, this deliberately stops short of *deciding* the
adjustment in code — it computes the numbers (TSB, ramp rate, adherence,
recovery trend) and returns them plus a rule-of-thumb suggested volume
scale factor. The AI coach uses this, plus the conversation with the
athlete (how they're feeling, life stress, upcoming travel, etc.), to
actually decide what to change, then calls `apply_week_adjustment` /
`upsert_planned_workout` to write it.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

from athlete_coach.coaching.training_load import compute_ctl_atl_series


def _non_duplicate_daily_tss(conn: sqlite3.Connection, start: date, end: date) -> dict[date, float]:
    rows = conn.execute(
        """
        SELECT start_time, tss FROM activities
        WHERE duplicate_of IS NULL AND tss IS NOT NULL
        AND date(start_time) BETWEEN ? AND ?
        """,
        (start.isoformat(), end.isoformat()),
    ).fetchall()
    out: dict[date, float] = {}
    for r in rows:
        d = date.fromisoformat(r["start_time"][:10])
        out[d] = out.get(d, 0.0) + (r["tss"] or 0.0)
    return out


def current_fitness_snapshot(conn: sqlite3.Connection, as_of: date | None = None, lookback_days: int = 120) -> dict:
    as_of = as_of or date.today()
    start = as_of - timedelta(days=lookback_days)
    daily = _non_duplicate_daily_tss(conn, start, as_of)
    series = compute_ctl_atl_series(daily, start, as_of)
    latest = series[-1]
    week_ago = series[-8] if len(series) >= 8 else series[0]
    return {
        "date": latest.day.isoformat(),
        "ctl": latest.ctl,
        "atl": latest.atl,
        "tsb": latest.tsb,
        "ctl_7d_ago": week_ago.ctl,
        "ramp_rate_ctl_per_week": round(latest.ctl - week_ago.ctl, 1),
        "acute_chronic_ratio": round(latest.atl / latest.ctl, 2) if latest.ctl > 1e-6 else None,
    }


def analyze_week(conn: sqlite3.Connection, week_start: date) -> dict:
    week_end = week_start + timedelta(days=6)

    planned = conn.execute(
        """
        SELECT pw.*, plw.block_type, plw.target_weekly_hours, plw.target_weekly_tss
        FROM planned_workouts pw
        JOIN plan_weeks plw ON plw.id = pw.plan_week_id
        WHERE pw.date BETWEEN ? AND ?
        ORDER BY pw.date
        """,
        (week_start.isoformat(), week_end.isoformat()),
    ).fetchall()

    actual = conn.execute(
        """
        SELECT * FROM activities
        WHERE duplicate_of IS NULL AND date(start_time) BETWEEN ? AND ?
        ORDER BY start_time
        """,
        (week_start.isoformat(), week_end.isoformat()),
    ).fetchall()

    metrics = conn.execute(
        "SELECT * FROM daily_metrics WHERE date BETWEEN ? AND ? ORDER BY date",
        (week_start.isoformat(), week_end.isoformat()),
    ).fetchall()

    body = conn.execute(
        "SELECT * FROM body_logs WHERE date BETWEEN ? AND ? ORDER BY date",
        (week_start.isoformat(), week_end.isoformat()),
    ).fetchall()

    planned_completed = sum(1 for p in planned if p["status"] == "completed")
    planned_total = len(planned)
    actual_hours = sum((a["duration_s"] or 0) for a in actual) / 3600.0
    actual_tss = sum((a["tss"] or 0) for a in actual)
    target_hours = planned[0]["target_weekly_hours"] if planned else None
    target_tss = planned[0]["target_weekly_tss"] if planned else None

    hrv_values = [m["hrv_ms"] for m in metrics if m["hrv_ms"] is not None]
    rhr_values = [m["resting_hr"] for m in metrics if m["resting_hr"] is not None]
    sleep_values = [m["sleep_duration_s"] for m in metrics if m["sleep_duration_s"] is not None]

    return {
        "week_start": week_start.isoformat(),
        "week_end": week_end.isoformat(),
        "block_type": planned[0]["block_type"] if planned else None,
        "planned_sessions": planned_total,
        "planned_sessions_marked_completed": planned_completed,
        "actual_activity_count": len(actual),
        "actual_hours": round(actual_hours, 1),
        "target_hours": target_hours,
        "actual_tss": round(actual_tss, 1),
        "target_tss": target_tss,
        "adherence_pct": round(100 * actual_hours / target_hours, 0) if target_hours else None,
        "avg_hrv_ms": round(sum(hrv_values) / len(hrv_values), 1) if hrv_values else None,
        "avg_resting_hr": round(sum(rhr_values) / len(rhr_values), 1) if rhr_values else None,
        "avg_sleep_hours": round(sum(sleep_values) / len(sleep_values) / 3600, 1) if sleep_values else None,
        "weight_change_kg": (
            round(body[-1]["weight_kg"] - body[0]["weight_kg"], 2)
            if len(body) >= 2 and body[0]["weight_kg"] and body[-1]["weight_kg"]
            else None
        ),
        "activities": [dict(a) for a in actual],
        "planned": [dict(p) for p in planned],
    }


def suggest_adjustment(conn: sqlite3.Connection, upcoming_week_start: date) -> dict:
    """Rule-of-thumb suggestion only — the AI coach makes the final call."""
    fitness = current_fitness_snapshot(conn, as_of=upcoming_week_start - timedelta(days=1))
    last_week = analyze_week(conn, upcoming_week_start - timedelta(days=7))

    scale = 1.0
    reasons = []

    tsb = fitness["tsb"]
    if tsb is not None:
        if tsb < -25:
            scale -= 0.15
            reasons.append(f"TSB is very negative ({tsb}) — high fatigue relative to fitness, back off.")
        elif tsb > 10:
            scale += 0.05
            reasons.append(f"TSB is positive ({tsb}) — fresh, some room to push.")

    acr = fitness["acute_chronic_ratio"]
    if acr is not None and acr > 1.5:
        scale -= 0.1
        reasons.append(f"Acute:chronic load ratio is high ({acr}) — injury-risk zone, reduce ramp.")

    adherence = last_week.get("adherence_pct")
    if adherence is not None:
        if adherence < 60:
            scale -= 0.1
            reasons.append(f"Only {adherence}% of last week's planned hours completed — consider repeating the block rather than progressing.")
        elif adherence > 110:
            reasons.append(f"Exceeded last week's target ({adherence}%) — make sure that was intentional, not overreaching.")

    if last_week.get("avg_hrv_ms") is not None and last_week.get("avg_sleep_hours") is not None:
        if last_week["avg_sleep_hours"] < 6.5:
            scale -= 0.05
            reasons.append(f"Average sleep last week was low ({last_week['avg_sleep_hours']}h) — prioritize recovery.")

    scale = max(0.6, min(1.15, round(scale, 2)))
    if not reasons:
        reasons.append("No strong fatigue/adherence signals — proceed with the plan as scheduled.")

    return {
        "upcoming_week_start": upcoming_week_start.isoformat(),
        "fitness_snapshot": fitness,
        "last_week_summary": {k: last_week[k] for k in (
            "actual_hours", "target_hours", "adherence_pct", "avg_hrv_ms",
            "avg_resting_hr", "avg_sleep_hours",
        )},
        "suggested_volume_scale": scale,
        "reasons": reasons,
    }


def apply_week_adjustment(conn: sqlite3.Connection, plan_week_id: int, scale: float, note: str | None = None) -> dict:
    row = conn.execute("SELECT * FROM plan_weeks WHERE id = ?", (plan_week_id,)).fetchone()
    if not row:
        return {"error": "plan_week_not_found"}
    new_hours = round((row["target_weekly_hours"] or 0) * scale, 1)
    new_tss = round((row["target_weekly_tss"] or 0) * scale, 0)
    conn.execute(
        "UPDATE plan_weeks SET target_weekly_hours = ?, target_weekly_tss = ? WHERE id = ?",
        (new_hours, new_tss, plan_week_id),
    )
    conn.execute(
        "INSERT INTO coaching_notes (category, note) VALUES ('training', ?)",
        (note or f"Scaled week {row['week_start_date']} by {scale} -> {new_hours}h / {new_tss} TSS",),
    )
    return {"plan_week_id": plan_week_id, "new_target_hours": new_hours, "new_target_tss": new_tss}
