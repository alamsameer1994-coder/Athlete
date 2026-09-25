"""Thin wrapper around the unofficial `myfitnesspal` library.

MyFitnessPal shut down its public developer API years ago, and the
underlying library's username/password login no longer works either —
MFP now blocks direct third-party logins, so the library instead needs a
real browser session cookie handed to it. You authenticate once by
copying your browser's `Cookie` header for myfitnesspal.com into
MFP_COOKIE (see .env.example); this wrapper turns that string into the
cookiejar the library expects.

Like garmin_client.py, this is an unofficial, reverse-engineered client.
The session cookie will need refreshing every few weeks as it expires,
and this can break if MyFitnessPal changes their site.
"""
from __future__ import annotations

from datetime import date, timedelta
from http.cookies import SimpleCookie
from typing import Any

from athlete_coach.config import get_config


class MyFitnessPalAuthError(RuntimeError):
    pass


def parse_cookie_header(raw: str) -> dict[str, str]:
    """Parse a raw `Cookie: ...` header value into a name->value dict."""
    jar: SimpleCookie = SimpleCookie()
    jar.load(raw.strip())
    return {key: morsel.value for key, morsel in jar.items()}


class MyFitnessPalClient:
    def __init__(self) -> None:
        self.cfg = get_config()
        self._client = None

    def _ensure_client(self):
        if self._client is not None:
            return self._client

        if not self.cfg.mfp_cookie:
            raise MyFitnessPalAuthError(
                "MFP_COOKIE is not set. Copy your myfitnesspal.com session cookie "
                "from a logged-in browser into .env (see .env.example)."
            )

        try:
            import myfitnesspal
        except ImportError as e:  # pragma: no cover
            raise MyFitnessPalAuthError(
                "myfitnesspal package not installed. Run `pip install myfitnesspal`."
            ) from e

        cookies = parse_cookie_header(self.cfg.mfp_cookie)
        try:
            client = myfitnesspal.Client(cookiejar=cookies)
        except Exception as e:
            raise MyFitnessPalAuthError(
                "Could not log in to MyFitnessPal with the configured cookie "
                f"(it has likely expired — copy a fresh one into MFP_COOKIE): {e}"
            ) from e

        self._client = client
        return client

    @property
    def authorized(self) -> bool:
        try:
            self._ensure_client()
            return True
        except MyFitnessPalAuthError:
            return False

    def get_day(self, day: date) -> dict[str, Any]:
        client = self._ensure_client()
        d = client.get_date(day)
        totals = d.totals
        return {
            "date": day.isoformat(),
            "calories": totals.get("calories"),
            "protein_g": totals.get("protein"),
            "carbs_g": totals.get("carbohydrates"),
            "fat_g": totals.get("fat"),
            "complete": d.complete,
        }

    def get_range(self, start: date, end: date) -> list[dict[str, Any]]:
        out = []
        d = start
        while d <= end:
            out.append(self.get_day(d))
            d += timedelta(days=1)
        return out
