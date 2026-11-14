"""Converte clipe 16:9 em vídeo vertical 9:16 (1080x1920) com ffmpeg.

Layout: fundo = o próprio vídeo ampliado e desfocado; centro = vídeo original inteiro;
topo = título do clipe; base = crédito do streamer. Os textos vão por arquivo (textfile=)
para evitar problemas de escape com aspas, dois-pontos e emojis no drawtext.

Windows: o parser de filtros do ffmpeg trata "\\" e ":" como especiais, então caminhos como
C:\\pasta\\x.txt quebram. Por isso textfiles e fonte (copiada para a pasta de saída) são
passados pelo NOME e o ffmpeg roda com cwd na pasta de saída.
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

# Fontes padrão por sistema. No Windows o ffmpeg não tem fontconfig configurado,
# então sem fontfile o drawtext falha ("Cannot load default config file").
FONT_CANDIDATES = [
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "C:/Windows/Fonts/segoeuib.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
    "/Library/Fonts/Arial Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf",
]


def resolve_font(configured: str = "") -> Path | None:
    """Fonte do config; senão a primeira fonte conhecida do sistema; senão None (fontconfig)."""
    if configured:
        if not Path(configured).exists():
            raise FileNotFoundError(f"render.font_file não encontrado: {configured}")
        return Path(configured)
    for cand in FONT_CANDIDATES:
        if Path(cand).exists():
            return Path(cand)
    if sys.platform == "win32":
        raise FileNotFoundError("Nenhuma fonte encontrada; defina render.font_file no config.yaml")
    return None


def build_command(src: Path, dst: Path, cfg: dict, title_file: Path | None,
                  credit_file: Path | None, font_file: Path | None = None) -> list[str]:
    """Arquivos de texto e fonte são referenciados só pelo NOME: o ffmpeg roda com cwd na
    pasta de saída, o que evita escapar caminhos do Windows dentro do filtergraph."""
    w, h = int(cfg["width"]), int(cfg["height"])
    font = f":fontfile='{font_file.name}'" if font_file else ""
    chain = [
        f"[0:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"boxblur=20:2,eq=brightness=-0.12[bg]",
        f"[0:v]scale={w}:-2[fg]",
        "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0]",
    ]
    last = "v0"
    if title_file:
        chain.append(
            f"[{last}]drawtext=textfile='{title_file.name}'{font}:fontcolor=white:fontsize={w // 16}"
            f":line_spacing=12:borderw=5:bordercolor=black:x=(w-text_w)/2:y=h*0.13[v1]"
        )
        last = "v1"
    if credit_file:
        chain.append(
            f"[{last}]drawtext=textfile='{credit_file.name}'{font}:fontcolor=white:fontsize={w // 24}"
            f":box=1:boxcolor=black@0.55:boxborderw=18:x=(w-text_w)/2:y=h*0.80[v2]"
        )
        last = "v2"
    return [
        "ffmpeg", "-nostdin", "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(src),
        "-filter_complex", ";".join(chain),
        "-map", f"[{last}]", "-map", "0:a?",
        "-t", str(cfg["max_seconds"]),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "21", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100",
        "-movflags", "+faststart",
        str(dst),
    ]


RENDER_TIMEOUT_S = 600


def render_vertical(src: Path, dst: Path, cfg: dict, title: str = "", credit: str = "") -> Path:
    src, dst = Path(src).resolve(), Path(dst).resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    title_file = credit_file = None
    if cfg.get("show_title") and title:
        title_file = dst.with_suffix(".title.txt")
        title_file.write_text(title, encoding="utf-8")
    if cfg.get("show_credit") and credit:
        credit_file = dst.with_suffix(".credit.txt")
        credit_file.write_text(credit, encoding="utf-8")
    font_file = None
    if title_file or credit_file:
        font = resolve_font(cfg.get("font_file", ""))
        if font:
            font_file = dst.parent / f"_font{font.suffix.lower()}"
            if not font_file.exists():
                shutil.copyfile(font, font_file)
    cmd = build_command(src, dst, cfg, title_file, credit_file, font_file)
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=dst.parent,
                              stdin=subprocess.DEVNULL, timeout=RENDER_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"ffmpeg passou de {RENDER_TIMEOUT_S}s renderizando {src.name}")
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg falhou: {proc.stderr.strip()[-800:]}")
    return dst
