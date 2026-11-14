"""Kick — EXPERIMENTAL.

A Kick não oferece endpoint oficial de clipes na API pública. Este módulo usa o endpoint
interno do site, que pode mudar sem aviso ou ser bloqueado pelo Cloudflare. Por isso:
  * o endpoint é configurável;
  * o parser é tolerante a nomes de campos diferentes;
  * qualquer falha é registrada e retorna lista vazia (não derruba a pipeline).
"""
from __future__ import annotations

import logging

import requests

from ..models import Clip, parse_ts

log = logging.getLogger(__name__)

DEFAULT_ENDPOINT = "https://kick.com/api/v2/categories/{slug}/clips"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept": "application/json",
}


def _first(d: dict, *keys, default=None):
    for k in keys:
        if d.get(k) not in (None, ""):
            return d[k]
    return default


class KickSource:
    name = "kick"

    def __init__(self, category_slug: str, endpoint: str = DEFAULT_ENDPOINT,
                 session: requests.Session | None = None):
        self.slug = category_slug
        self.endpoint = endpoint
        self.http = session or requests.Session()

    def fetch_clips(self, max_pages: int = 3) -> list[Clip]:
        url = self.endpoint.format(slug=self.slug)
        params: dict = {"sort": "view", "time": "day"}
        clips: list[Clip] = []
        try:
            for _ in range(max_pages):
                r = self.http.get(url, params=params, headers=HEADERS, timeout=20)
                r.raise_for_status()
                body = r.json()
                items = body.get("clips") or body.get("data") or []
                for item in items:
                    clip = self.parse(item)
                    if clip:
                        clips.append(clip)
                cursor = body.get("nextCursor") or body.get("next_cursor")
                if not cursor:
                    break
                params["cursor"] = cursor
        except (requests.RequestException, ValueError) as e:
            log.warning("kick: falha ao buscar clipes (%s). Seguindo sem Kick.", e)
        log.info("kick: %d clipes", len(clips))
        return clips

    @staticmethod
    def parse(c: dict) -> Clip | None:
        channel = c.get("channel") or {}
        slug = _first(channel, "slug", "username", default="")
        clip_id = str(_first(c, "id", "clip_id", default=""))
        if not clip_id or not slug:
            return None
        return Clip(
            platform="kick",
            clip_id=clip_id,
            url=f"https://kick.com/{slug}/clips/{clip_id}",
            media_url=_first(c, "video_url", "clip_url", default=""),
            title=(c.get("title") or "").strip(),
            broadcaster=_first(channel, "username", "slug", default=slug),
            broadcaster_id=str(_first(channel, "id", default="")),
            language="",
            views=int(_first(c, "view_count", "views", default=0)),
            duration=float(_first(c, "duration", default=0)),
            created_at=parse_ts(c.get("created_at")),
            thumbnail_url=_first(c, "thumbnail_url", default=""),
        )
