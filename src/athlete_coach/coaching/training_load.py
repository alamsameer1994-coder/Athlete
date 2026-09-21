"""Training load math: per-activity stress score, CTL/ATL/TSB, and rollups.

Pure functions, no I/O, so they're easy to unit test.

Model:
- If power data is available (cycling with a power meter): TSS via
  normalized-power-relative-intensity, the standard Coggan formula.
- Else if heart rate + HR zones are available: hrTSS approximation using
  average HR relative to threshold HR.
- Else if RPE (rate of perceived exertion, 1-10) is logged: duration-based
  estimate (RPE * duration_hours * 10), a common "sRPE" load proxy.
- Else: a conservative duration-only fallback (50 TSS/hour at "moderate").

CTL (Chronic Training Load, "fitness") = 42-day exponentially weighted
moving average of daily TSS.
ATL (Acute Training Load, "fatigue") = 7-day exponentially weighted
moving average of daily TSS.
TSB (Training Stress Balance, "form") = CTL - ATL (using yesterday's
CTL/ATL relative to today, per convention).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

CTL_DAYS = 42
ATL_DAYS = 7


def estimate_tss(
    *,
    duration_s: float | None,
    avg_power: float | None = None,
    normalized_power: float | None = None,
    ftp_watts: float | None = None,
    avg_hr: float | None = None,
    threshold_hr: float | None = None,
    rpe: float | None = None,
) -> tuple[float | None, str]:
    """Returns (tss, load_source). tss is None if nothing usable is present."""
    if not duration_s or duration_s <= 0:
        return None, "none"

    hours = duration_s / 3600.0

    if (normalized_power or avg_power) and ftp_watts:
        np = normalized_power or avg_power
        intensity_factor = np / ftp_watts
        tss = hours * (intensity_factor**2) * 100
        return round(tss, 1), "power"

    if avg_hr and threshold_hr:
        hr_ratio = min(avg_hr / threshold_hr, 1.15)
        tss = hours * (hr_ratio**2) * 100
        return round(tss, 1), "hr"

    if rpe:
        tss = rpe * hours * 10
        return round(tss, 1), "rpe"

    tss = hours * 50
    return round(tss, 1), "duration"


@dataclass
class DailyLoadPoint:
    day: date
    ctl: float
    atl: float
    tsb: float
    daily_tss: float


def compute_ctl_atl_series(
    daily_tss_by_date: dict[date, float],
    start: date,
    end: date,
    seed_ctl: float = 0.0,
    seed_atl: float = 0.0,
) -> list[DailyLoadPoint]:
    """Walk day-by-day from `start` to `end` inclusive, computing EWMA CTL/ATL.

    `daily_tss_by_date` should be pre-summed (multiple activities on the
    same day added together). Missing days count as 0 TSS (rest day).
    """
    ctl_alpha = 2 / (CTL_DAYS + 1)
    atl_alpha = 2 / (ATL_DAYS + 1)

    ctl = seed_ctl
    atl = seed_atl
    out: list[DailyLoadPoint] = []

    d = start
    while d <= end:
        today_tss = daily_tss_by_date.get(d, 0.0)
        ctl = ctl + ctl_alpha * (today_tss - ctl)
        atl = atl + atl_alpha * (today_tss - atl)
        tsb = ctl - atl
        out.append(DailyLoadPoint(day=d, ctl=round(ctl, 1), atl=round(atl, 1), tsb=round(tsb, 1), daily_tss=today_tss))
        d += timedelta(days=1)

    return out


def weekly_totals(daily_tss_by_date: dict[date, float], week_start: date) -> float:
    return sum(
        daily_tss_by_date.get(week_start + timedelta(days=i), 0.0) for i in range(7)
    )
