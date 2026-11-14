"""Filtro, deduplicação e ranking de clipes.

Score = (views * peso_plataforma) / (idade_horas + 2) ** 1.5

Por que não usar só views? Um clipe de 20h com 50k views já "rodou"; um clipe de 1h com 8k
views está acelerando. A gravidade (1.5) favorece velocidade de crescimento — o que importa
para pegar a tendência antes dos outros canais de clipes.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable

from .models import Clip, parse_ts, utcnow

GRAVITY = 1.5


def score(clip: Clip, weights: dict[str, float] | None = None, now: datetime | None = None) -> float:
    now = now or utcnow()
    age_h = max(0.0, (now - clip.created_at).total_seconds() / 3600)
    w = (weights or {}).get(clip.platform, 1.0)
    return round(clip.views * w / (age_h + 2) ** GRAVITY, 4)


def passes_filters(clip: Clip, cfg: dict) -> tuple[bool, str]:
    d, s = cfg["discovery"], cfg["streamers"]
    name = clip.broadcaster.lower()
    if name in [b.lower() for b in s["blocklist"]]:
        return False, "blocklist"
    if s["mode"] == "allowlist" and name not in [a.lower() for a in s["allowlist"]]:
        return False, "fora da allowlist"
    if clip.views < d["min_views"]:
        return False, "poucas views"
    if not (d["min_duration_s"] <= clip.duration <= d["max_duration_s"]):
        return False, "duração"
    langs = [l.lower() for l in d["languages"]]
    if langs and clip.language and clip.language.lower().split("-")[0] not in langs:
        return False, "idioma"
    return True, ""


def collapse_duplicates(clips: Iterable[Clip], window_s: int) -> list[Clip]:
    """Vários viewers clipam o mesmo momento. Mantém o de maior score por (plataforma, streamer, janela)."""
    kept: list[Clip] = []
    for clip in sorted(clips, key=lambda c: c.score, reverse=True):
        dup = any(
            k.platform == clip.platform
            and k.broadcaster.lower() == clip.broadcaster.lower()
            and abs((k.created_at - clip.created_at).total_seconds()) <= window_s
            for k in kept
        )
        if not dup:
            kept.append(clip)
    return kept


def is_known_moment(clip: Clip, known_created_at: list[str], window_s: int) -> bool:
    return any(abs((parse_ts(t) - clip.created_at).total_seconds()) <= window_s for t in known_created_at)


def rank(clips: Iterable[Clip], cfg: dict, now: datetime | None = None) -> tuple[list[Clip], dict[str, int]]:
    """Retorna (clipes aprovados no filtro, ordenados por score) e contagem de descartes por motivo."""
    weights = cfg["sources"].get("platform_weight", {})
    rejected: dict[str, int] = {}
    ok: list[Clip] = []
    for c in clips:
        passed, reason = passes_filters(c, cfg)
        if not passed:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue
        c.score = score(c, weights, now)
        ok.append(c)
    before = len(ok)
    ok = collapse_duplicates(ok, cfg["discovery"]["dedup_window_s"])
    if before - len(ok):
        rejected["mesmo momento"] = before - len(ok)
    return ok, rejected
