from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def parse_ts(value: str | None) -> datetime:
    if not value:
        return utcnow()
    value = value.replace("Z", "+00:00")
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


@dataclass
class Clip:
    platform: str          # "twitch" | "kick"
    clip_id: str
    url: str               # página pública do clipe (crédito/link)
    title: str
    broadcaster: str       # nome de exibição do canal
    views: int
    duration: float
    created_at: datetime
    broadcaster_id: str = ""
    language: str = ""
    media_url: str = ""    # URL direta do vídeo, se a fonte fornecer
    thumbnail_url: str = ""
    score: float = 0.0
    extra: dict = field(default_factory=dict)

    @property
    def uid(self) -> str:
        return f"{self.platform}:{self.clip_id}"

    @property
    def channel_url(self) -> str:
        base = "https://twitch.tv/" if self.platform == "twitch" else "https://kick.com/"
        return base + self.broadcaster.lower()
