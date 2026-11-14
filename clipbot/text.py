"""Títulos, descrições e textos de overlay. O crédito ao streamer é obrigatório no design."""
from __future__ import annotations

import re
import textwrap

from .models import Clip

PLATFORM_LABEL = {"twitch": "Twitch", "kick": "Kick"}


def clean(text: str) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text.replace("<", "").replace(">", "")  # YouTube rejeita < >


def youtube_title(clip: Clip, hashtags: list[str]) -> str:
    base = clean(clip.title) or "Momento insano no GTA 6"
    tag = hashtags[0] if hashtags else ""
    suffix = f" | {clip.broadcaster} {tag}".rstrip()
    max_base = 100 - len(suffix)
    if len(base) > max_base:
        base = base[: max_base - 1].rstrip() + "…"
    return base + suffix


def description(clip: Clip, hashtags: list[str]) -> str:
    label = PLATFORM_LABEL.get(clip.platform, clip.platform)
    return (
        f"{clean(clip.title)}\n\n"
        f"🎮 Streamer: {clip.broadcaster} ({label})\n"
        f"📺 Canal: {clip.channel_url}\n"
        f"🎬 Clipe original: {clip.url}\n\n"
        f"Todo o crédito ao criador original. Siga o canal dele!\n\n"
        + " ".join(hashtags)
    )


def tiktok_caption(clip: Clip, hashtags: list[str]) -> str:
    tags = " ".join(h for h in hashtags if h.lower() != "#shorts")
    label = PLATFORM_LABEL.get(clip.platform, clip.platform)
    cap = f"{clean(clip.title)} 🎮 {clip.broadcaster} ({label}) — créditos: {clip.channel_url} {tags}"
    return cap[:2200]


def tags(clip: Clip, hashtags: list[str]) -> list[str]:
    base = [h.lstrip("#") for h in hashtags] + ["GTA 6", "GTA VI", clip.broadcaster, "clipes"]
    out, total = [], 0
    for t in base:
        if t and t not in out and total + len(t) + 1 <= 480:
            out.append(t)
            total += len(t) + 1
    return out


def overlay_title(clip: Clip, width_chars: int = 26, max_lines: int = 3) -> str:
    lines = textwrap.wrap(clean(clip.title).upper(), width_chars)[:max_lines]
    return "\n".join(lines)


def overlay_credit(clip: Clip) -> str:
    domain = "twitch.tv" if clip.platform == "twitch" else "kick.com"
    return f"{domain}/{clip.broadcaster.lower()}"
