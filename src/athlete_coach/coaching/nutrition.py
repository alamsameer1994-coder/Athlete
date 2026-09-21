"""Calorie/macro targets and body-composition trend tracking.

TDEE is estimated as: BMR * 1.2 (a sedentary NEAT baseline covering
non-exercise daily movement) + average daily training calories actually
logged from Strava/Garmin over the lookback window. This avoids the
usual "which activity multiplier am I" guesswork — the training energy
comes from real logged data instead of a category guess.

Calorie/macro numbers here are standard sports-nutrition heuristics
(Mifflin-St Jeor BMR, 1.6-2.2 g/kg protein, minimum fat floor). They are
a starting point for the AI coach to refine in conversation (e.g. the
athlete's actual hunger/energy/performance response), not a medical
prescription — flag aggressive deficits to the athlete rather than
silently applying them.
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Literal

Sex = Literal["male", "female"]
Goal = Literal["fat_loss", "maintenance", "muscle_gain"]
Aggressiveness = Literal["conservative", "moderate", "aggressive"]

_DEFICIT_BY_AGGRESSIVENESS = {"conservative": 0.10, "moderate": 0.20, "aggressive": 0.25}
_SURPLUS_BY_AGGRESSIVENESS = {"conservative": 0.05, "moderate": 0.10, "aggressive": 0.15}
_PROTEIN_G_PER_KG = {"fat_loss": 2.0, "maintenance": 1.7, "muscle_gain": 1.8}


def bmr_mifflin_st_jeor(weight_kg: float, height_cm: float, age: int, sex: Sex) -> float:
    base = 10 * weight_kg + 6.25 * height_cm - 5 * age
    return base + (5 if sex == "male" else -161)


def avg_daily_training_calories(conn: sqlite3.Connection, lookback_days: int = 14) -> float:
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    row = conn.execute(
        """
        SELECT COALESCE(SUM(calories), 0) as total, COUNT(DISTINCT date(start_time)) as days
        FROM activities WHERE duplicate_of IS NULL AND date(start_time) >= ? AND calories IS NOT NULL
        """,
        (start,),
    ).fetchone()
    if not row or not row["total"]:
        return 0.0
    return row["total"] / lookback_days


def estimate_tdee(conn: sqlite3.Connection, bmr: float, lookback_days: int = 14) -> dict:
    training_cals = avg_daily_training_calories(conn, lookback_days)
    neat_baseline = bmr * 1.2
    return {
        "bmr": round(bmr, 0),
        "neat_baseline": round(neat_baseline, 0),
        "avg_daily_training_calories": round(training_cals, 0),
        "tdee": round(neat_baseline + training_cals, 0),
    }


def calorie_and_macro_targets(
    weight_kg: float,
    tdee: float,
    goal: Goal,
    aggressiveness: Aggressiveness = "moderate",
) -> dict:
    if goal == "fat_loss":
        pct = _DEFICIT_BY_AGGRESSIVENESS[aggressiveness]
        target_calories = tdee * (1 - pct)
        floor = tdee * 0.75  # avoid excessive deficits for an active athlete
        target_calories = max(target_calories, floor)
        delta_note = f"-{round(tdee - target_calories)} kcal/day deficit ({aggressiveness})"
    elif goal == "muscle_gain":
        pct = _SURPLUS_BY_AGGRESSIVENESS[aggressiveness]
        target_calories = tdee * (1 + pct)
        delta_note = f"+{round(target_calories - tdee)} kcal/day surplus ({aggressiveness})"
    else:
        target_calories = tdee
        delta_note = "at maintenance"

    protein_g = _PROTEIN_G_PER_KG[goal] * weight_kg
    fat_g = max(0.6 * weight_kg, target_calories * 0.20 / 9)
    protein_cals = protein_g * 4
    fat_cals = fat_g * 9
    carb_cals = max(target_calories - protein_cals - fat_cals, 0)
    carbs_g = carb_cals / 4

    return {
        "target_calories": round(target_calories),
        "delta_note": delta_note,
        "protein_g": round(protein_g),
        "fat_g": round(fat_g),
        "carbs_g": round(carbs_g),
    }


def body_comp_trend(conn: sqlite3.Connection, lookback_days: int = 28) -> dict:
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        "SELECT date, weight_kg, body_fat_pct FROM body_logs WHERE date >= ? AND weight_kg IS NOT NULL ORDER BY date",
        (start,),
    ).fetchall()
    if len(rows) < 2:
        return {"data_points": len(rows), "message": "Not enough weigh-ins to compute a trend yet."}

    weights = [r["weight_kg"] for r in rows]
    alpha = 0.25
    ema = [weights[0]]
    for w in weights[1:]:
        ema.append(ema[-1] + alpha * (w - ema[-1]))

    days_span = (date.fromisoformat(rows[-1]["date"]) - date.fromisoformat(rows[0]["date"])).days or 1
    total_change = ema[-1] - ema[0]
    weekly_rate_kg = total_change / days_span * 7
    weekly_rate_pct = weekly_rate_kg / weights[0] * 100

    fat_points = [(r["date"], r["body_fat_pct"]) for r in rows if r["body_fat_pct"] is not None]

    return {
        "data_points": len(rows),
        "start_weight_kg": round(weights[0], 1),
        "latest_weight_kg": round(weights[-1], 1),
        "smoothed_start_kg": round(ema[0], 1),
        "smoothed_latest_kg": round(ema[-1], 1),
        "weekly_rate_kg": round(weekly_rate_kg, 2),
        "weekly_rate_pct": round(weekly_rate_pct, 2),
        "body_fat_pct_points": fat_points,
    }


def review_calorie_adherence(
    conn: sqlite3.Connection, goal: Goal, target_weekly_rate_pct: float, lookback_days: int = 28
) -> dict:
    """Compares actual weight-trend rate to the target rate and suggests a
    calorie-target adjustment (adaptive-TDEE style feedback loop)."""
    trend = body_comp_trend(conn, lookback_days)
    if trend.get("data_points", 0) < 2:
        return {**trend, "suggested_calorie_adjustment_pct": 0, "reason": "insufficient_data"}

    actual = trend["weekly_rate_pct"]
    diff = actual - target_weekly_rate_pct

    if goal == "fat_loss":
        if diff > 0.15:  # losing slower than target (less negative)
            adjustment = -0.05
            reason = f"Losing {actual}%/wk vs target {target_weekly_rate_pct}%/wk — tighten intake ~5%."
        elif diff < -0.25:  # losing much faster than target
            adjustment = 0.05
            reason = f"Losing {actual}%/wk, faster than target {target_weekly_rate_pct}%/wk — ease deficit to protect performance/muscle."
        else:
            adjustment = 0.0
            reason = "On track — no change needed."
    elif goal == "muscle_gain":
        if diff < -0.15:
            adjustment = 0.05
            reason = f"Gaining {actual}%/wk, below target {target_weekly_rate_pct}%/wk — increase surplus ~5%."
        elif diff > 0.25:
            adjustment = -0.05
            reason = f"Gaining {actual}%/wk, faster than target (likely excess fat gain) — trim surplus ~5%."
        else:
            adjustment = 0.0
            reason = "On track — no change needed."
    else:
        adjustment = -0.03 if abs(actual) > 0.25 else 0.0
        reason = "Maintenance goal: trend should hover near 0%/wk." if adjustment else "Weight stable — on track."

    return {**trend, "suggested_calorie_adjustment_pct": adjustment, "reason": reason}
