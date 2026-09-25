"""Configuration loading for the athlete-coach MCP server.

All configuration comes from environment variables (typically via a .env
file in the working directory, or set directly in the Claude Desktop/Code
MCP server config's `env` block).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

DEFAULT_HOME = Path(os.environ.get("ATHLETE_COACH_HOME", str(Path.home() / ".athlete_coach")))


@dataclass(frozen=True)
class Config:
    home_dir: Path
    db_path: Path

    strava_client_id: str | None
    strava_client_secret: str | None
    strava_redirect_uri: str

    garmin_email: str | None
    garmin_password: str | None

    mfp_cookie: str | None

    timezone: str

    @classmethod
    def load(cls) -> "Config":
        home = DEFAULT_HOME
        home.mkdir(parents=True, exist_ok=True)
        db_path = Path(os.environ.get("ATHLETE_COACH_DB_PATH", str(home / "athlete.db")))
        return cls(
            home_dir=home,
            db_path=db_path,
            strava_client_id=os.environ.get("STRAVA_CLIENT_ID"),
            strava_client_secret=os.environ.get("STRAVA_CLIENT_SECRET"),
            strava_redirect_uri=os.environ.get(
                "STRAVA_REDIRECT_URI", "http://localhost:8721/callback"
            ),
            garmin_email=os.environ.get("GARMIN_EMAIL"),
            garmin_password=os.environ.get("GARMIN_PASSWORD"),
            mfp_cookie=os.environ.get("MFP_COOKIE"),
            timezone=os.environ.get("ATHLETE_COACH_TZ", "Europe/London"),
        )


def get_config() -> Config:
    return Config.load()
