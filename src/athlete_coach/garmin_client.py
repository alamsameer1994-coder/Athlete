"""Thin wrapper around the unofficial `garminconnect` library.

Garmin has no public personal-use API, so this uses your own Garmin
Connect login (email/password from env) via the `garminconnect` package,
which itself uses `garth` for session/token handling. The session token
is cached under ~/.athlete_coach/garmin_tokens so you don't re-login on
every sync.

NOTE: this depends on an unofficial, reverse-engineered client. Garmin
can change their backend at any time and break it; treat sync failures
as "try again later / update the garminconnect package", not a bug in
this repo.
"""
from __future__ import annotations

from datetime import date
from typing import Any

from athlete_coach.config import get_config


class GarminAuthError(RuntimeError):
    pass


class GarminClient:
    def __init__(self) -> None:
        self.cfg = get_config()
        self._api = None

    def _ensure_login(self):
        if self._api is not None:
            return self._api

        try:
            from garminconnect import Garmin
        except ImportError as e:  # pragma: no cover
            raise GarminAuthError(
                "garminconnect package not installed. Run `pip install garminconnect`."
            ) from e

        token_dir = str(self.cfg.home_dir / "garmin_tokens")
        api = Garmin(self.cfg.garmin_email, self.cfg.garmin_password)

        try:
            api.login(token_dir)
        except Exception:
            if not self.cfg.garmin_email or not self.cfg.garmin_password:
                raise GarminAuthError(
                    "No cached Garmin session and GARMIN_EMAIL/GARMIN_PASSWORD not set."
                )
            api.login()
            try:
                api.garth.dump(token_dir)
            except Exception:
                pass

        self._api = api
        return api

    @property
    def authorized(self) -> bool:
        try:
            self._ensure_login()
            return True
        except GarminAuthError:
            return False

    def get_activities(self, start: int = 0, limit: int = 100) -> list[dict[str, Any]]:
        api = self._ensure_login()
        return api.get_activities(start, limit)

    def get_daily_metrics(self, day: date) -> dict[str, Any]:
        api = self._ensure_login()
        iso = day.isoformat()
        out: dict[str, Any] = {"date": iso}

        def _safe(fn, *args):
            try:
                return fn(*args)
            except Exception:
                return None

        sleep = _safe(api.get_sleep_data, iso)
        if sleep:
            daily = sleep.get("dailySleepDTO", {}) if isinstance(sleep, dict) else {}
            out["sleep_score"] = (daily or {}).get("sleepScores", {}).get("overall", {}).get("value")
            out["sleep_duration_s"] = (daily or {}).get("sleepTimeSeconds")

        hr = _safe(api.get_heart_rates, iso)
        if hr:
            out["resting_hr"] = hr.get("restingHeartRate")

        hrv = _safe(api.get_hrv_data, iso)
        if hrv:
            summary = hrv.get("hrvSummary", {}) if isinstance(hrv, dict) else {}
            out["hrv_ms"] = summary.get("lastNightAvg")

        bb = _safe(api.get_body_battery, iso, iso)
        if bb and isinstance(bb, list) and bb:
            values = [p.get("bodyBatteryLevel") for p in bb[0].get("bodyBatteryValuesArray", []) if p]
            if values:
                out["body_battery_max"] = max(values)
                out["body_battery_min"] = min(values)

        stress = _safe(api.get_stress_data, iso)
        if stress:
            out["stress_avg"] = stress.get("avgStressLevel")

        steps = _safe(api.get_steps_data, iso)
        if steps and isinstance(steps, list):
            out["steps"] = sum(p.get("steps", 0) or 0 for p in steps)

        out["raw"] = {"sleep": sleep, "hr": hr, "hrv": hrv, "body_battery": bb, "stress": stress}
        return out

    def get_weigh_ins(self, start_date: date, end_date: date) -> list[dict[str, Any]]:
        api = self._ensure_login()
        try:
            data = api.get_weigh_ins(start_date.isoformat(), end_date.isoformat())
        except Exception:
            return []
        entries = data.get("dailyWeightSummaries", []) if isinstance(data, dict) else []
        out = []
        for day_summary in entries:
            for w in day_summary.get("allWeightMetrics", []):
                out.append(w)
        return out
