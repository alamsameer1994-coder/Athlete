"""Strength-training consistency tracking and phase-based guidance.

We don't have reliable cross-platform access to set/rep/load detail (the
public Strava API doesn't expose that, and Garmin's varies by how the
session was logged), so this focuses on what's reliably available:
session frequency, duration, and consistency — plus a reference table of
how strength focus should shift across an endurance periodization block,
for the AI coach to turn into concrete sets/reps/exercises in
conversation (informed by the athlete's own lifting history/experience).
"""
from __future__ import annotations

import sqlite3
from datetime import date, timedelta

PHASE_GUIDANCE = {
    "base": {
        "focus": "General strength & tendon/muscle adaptation",
        "rep_range": "10-15 reps, 2-3 sets",
        "frequency_per_week": 2,
        "notes": "Full-body compound lifts (squat, hinge, push, pull, core). Moderate load, higher volume.",
    },
    "build": {
        "focus": "Maximal strength",
        "rep_range": "4-6 reps, 3-5 sets",
        "frequency_per_week": 2,
        "notes": "Heavier compound lifts; keep sessions short and recover well — this is supporting endurance work, not competing with it.",
    },
    "peak": {
        "focus": "Power / rate of force development, maintenance",
        "rep_range": "3-5 reps, low volume, some explosive work",
        "frequency_per_week": 1,
        "notes": "Reduce volume sharply; goal is to maintain strength without adding fatigue before key sessions/races.",
    },
    "taper": {
        "focus": "Maintenance only",
        "rep_range": "light, 2-3 sets, short sessions",
        "frequency_per_week": 1,
        "notes": "Very light — just enough neuromuscular stimulus, no soreness risk this close to race day.",
    },
    "recovery": {
        "focus": "Mobility & light maintenance",
        "rep_range": "light, high rep, or skip",
        "frequency_per_week": 1,
        "notes": "Optional; prioritize recovery over stimulus during deload weeks.",
    },
}


def strength_consistency(conn: sqlite3.Connection, lookback_days: int = 28) -> dict:
    start = (date.today() - timedelta(days=lookback_days)).isoformat()
    rows = conn.execute(
        """
        SELECT date(start_time) as d, duration_s, name FROM activities
        WHERE sport = 'strength' AND duplicate_of IS NULL AND date(start_time) >= ?
        ORDER BY d
        """,
        (start,),
    ).fetchall()
    weeks = max(1, lookback_days / 7)
    total_minutes = sum((r["duration_s"] or 0) for r in rows) / 60
    return {
        "lookback_days": lookback_days,
        "session_count": len(rows),
        "sessions_per_week": round(len(rows) / weeks, 1),
        "avg_session_minutes": round(total_minutes / len(rows), 0) if rows else 0,
        "sessions": [dict(r) for r in rows],
    }


def phase_guidance(block_type: str) -> dict:
    return PHASE_GUIDANCE.get(block_type, PHASE_GUIDANCE["base"])
