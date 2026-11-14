"""Orquestração: descobrir -> (aprovar) -> baixar -> renderizar -> publicar."""
from __future__ import annotations

import logging
import sqlite3
import time
from datetime import timedelta
from pathlib import Path

from . import text
from .config import Config
from .db import Database
from .models import Clip, parse_ts, utcnow
from .ranking import is_known_moment, passes_filters, rank, score

log = logging.getLogger(__name__)


def row_to_clip(r: sqlite3.Row) -> Clip:
    return Clip(
        platform=r["platform"], clip_id=r["clip_id"], url=r["url"], title=r["title"] or "",
        broadcaster=r["broadcaster"] or "", views=r["views"] or 0, duration=r["duration"] or 0,
        created_at=parse_ts(r["created_at"]), broadcaster_id=r["broadcaster_id"] or "",
        language=r["language"] or "", media_url=r["media_url"] or "", score=r["score"] or 0,
    )


class Pipeline:
    def __init__(self, cfg: Config, db: Database | None = None, sources=None, publishers=None):
        self.cfg = cfg
        self.db = db or Database(cfg.data_dir / "clipbot.sqlite3")
        self._sources = sources
        self._publishers = publishers
        self._game_id = ""

    # ---------- dependências (lazy, para o CLI funcionar mesmo sem todas as credenciais) ----------
    def sources(self) -> list:
        if self._sources is not None:
            return self._sources
        from .sources import KickSource, TwitchSource

        out = []
        if self.cfg["sources"]["twitch"]:
            out.append(TwitchSource(Config.env("TWITCH_CLIENT_ID"), Config.env("TWITCH_CLIENT_SECRET")))
        if self.cfg["sources"]["kick"]:
            out.append(KickSource(self.cfg["game"]["kick_category_slug"]))
        self._sources = out
        return out

    def publishers(self) -> dict:
        """Publishers criados sob demanda: credencial ausente vira erro só daquela plataforma."""
        if self._publishers is None:
            self._publishers = _LazyPublishers(self.cfg)
        return self._publishers

    # ---------- 1. descoberta ----------
    def fetch(self) -> list[Clip]:
        since = utcnow() - timedelta(hours=self.cfg["discovery"]["lookback_hours"])
        clips: dict[str, Clip] = {}
        for src in self.sources():
            try:
                if src.name == "twitch":
                    found = self._twitch_clips(src, since)
                else:
                    found = [c for c in src.fetch_clips() if c.created_at >= since]
                for c in found:
                    clips.setdefault(c.uid, c)
            except Exception as e:  # uma fonte fora do ar não pode parar a outra
                log.error("fonte %s falhou: %s", src.name, e)
        return list(clips.values())

    def _twitch_clips(self, src, since) -> list[Clip]:
        """Duas buscas somadas:
        1. top global do jogo (os mais vistos do mundo, quase sempre em inglês);
        2. clipes de cada streamer que esteve ao vivo no jogo nos idiomas configurados,
           mais a allowlist. Sem a 2, o filtro de idioma sobra com quase nada."""
        if not self._game_id:
            self._game_id = self.cfg["game"]["twitch_game_id"] or src.resolve_game_id(self.cfg["game"]["name"])
        clips = list(src.fetch_clips(self._game_id, since))
        if hasattr(src, "fetch_clips_by_broadcaster"):
            clips += self._clips_from_streamers(src, self._game_id, since)
        return clips

    def _clips_from_streamers(self, src, game_id: str, since) -> list[Clip]:
        d = self.cfg["discovery"]
        limit = int(d["streamer_scan_limit"])
        if limit <= 0:
            return []
        langs = [l.lower() for l in d["languages"]]
        if langs:
            try:
                self.db.touch_streamers("twitch", src.fetch_live_streams(game_id, langs))
            except Exception as e:
                log.warning("twitch: não foi possível listar lives (%s); usando streamers já conhecidos", e)
        # inclui quem saiu do ar nas últimas horas: o clipe continua ganhando views depois da live
        targets = dict(self.db.recent_streamers("twitch", since, limit))
        allow = self.cfg["streamers"]["allowlist"]
        if allow:
            try:
                targets.update(src.resolve_users(allow))
            except Exception as e:
                log.warning("twitch: não foi possível resolver a allowlist (%s)", e)
        clips: list[Clip] = []
        failed = 0
        for broadcaster_id in targets:
            try:
                clips += src.fetch_clips_by_broadcaster(broadcaster_id, game_id, since,
                                                        first=int(d["clips_per_streamer"]))
            except Exception as e:
                failed += 1
                log.debug("twitch: clipes de %s falharam: %s", targets[broadcaster_id], e)
        log.info("twitch: %d clipes de %d streamers (%s)%s", len(clips), len(targets),
                 ",".join(langs) or "allowlist", f", {failed} falhas" if failed else "")
        return clips

    def discover(self) -> dict:
        ranked, rejected = rank(self.fetch(), self.cfg.data)
        window = self.cfg["discovery"]["dedup_window_s"]
        new = 0
        for clip in ranked:
            if not self.db.get(clip.uid) and is_known_moment(
                clip, self.db.queued_moments(clip.broadcaster, clip.platform), window
            ):
                rejected["mesmo momento"] = rejected.get("mesmo momento", 0) + 1
                continue
            new += self.db.upsert_clip(clip)
        expired = self.refresh_queue()
        auto = 0 if self.cfg["publishing"]["require_approval"] else self.auto_approve()
        summary = {"candidatos": len(ranked), "novos": new, "expirados": expired,
                   "auto_aprovados": auto, "descartes": rejected}
        log.info("discover: %s", summary)
        return summary

    def refresh_queue(self) -> int:
        """Mantém a fila honesta: o score é recalculado com a idade atual (um clipe que saiu
        do resultado da Twitch não fica com a nota congelada) e o que passou de
        max_age_hours sem ser publicado expira. Retorna quantos expiraram."""
        now = utcnow()
        max_age = timedelta(hours=self.cfg["discovery"]["max_age_hours"])
        weights = self.cfg["sources"].get("platform_weight", {})
        expired, rescored = [], []
        for row in self.db.by_status("discovered", "approved", "failed", limit=100_000):
            clip = row_to_clip(row)
            if now - clip.created_at > max_age:
                expired.append(clip.uid)
            else:
                rescored.append((score(clip, weights, now), clip.uid))
        self.db.rescore(rescored)
        self.db.expire(expired)
        return len(expired)

    def candidates(self, *statuses: str, limit: int = 50) -> list[sqlite3.Row]:
        """Clipes da fila que passam nos filtros ATUAIS do config, melhores primeiro.

        Os filtros valem na entrada E na saída: se você muda idioma, views mínimas ou
        blocklist no config.yaml, a fila reflete na hora, sem apagar nada do banco
        (um clipe abaixo do mínimo de views volta a aparecer se crescer)."""
        out = []
        for row in self.db.by_status(*(statuses or ("discovered",)), limit=100_000):
            if passes_filters(row_to_clip(row), self.cfg.data)[0]:
                out.append(row)
                if len(out) >= limit:
                    break
        return out

    # ---------- 2. aprovação ----------
    def can_approve(self, broadcaster: str) -> bool:
        limit = self.cfg["discovery"]["max_per_streamer_per_day"]
        return self.db.streamer_count_since(broadcaster, utcnow() - timedelta(hours=24)) < limit

    def approve(self, uid: str, force: bool = False) -> bool:
        row = self.db.get(uid)
        if not row or row["status"] not in ("discovered", "rejected", "failed"):
            return False
        if not force and not self.can_approve(row["broadcaster"]):
            log.warning("%s: limite diário do streamer %s atingido", uid, row["broadcaster"])
            return False
        self.db.set_status(uid, "approved", error=None)
        return True

    def approve_top(self, n: int) -> int:
        done = 0
        for row in self.candidates("discovered", "failed", limit=200):
            if done >= n:
                break
            done += self.approve(row["uid"])
        return done

    def auto_approve(self) -> int:
        """Aprova só o necessário para os próximos posts (just-in-time).

        Aprovar tudo de uma vez criaria uma fila de dias: quando o clipe fosse ao ar já
        estaria velho. Mantendo um estoque pequeno (auto_approve_buffer), cada vaga é
        preenchida pelo melhor clipe do momento."""
        pub = self.cfg["publishing"]
        capacity = max((self.db.capacity(t, pub["daily_limit"][t]) for t in self.cfg.enabled_targets),
                       default=0)
        backlog = len(self.db.by_status("approved", "rendered", limit=1000))
        slots = min(capacity, int(pub["auto_approve_buffer"])) - backlog
        done = 0
        for row in self.candidates("discovered", limit=200):
            if done >= slots or (row["score"] or 0) < pub["min_score_auto_approve"]:
                break
            done += self.approve(row["uid"])
        return done

    def reject(self, uid: str) -> None:
        self.db.set_status(uid, "rejected")

    # ---------- 3. produção e publicação ----------
    def prepare(self, row: sqlite3.Row) -> Path:
        """Baixa e renderiza (idempotente: reaproveita arquivo já renderizado)."""
        from .download import download
        from .render import render_vertical

        if row["rendered_path"] and Path(row["rendered_path"]).exists():
            return Path(row["rendered_path"])
        clip = row_to_clip(row)
        safe = clip.uid.replace(":", "_")
        log.info("baixando %s (%s)", clip.uid, clip.url)
        raw = download(clip.media_url or clip.url, self.cfg.data_dir / "raw", safe)
        log.info("renderizando %s (%.0fs de vídeo)...", raw.name, min(clip.duration, self.cfg["render"]["max_seconds"]))
        t0 = time.time()
        out = render_vertical(
            raw, self.cfg.data_dir / "rendered" / f"{safe}.mp4", self.cfg["render"],
            title=text.overlay_title(clip), credit=text.overlay_credit(clip),
        )
        log.info("pronto em %.0fs: %s", time.time() - t0, out)
        self.db.set_status(clip.uid, "rendered", video_path=str(raw), rendered_path=str(out))
        return out

    def target_ready(self, target: str) -> bool:
        pub = self.cfg["publishing"]
        if self.db.capacity(target, pub["daily_limit"][target]) <= 0:
            return False
        last = self.db.last_upload_at(target)
        return not last or utcnow() - last >= timedelta(minutes=pub["min_minutes_between_posts"])

    def publish(self, row: sqlite3.Row, video: Path, targets: list[str]) -> tuple[list[str], dict[str, str]]:
        """Publica em cada plataforma de forma independente: se uma falhar, as outras seguem."""
        clip = row_to_clip(row)
        tags = self.cfg["publishing"]["hashtags"]
        done, errors = [], {}
        for target in targets:
            try:
                pub = self.publishers()[target]
                if target == "youtube":
                    remote = pub.upload(video, text.youtube_title(clip, tags),
                                        text.description(clip, tags), text.tags(clip, tags))
                else:
                    remote = pub.upload(video, text.tiktok_caption(clip, tags))
                self.db.record_upload(clip.uid, target, remote)
                done.append(target)
            except Exception as e:
                log.error("%s: falha no %s: %s", clip.uid, target, e)
                errors[target] = str(e)[:300]
        return done, errors

    def process(self, dry_run: bool = False, max_items: int = 1) -> list[dict]:
        """Processa até max_items clipes aprovados respeitando cota diária e intervalo entre posts."""
        results = []
        for row in self.db.by_status("approved", "rendered", limit=max_items * 3):
            if len(results) >= max_items:
                break
            pending = [t for t in self.cfg.enabled_targets if t not in self.db.uploaded_targets(row["uid"])]
            if not pending and not dry_run:
                self.db.set_status(row["uid"], "published", error=None)
                continue
            targets = pending if dry_run else [t for t in pending if self.target_ready(t)]
            if not targets:
                continue
            try:
                video = self.prepare(row)
            except Exception as e:
                log.exception("falha ao preparar %s", row["uid"])
                self.db.set_status(row["uid"], "failed", error=str(e)[:500])
                results.append({"uid": row["uid"], "erro": str(e)[:200]})
                continue
            if dry_run:
                results.append({"uid": row["uid"], "video": str(video), "publicado_em": []})
                continue
            done, errors = self.publish(self.db.get(row["uid"]), video, targets)
            if set(self.cfg.enabled_targets) <= self.db.uploaded_targets(row["uid"]):
                self.db.set_status(row["uid"], "published", error=None)
            elif errors:
                # continua 'rendered': o próximo ciclo tenta só as plataformas que faltaram
                self.db.set_status(row["uid"], "rendered", error="; ".join(f"{k}: {v}" for k, v in errors.items())[:500])
            res = {"uid": row["uid"], "video": str(video), "publicado_em": done}
            if errors:
                res["erros"] = errors
            results.append(res)
        return results


class _LazyPublishers(dict):
    def __init__(self, cfg: Config):
        super().__init__()
        self.cfg = cfg

    def __missing__(self, target: str):
        pub = self.cfg["publishing"]
        if target == "youtube":
            from .publishers.youtube import YouTubePublisher

            obj = YouTubePublisher(
                Config.env("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json"),
                pub["youtube"]["privacy"], pub["youtube"]["category_id"],
            )
        elif target == "tiktok":
            from .publishers.tiktok import TikTokPublisher

            obj = TikTokPublisher(
                Config.env("TIKTOK_TOKEN_FILE", "secrets/tiktok_token.json"),
                Config.env("TIKTOK_CLIENT_KEY"), Config.env("TIKTOK_CLIENT_SECRET"),
                pub["tiktok"]["privacy"],
            )
        else:
            raise KeyError(target)
        self[target] = obj
        return obj
