"""Twitch Helix API (oficial). Usa app access token (client credentials)."""
from __future__ import annotations

import logging
import time
from datetime import datetime

import requests

from ..models import Clip, parse_ts, utcnow

log = logging.getLogger(__name__)

AUTH_URL = "https://id.twitch.tv/oauth2/token"
API = "https://api.twitch.tv/helix"


class TwitchSource:
    name = "twitch"

    def __init__(self, client_id: str, client_secret: str, session: requests.Session | None = None):
        if not client_id or not client_secret:
            raise ValueError("TWITCH_CLIENT_ID/TWITCH_CLIENT_SECRET não configurados")
        self.client_id = client_id
        self.client_secret = client_secret
        self.http = session or requests.Session()
        self._token: str | None = None
        self._token_exp = 0.0

    def _auth(self) -> str:
        if self._token and time.time() < self._token_exp - 60:
            return self._token
        r = self.http.post(
            AUTH_URL,
            params={"client_id": self.client_id, "client_secret": self.client_secret,
                    "grant_type": "client_credentials"},
            timeout=20,
        )
        r.raise_for_status()
        body = r.json()
        self._token = body["access_token"]
        self._token_exp = time.time() + int(body.get("expires_in", 3600))
        return self._token

    def _get(self, path: str, params: dict) -> dict:
        for attempt in range(3):
            r = self.http.get(
                f"{API}{path}",
                params=params,
                headers={"Client-Id": self.client_id, "Authorization": f"Bearer {self._auth()}"},
                timeout=20,
            )
            if r.status_code == 401 and attempt == 0:
                self._token = None
                continue
            if r.status_code == 429:
                reset = float(r.headers.get("Ratelimit-Reset", time.time() + 5))
                time.sleep(max(1.0, reset - time.time()))
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Twitch API falhou em {path}")

    def resolve_game_id(self, name: str) -> str:
        data = self._get("/games", {"name": name}).get("data", [])
        if not data:
            raise LookupError(f"Jogo '{name}' não encontrado na Twitch")
        return data[0]["id"]

    def fetch_clips(self, game_id: str, started_at: datetime, ended_at: datetime | None = None,
                    max_pages: int = 5) -> list[Clip]:
        params = {
            "game_id": game_id,
            "started_at": started_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "ended_at": (ended_at or utcnow()).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "first": 100,
        }
        clips: list[Clip] = []
        for _ in range(max_pages):
            body = self._get("/clips", params)
            clips.extend(self.parse(c) for c in body.get("data", []))
            cursor = body.get("pagination", {}).get("cursor")
            if not cursor:
                break
            params["after"] = cursor
        log.info("twitch: %d clipes", len(clips))
        return clips

    @staticmethod
    def parse(c: dict) -> Clip:
        return Clip(
            platform="twitch",
            clip_id=c["id"],
            url=c["url"],
            title=c.get("title", "").strip(),
            broadcaster=c.get("broadcaster_name", ""),
            broadcaster_id=c.get("broadcaster_id", ""),
            language=c.get("language", ""),
            views=int(c.get("view_count", 0)),
            duration=float(c.get("duration", 0)),
            created_at=parse_ts(c.get("created_at")),
            thumbnail_url=c.get("thumbnail_url", ""),
            extra={"vod_offset": c.get("vod_offset"), "creator": c.get("creator_name")},
        )
