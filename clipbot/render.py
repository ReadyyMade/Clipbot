"""Converte clipe 16:9 em vídeo vertical 9:16 (1080x1920) com ffmpeg.

Layout: fundo = o próprio vídeo ampliado e desfocado; centro = vídeo original inteiro;
topo = título do clipe; base = crédito do streamer. Os textos vão por arquivo (textfile=)
para evitar problemas de escape com aspas, dois-pontos e emojis no drawtext.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def build_command(src: Path, dst: Path, cfg: dict, title_file: Path | None,
                  credit_file: Path | None) -> list[str]:
    w, h = int(cfg["width"]), int(cfg["height"])
    font = f":fontfile='{cfg['font_file']}'" if cfg.get("font_file") else ""
    chain = [
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"boxblur=20:2,eq=brightness=-0.12[bg]",
        f"[0:v]scale={w}:-2[fg]",
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]",
    ]
    last = "v0"
    if title_file:
        chain.append(
            f"[{last}]drawtext=textfile='{title_file}'{font}:fontcolor=white:fontsize={w // 16}"
            f":line_spacing=12:borderw=5:bordercolor=black:x=(w-text_w)/2:y=h*0.13[v1]"
        )
        last = "v1"
    if credit_file:
        chain.append(
            f"[{last}]drawtext=textfile='{credit_file}'{font}:fontcolor=white:fontsize={w // 24}"
            f":box=1:boxcolor=black@0.55:boxborderw=18:x=(w-text_w)/2:y=h*0.80[v2]"
        )
        last = "v2"
    return [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-filter_complex", ";".join(chain),
        "-map", f"[{last}]", "-map", "0:a?",
        "-t", str(cfg["max_seconds"]),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        str(dst),
    ]


def render_vertical(src: Path, dst: Path, cfg: dict, title: str = "", credit: str = "") -> Path:
    dst.parent.mkdir(parents=True, exist_ok=True)
    title_file = credit_file = None
    if cfg.get("show_title") and title:
        title_file = dst.with_suffix(".title.txt")
        title_file.write_text(title, encoding="utf-8")
    if cfg.get("show_credit") and credit:
        credit_file = dst.with_suffix(".credit.txt")
        credit_file.write_text(credit, encoding="utf-8")
    cmd = build_command(src, dst, cfg, title_file, credit_file)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou: {proc.stderr.strip()[-800:]}")
    return dst
