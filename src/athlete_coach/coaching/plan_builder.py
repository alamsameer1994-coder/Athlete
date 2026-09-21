"""Periodized plan scaffolding.

Deliberately does *not* try to hardcode exercise-science workout
prescriptions in Python — that's what the AI coach (Claude, talking to
the athlete) is for. What this module provides is the *structure* a real
coach works within:

- block periodization (base -> build -> peak -> taper, with a deload
  every 4th week) between "today" (or a chosen start) and a race date
- a sensible weekly volume ramp (progressive overload, capped step size,
  deload cutback) from current to peak weekly hours
- a lightweight default session skeleton per week that Claude is expected
  to flesh out / replace via the `upsert_planned_workout` tool based on
  the athlete's actual history, preferences, and constraints

Taper length and block proportions follow common endurance-coaching
heuristics (e.g. Daniels/Friel-style linear periodization); treat the
numbers as reasonable defaults, not gospel — the AI coach can and should
adjust them in conversation.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

TAPER_WEEKS_BY_RACE_TYPE = {
    "5k": 1,
    "10k": 1,
    "half_marathon": 2,
    "marathon": 3,
    "70.3": 2,
    "ironman": 3,
    "road_race": 1,
    "criterium": 1,
    "gran_fondo": 1,
    "olympic_triathlon": 2,
}
DEFAULT_TAPER_WEEKS = 2


def _monday_on_or_before(d: date) -> date:
    return d - timedelta(days=d.weekday())


@dataclass
class WeekPlanSpec:
    week_start: date
    block_type: str  # base | build | peak | taper | recovery
    target_weekly_hours: float
    target_weekly_tss: float


def compute_block_structure(
    start_date: date,
    race_date: date,
    race_type: str,
    current_weekly_hours: float,
    peak_weekly_hours: float,
    avg_tss_per_hour: float = 60.0,
) -> list[WeekPlanSpec]:
    start_monday = _monday_on_or_before(start_date)
    race_monday = _monday_on_or_before(race_date)
    total_weeks = max(1, (race_monday - start_monday).days // 7 + 1)

    taper_weeks = min(
        TAPER_WEEKS_BY_RACE_TYPE.get(race_type, DEFAULT_TAPER_WEEKS), max(1, total_weeks - 2)
    )
    remaining = total_weeks - taper_weeks
    peak_weeks = min(2, remaining) if remaining > 2 else max(0, remaining - 1)
    remaining -= peak_weeks
    build_weeks = remaining // 2
    base_weeks = remaining - build_weeks

    block_sequence: list[str] = (
        ["base"] * base_weeks + ["build"] * build_weeks + ["peak"] * peak_weeks + ["taper"] * taper_weeks
    )
    # Deload every 4th week within base/build (not during peak/taper).
    for i in range(3, len(block_sequence), 4):
        if block_sequence[i] in ("base", "build"):
            block_sequence[i] = "recovery"

    ramp_weeks = [b for b in block_sequence if b in ("base", "build")]
    n_ramp = max(1, len(ramp_weeks))
    hour_step = (peak_weekly_hours - current_weekly_hours) / n_ramp if n_ramp else 0

    specs: list[WeekPlanSpec] = []
    hours_cursor = current_weekly_hours
    ramp_idx = 0
    for i, block in enumerate(block_sequence):
        week_start = start_monday + timedelta(weeks=i)
        if block in ("base", "build"):
            hours_cursor = current_weekly_hours + hour_step * ramp_idx
            ramp_idx += 1
            hours = round(hours_cursor, 1)
        elif block == "recovery":
            hours = round(hours_cursor * 0.65, 1)
        elif block == "peak":
            hours = round(peak_weekly_hours * 0.9, 1)
        else:  # taper
            weeks_to_race = taper_weeks - (i - (len(block_sequence) - taper_weeks))
            fraction = 0.75 if weeks_to_race > 1 else 0.5
            hours = round(peak_weekly_hours * fraction, 1)

        intensity_multiplier = {
            "base": 0.9,
            "build": 1.0,
            "recovery": 0.8,
            "peak": 1.15,
            "taper": 1.0,
        }[block]
        tss = round(hours * avg_tss_per_hour * intensity_multiplier, 0)

        specs.append(WeekPlanSpec(week_start=week_start, block_type=block, target_weekly_hours=hours, target_weekly_tss=tss))

    return specs


DEFAULT_SESSION_SKELETON = {
    "base": [
        (0, "run", "Easy run", "Z1-Z2", 0.20),
        (1, "strength", "Strength: full body", None, None),
        (2, "run", "Easy run + strides", "Z2 + 6x20s strides", 0.20),
        (4, "run", "Long run", "Z2, conversational", 0.40),
        (5, "run", "Recovery run", "Z1", 0.20),
    ],
    "build": [
        (0, "run", "Easy run", "Z1-Z2", 0.15),
        (1, "strength", "Strength: full body", None, None),
        (2, "run", "Tempo / threshold intervals", "Z3-Z4", 0.20),
        (3, "run", "Easy run", "Z2", 0.15),
        (4, "run", "Long run with pickups", "Z2 + race-pace segments", 0.35),
        (5, "run", "Recovery run", "Z1", 0.15),
    ],
    "peak": [
        (0, "run", "Easy run", "Z1-Z2", 0.15),
        (1, "strength", "Strength: maintenance (lighter)", None, None),
        (2, "run", "Race-pace intervals", "Z4-Z5, race-specific", 0.20),
        (4, "run", "Long run, race simulation", "includes goal-pace block", 0.35),
        (5, "run", "Easy shakeout", "Z1", 0.15),
        (3, "run", "Easy run", "Z2", 0.15),
    ],
    "taper": [
        (1, "run", "Short sharpening intervals", "Z4, low volume", 0.30),
        (3, "run", "Easy run", "Z2", 0.30),
        (5, "run", "Short shakeout + strides", "Z1-Z2 + 4x20s", 0.20),
        (6, "run", "Easy pre-race jog or rest", "Z1 / rest", 0.20),
    ],
    "recovery": [
        (0, "run", "Easy run", "Z1", 0.30),
        (2, "run", "Easy run", "Z1-Z2", 0.30),
        (4, "run", "Easy run or cross-train", "Z1-Z2", 0.40),
    ],
}


def default_sessions_for_week(spec: WeekPlanSpec) -> list[dict]:
    """A starter skeleton for a week; Claude should refine/replace these
    via `upsert_planned_workout` based on the athlete's real context."""
    template = DEFAULT_SESSION_SKELETON[spec.block_type]
    sessions = []
    for day_offset, sport, title, intensity, hour_fraction in template:
        d = spec.week_start + timedelta(days=day_offset)
        if sport == "strength":
            sessions.append(
                {
                    "date": d.isoformat(),
                    "sport": "strength",
                    "title": title,
                    "description": "Compound lifts + core; adjust load/reps to athlete's strength history.",
                    "target_type": "duration",
                    "target_value": 40,
                    "intensity": "moderate",
                }
            )
            continue
        minutes = round(spec.target_weekly_hours * 60 * hour_fraction)
        sessions.append(
            {
                "date": d.isoformat(),
                "sport": sport,
                "title": title,
                "description": f"Placeholder session for the '{spec.block_type}' block — refine based on athlete history.",
                "target_type": "duration",
                "target_value": minutes,
                "intensity": intensity,
            }
        )
    return sessions
