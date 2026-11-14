from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml

DEFAULTS: dict[str, Any] = {
    "game": {"name": "Grand Theft Auto VI", "twitch_game_id": "", "kick_category_slug": "grand-theft-auto-vi"},
    "sources": {"twitch": True, "kick": False, "platform_weight": {"twitch": 1.0, "kick": 2.5}},
    "discovery": {
        "lookback_hours": 24,
        "min_views": 300,
        "min_duration_s": 8,
        "max_duration_s": 60,
        "languages": [],
        "dedup_window_s": 120,
        "max_per_streamer_per_day": 2,
    },
    "streamers": {"mode": "open", "allowlist": [], "blocklist": []},
    "publishing": {
        "require_approval": True,
        "min_score_auto_approve": 0,
        "min_minutes_between_posts": 45,
        "daily_limit": {"youtube": 6, "tiktok": 10},
        "hashtags": ["#GTA6", "#GTAVI", "#Shorts"],
        "youtube": {"enabled": True, "privacy": "public", "category_id": "20"},
        "tiktok": {"enabled": True, "privacy": "SELF_ONLY"},
    },
    "render": {
        "width": 1080,
        "height": 1920,
        "max_seconds": 59,
        "zoom": 1.0,
        "show_title": True,
        "show_credit": True,
        "font_file": "",
    },
    "paths": {"data_dir": "data"},
}


def _merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Config:
    """Config do YAML (comportamento) + variáveis de ambiente (segredos)."""

    def __init__(self, data: dict[str, Any]):
        self.data = data

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    @property
    def data_dir(self) -> Path:
        p = Path(self.data["paths"]["data_dir"])
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def enabled_targets(self) -> list[str]:
        pub = self.data["publishing"]
        return [t for t in ("youtube", "tiktok") if pub[t]["enabled"]]

    @staticmethod
    def env(name: str, default: str = "") -> str:
        return os.environ.get(name, default)

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        try:
            from dotenv import load_dotenv

            load_dotenv()
        except ImportError:
            pass
        user: dict[str, Any] = {}
        path = Path(path or os.environ.get("CLIPBOT_CONFIG", "config.yaml"))
        if path.exists():
            user = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls(_merge(DEFAULTS, user))
