"""Strava API v3 client: OAuth device flow + authenticated requests.

Run `athlete-coach-strava-auth` once to authorize (opens a browser, spins
up a local server to catch the redirect, and stores a refresh token in
`~/.athlete_coach/strava_token.json`). After that, `StravaClient` handles
access-token refresh transparently.
"""
from __future__ import annotations

import json
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any
from urllib.parse import parse_qs, urlencode, urlparse

import requests

from athlete_coach.config import get_config

AUTHORIZE_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_BASE = "https://www.strava.com/api/v3"
SCOPES = "read,activity:read_all,profile:read_all"


class StravaAuthError(RuntimeError):
    pass


def _token_path():
    return get_config().home_dir / "strava_token.json"


def _save_token(token: dict[str, Any]) -> None:
    _token_path().write_text(json.dumps(token, indent=2))


def _load_token() -> dict[str, Any] | None:
    path = _token_path()
    if not path.exists():
        return None
    return json.loads(path.read_text())


class _CallbackHandler(BaseHTTPRequestHandler):
    code: str | None = None

    def do_GET(self) -> None:  # noqa: N802 (stdlib override)
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)
        if "code" in qs:
            _CallbackHandler.code = qs["code"][0]
            body = b"<html><body>Strava authorized. You can close this tab.</body></html>"
        else:
            body = b"<html><body>No authorization code received.</body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:  # silence default logging
        pass


def run_oauth_flow() -> None:
    """Interactive one-time authorization. Run from a terminal."""
    cfg = get_config()
    if not cfg.strava_client_id or not cfg.strava_client_secret:
        raise StravaAuthError(
            "Set STRAVA_CLIENT_ID and STRAVA_CLIENT_SECRET (create an API app at "
            "https://www.strava.com/settings/api) before running this."
        )

    redirect = urlparse(cfg.strava_redirect_uri)
    params = {
        "client_id": cfg.strava_client_id,
        "redirect_uri": cfg.strava_redirect_uri,
        "response_type": "code",
        "approval_prompt": "auto",
        "scope": SCOPES,
    }
    url = f"{AUTHORIZE_URL}?{urlencode(params)}"
    print(f"Opening browser for Strava authorization:\n{url}\n")
    webbrowser.open(url)

    server = HTTPServer((redirect.hostname or "localhost", redirect.port or 8721), _CallbackHandler)
    print("Waiting for redirect...")
    while _CallbackHandler.code is None:
        server.handle_request()
    code = _CallbackHandler.code

    resp = requests.post(
        TOKEN_URL,
        data={
            "client_id": cfg.strava_client_id,
            "client_secret": cfg.strava_client_secret,
            "code": code,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )
    resp.raise_for_status()
    token = resp.json()
    _save_token(token)
    print(f"Authorized as athlete id {token.get('athlete', {}).get('id')}. Token saved.")


class StravaClient:
    def __init__(self) -> None:
        self.cfg = get_config()
        self._token = _load_token()

    @property
    def authorized(self) -> bool:
        return self._token is not None

    def _refresh_if_needed(self) -> None:
        if self._token is None:
            raise StravaAuthError(
                "Not authorized with Strava yet. Run `athlete-coach-strava-auth` first."
            )
        if self._token["expires_at"] > time.time() + 60:
            return
        resp = requests.post(
            TOKEN_URL,
            data={
                "client_id": self.cfg.strava_client_id,
                "client_secret": self.cfg.strava_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": self._token["refresh_token"],
            },
            timeout=30,
        )
        resp.raise_for_status()
        new_token = resp.json()
        self._token.update(new_token)
        _save_token(self._token)

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        self._refresh_if_needed()
        resp = requests.get(
            f"{API_BASE}{path}",
            headers={"Authorization": f"Bearer {self._token['access_token']}"},
            params=params or {},
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()

    def get_athlete(self) -> dict[str, Any]:
        return self._get("/athlete")

    def get_athlete_zones(self) -> dict[str, Any]:
        return self._get("/athlete/zones")

    def list_activities(
        self, after_epoch: int | None = None, before_epoch: int | None = None, per_page: int = 100
    ) -> list[dict[str, Any]]:
        """Fetch all activities in the given epoch-seconds window, paginated."""
        results: list[dict[str, Any]] = []
        page = 1
        while True:
            params: dict[str, Any] = {"per_page": per_page, "page": page}
            if after_epoch:
                params["after"] = after_epoch
            if before_epoch:
                params["before"] = before_epoch
            batch = self._get("/athlete/activities", params=params)
            if not batch:
                break
            results.extend(batch)
            if len(batch) < per_page:
                break
            page += 1
        return results

    def get_activity(self, activity_id: str | int) -> dict[str, Any]:
        return self._get(f"/activities/{activity_id}")
