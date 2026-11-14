"""Upload para TikTok via Content Posting API (Direct Post, FILE_UPLOAD).

IMPORTANTE: enquanto o app não passar na auditoria do TikTok, posts só podem ser
SELF_ONLY (privados). Depois da auditoria, use o privacy_level retornado por
creator_info (ex.: PUBLIC_TO_EVERYONE).
"""
from __future__ import annotations

import json
import logging
import math
import time
from pathlib import Path

import requests

log = logging.getLogger(__name__)

API = "https://open.tiktokapis.com/v2"
MIN_CHUNK = 5 * 1024 * 1024
MAX_CHUNK = 64 * 1024 * 1024


class TikTokTokenStore:
    """Guarda access/refresh token em JSON e renova automaticamente."""

    def __init__(self, path: str, client_key: str, client_secret: str, session: requests.Session):
        self.path = Path(path)
        self.client_key = client_key
        self.client_secret = client_secret
        self.http = session

    def access_token(self) -> str:
        if not self.path.exists():
            raise FileNotFoundError(f"{self.path} não existe. Rode scripts/tiktok_auth.py")
        tok = json.loads(self.path.read_text(encoding="utf-8"))
        if time.time() < tok.get("expires_at", 0) - 120:
            return tok["access_token"]
        r = self.http.post(
            f"{API}/oauth/token/",
            data={"client_key": self.client_key, "client_secret": self.client_secret,
                  "grant_type": "refresh_token", "refresh_token": tok["refresh_token"]},
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
        )
        r.raise_for_status()
        self.save(r.json())
        return r.json()["access_token"]

    def save(self, body: dict) -> None:
        if "access_token" not in body:
            raise RuntimeError(f"TikTok OAuth sem access_token: {body}")
        body["expires_at"] = time.time() + int(body.get("expires_in", 0))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(body, indent=2), encoding="utf-8")


def chunk_plan(size: int) -> tuple[int, int]:
    """(chunk_size, total_chunks) seguindo as regras da API: arquivos < 5MB vão inteiros;
    o último chunk absorve o resto (pode passar de chunk_size)."""
    if size <= MIN_CHUNK:
        return size, 1
    chunk = min(MAX_CHUNK, max(MIN_CHUNK, 10 * 1024 * 1024))
    return chunk, max(1, math.floor(size / chunk))


class TikTokPublisher:
    name = "tiktok"

    def __init__(self, token_file: str, client_key: str, client_secret: str,
                 privacy: str = "SELF_ONLY", session: requests.Session | None = None):
        self.http = session or requests.Session()
        self.tokens = TikTokTokenStore(token_file, client_key, client_secret, self.http)
        self.privacy = privacy

    def _post(self, path: str, payload: dict) -> dict:
        r = self.http.post(
            f"{API}{path}",
            json=payload,
            headers={"Authorization": f"Bearer {self.tokens.access_token()}",
                     "Content-Type": "application/json; charset=UTF-8"},
            timeout=30,
        )
        body = r.json() if r.content else {}
        err = body.get("error", {})
        if r.status_code >= 400 or (err.get("code") not in (None, "ok")):
            raise RuntimeError(f"TikTok {path}: {r.status_code} {err}")
        return body.get("data", {})

    def creator_info(self) -> dict:
        return self._post("/post/publish/creator_info/query/", {})

    def upload(self, video: Path, caption: str) -> str:
        info = self.creator_info()
        allowed = info.get("privacy_level_options") or []
        privacy = self.privacy if not allowed or self.privacy in allowed else allowed[0]
        size = video.stat().st_size
        chunk, total = chunk_plan(size)
        data = self._post("/post/publish/video/init/", {
            "post_info": {"title": caption, "privacy_level": privacy,
                          "disable_duet": False, "disable_comment": False, "disable_stitch": False},
            "source_info": {"source": "FILE_UPLOAD", "video_size": size,
                            "chunk_size": chunk, "total_chunk_count": total},
        })
        publish_id, upload_url = data["publish_id"], data["upload_url"]
        with open(video, "rb") as f:
            for i in range(total):
                start = i * chunk
                end = size - 1 if i == total - 1 else start + chunk - 1
                f.seek(start)
                part = f.read(end - start + 1)
                r = self.http.put(upload_url, data=part, timeout=300, headers={
                    "Content-Type": "video/mp4",
                    "Content-Length": str(len(part)),
                    "Content-Range": f"bytes {start}-{end}/{size}",
                })
                if r.status_code not in (200, 201, 206):
                    raise RuntimeError(f"TikTok upload chunk {i}: {r.status_code} {r.text[:300]}")
        status = self.wait(publish_id)
        log.info("tiktok: publish_id=%s status=%s privacy=%s", publish_id, status, privacy)
        return publish_id

    def wait(self, publish_id: str, timeout_s: int = 300) -> str:
        deadline = time.time() + timeout_s
        status = "UNKNOWN"
        while time.time() < deadline:
            status = self._post("/post/publish/status/fetch/", {"publish_id": publish_id}).get("status", status)
            if status == "PUBLISH_COMPLETE":
                return status
            if status == "FAILED":
                raise RuntimeError(f"TikTok publicação falhou ({publish_id})")
            time.sleep(5)
        return status  # ainda processando; o TikTok conclui de forma assíncrona
