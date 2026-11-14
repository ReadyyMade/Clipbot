"""Estado persistente em SQLite: fila de clipes, uploads e histórico (base para KPIs)."""
from __future__ import annotations

import csv
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Iterable

from .models import Clip, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS clips (
    uid            TEXT PRIMARY KEY,
    platform       TEXT NOT NULL,
    clip_id        TEXT NOT NULL,
    url            TEXT NOT NULL,
    media_url      TEXT,
    title          TEXT,
    broadcaster    TEXT,
    broadcaster_id TEXT,
    language       TEXT,
    views          INTEGER,
    duration       REAL,
    created_at     TEXT,
    discovered_at  TEXT,
    updated_at     TEXT,
    score          REAL,
    status         TEXT NOT NULL DEFAULT 'discovered',
    video_path     TEXT,
    rendered_path  TEXT,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS ix_clips_status ON clips(status, score);
CREATE INDEX IF NOT EXISTS ix_clips_broadcaster ON clips(broadcaster, created_at);

-- Streamers vistos ao vivo no jogo (base da busca por idioma/allowlist)
CREATE TABLE IF NOT EXISTS streamers (
    platform       TEXT NOT NULL,
    broadcaster_id TEXT NOT NULL,
    name           TEXT,
    language       TEXT,
    viewers        INTEGER,
    last_live_at   TEXT NOT NULL,
    PRIMARY KEY (platform, broadcaster_id)
);

CREATE TABLE IF NOT EXISTS uploads (
    uid          TEXT NOT NULL,
    target       TEXT NOT NULL,      -- youtube | tiktok
    remote_id    TEXT,
    uploaded_at  TEXT NOT NULL,
    PRIMARY KEY (uid, target)
);
"""

# Fluxo: discovered -> approved -> rendered -> published
#            |  \-> rejected            \-> failed
#            \-> expired (ficou velho sem ser publicado)
STATUSES = ("discovered", "approved", "rejected", "rendered", "published", "failed", "expired")


class Database:
    def __init__(self, path: str | Path):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)

    # ---------- clips ----------
    def upsert_clip(self, clip: Clip) -> bool:
        """Insere clipe novo ou atualiza views/score. Retorna True se for novo.

        updated_at NÃO muda na atualização de views: ele marca a última mudança de status,
        e é por ele que se conta o limite diário por streamer."""
        now = utcnow().isoformat()
        exists = self.conn.execute("SELECT 1 FROM clips WHERE uid=?", (clip.uid,)).fetchone()
        if exists:
            self.conn.execute(
                "UPDATE clips SET views=?, score=? WHERE uid=?",
                (clip.views, clip.score, clip.uid),
            )
        else:
            self.conn.execute(
                """INSERT INTO clips (uid, platform, clip_id, url, media_url, title, broadcaster,
                   broadcaster_id, language, views, duration, created_at, discovered_at, updated_at, score)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    clip.uid, clip.platform, clip.clip_id, clip.url, clip.media_url, clip.title,
                    clip.broadcaster, clip.broadcaster_id, clip.language, clip.views, clip.duration,
                    clip.created_at.isoformat(), now, now, clip.score,
                ),
            )
        self.conn.commit()
        return not exists

    def get(self, uid: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM clips WHERE uid=?", (uid,)).fetchone()

    def set_status(self, uid: str, status: str, **fields) -> None:
        assert status in STATUSES, status
        fields["status"] = status
        fields["updated_at"] = utcnow().isoformat()
        cols = ", ".join(f"{k}=?" for k in fields)
        self.conn.execute(f"UPDATE clips SET {cols} WHERE uid=?", (*fields.values(), uid))
        self.conn.commit()

    def by_status(self, *statuses: str, limit: int = 50) -> list[sqlite3.Row]:
        marks = ",".join("?" * len(statuses))
        return self.conn.execute(
            f"SELECT * FROM clips WHERE status IN ({marks}) ORDER BY score DESC LIMIT ?",
            (*statuses, limit),
        ).fetchall()

    def rescore(self, pairs: Iterable[tuple[float, str]]) -> None:
        """Atualiza scores em lote [(score, uid)] sem mexer em updated_at."""
        self.conn.executemany("UPDATE clips SET score=? WHERE uid=?", list(pairs))
        self.conn.commit()

    def expire(self, uids: Iterable[str]) -> None:
        now = utcnow().isoformat()
        self.conn.executemany(
            "UPDATE clips SET status='expired', updated_at=? WHERE uid=?", [(now, u) for u in uids]
        )
        self.conn.commit()

    def queued_moments(self, broadcaster: str, platform: str) -> list[str]:
        """created_at dos clipes já conhecidos do streamer (para deduplicar o mesmo momento)."""
        rows = self.conn.execute(
            "SELECT created_at FROM clips WHERE platform=? AND lower(broadcaster)=lower(?) AND status!='rejected'",
            (platform, broadcaster),
        ).fetchall()
        return [r["created_at"] for r in rows]

    def streamer_count_since(self, broadcaster: str, since: datetime) -> int:
        row = self.conn.execute(
            """SELECT COUNT(*) AS n FROM clips WHERE lower(broadcaster)=lower(?)
               AND status IN ('approved','rendered','published') AND updated_at>=?""",
            (broadcaster, since.isoformat()),
        ).fetchone()
        return int(row["n"])

    # ---------- streamers ----------
    def touch_streamers(self, platform: str, streams: Iterable[dict]) -> None:
        """Registra quem está ao vivo agora: [{broadcaster_id, name, language, viewers}]."""
        now = utcnow().isoformat()
        self.conn.executemany(
            """INSERT INTO streamers (platform, broadcaster_id, name, language, viewers, last_live_at)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(platform, broadcaster_id) DO UPDATE SET
                 name=excluded.name, language=excluded.language,
                 viewers=excluded.viewers, last_live_at=excluded.last_live_at""",
            [(platform, s["broadcaster_id"], s.get("name", ""), s.get("language", ""),
              int(s.get("viewers", 0)), now) for s in streams],
        )
        self.conn.commit()

    def recent_streamers(self, platform: str, since: datetime, limit: int) -> list[tuple[str, str]]:
        """(broadcaster_id, name) de quem esteve ao vivo desde `since`, maiores audiências primeiro."""
        rows = self.conn.execute(
            """SELECT broadcaster_id, name FROM streamers WHERE platform=? AND last_live_at>=?
               ORDER BY viewers DESC LIMIT ?""",
            (platform, since.isoformat(), limit),
        ).fetchall()
        return [(r["broadcaster_id"], r["name"]) for r in rows]

    # ---------- uploads ----------
    def record_upload(self, uid: str, target: str, remote_id: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO uploads (uid, target, remote_id, uploaded_at) VALUES (?,?,?,?)",
            (uid, target, remote_id, utcnow().isoformat()),
        )
        self.conn.commit()

    def uploaded_targets(self, uid: str) -> set[str]:
        rows = self.conn.execute("SELECT target FROM uploads WHERE uid=?", (uid,)).fetchall()
        return {r["target"] for r in rows}

    def uploads_since(self, target: str, since: datetime) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM uploads WHERE target=? AND uploaded_at>=?",
            (target, since.isoformat()),
        ).fetchone()
        return int(row["n"])

    def last_upload_at(self, target: str) -> datetime | None:
        row = self.conn.execute(
            "SELECT MAX(uploaded_at) AS t FROM uploads WHERE target=?", (target,)
        ).fetchone()
        return datetime.fromisoformat(row["t"]) if row and row["t"] else None

    def capacity(self, target: str, daily_limit: int) -> int:
        return max(0, daily_limit - self.uploads_since(target, utcnow() - timedelta(hours=24)))

    # ---------- relatórios ----------
    # Nunca apagáveis pelo clear: perder esse histórico faria o bot repostar o mesmo clipe
    PROTECTED = ("approved", "rendered", "published")

    def clear(self, statuses: Iterable[str]) -> int:
        """Apaga clipes da fila nos status pedidos. Status protegidos são ignorados."""
        statuses = [st for st in statuses if st in STATUSES and st not in self.PROTECTED]
        if not statuses:
            return 0
        marks = ",".join("?" * len(statuses))
        cur = self.conn.execute(f"DELETE FROM clips WHERE status IN ({marks})", statuses)
        self.conn.commit()
        return cur.rowcount

    def stats(self) -> dict[str, int]:
        rows = self.conn.execute("SELECT status, COUNT(*) AS n FROM clips GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}

    def export_csv(self, path: str | Path) -> int:
        """Exporta uma linha por upload (granularidade clipe x plataforma) para Power BI/Excel."""
        rows = self.conn.execute(
            """SELECT u.uid, u.target, u.remote_id, u.uploaded_at, c.platform AS source,
                      c.broadcaster, c.title, c.views AS source_views, c.duration, c.score,
                      c.created_at AS clip_created_at, c.discovered_at, c.language
               FROM uploads u JOIN clips c ON c.uid = u.uid ORDER BY u.uploaded_at"""
        ).fetchall()
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            if rows:
                w.writerow(rows[0].keys())
            w.writerows([tuple(r) for r in rows])
        return len(rows)

    def iter_all(self) -> Iterable[sqlite3.Row]:
        return self.conn.execute("SELECT * FROM clips ORDER BY score DESC")
