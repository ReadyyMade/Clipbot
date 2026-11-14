"""Download via yt-dlp (suporta clipes da Twitch e da Kick)."""
from __future__ import annotations

from pathlib import Path


def download(url: str, out_dir: Path, name: str) -> Path:
    import yt_dlp

    out_dir.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(out_dir / f"{name}.%(ext)s"),
        "format": "best[ext=mp4]/best",
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "retries": 3,
        "socket_timeout": 30,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = Path(ydl.prepare_filename(info))
    if not path.exists():
        matches = sorted(out_dir.glob(f"{name}.*"))
        if not matches:
            raise FileNotFoundError(f"download não gerou arquivo para {url}")
        path = matches[0]
    return path
