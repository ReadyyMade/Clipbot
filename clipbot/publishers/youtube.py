"""Upload para YouTube (Shorts) via YouTube Data API v3.

Shorts não têm endpoint próprio: é um upload normal de vídeo vertical com até 3 min.
Cota: a cota padrão do projeto é 10.000 unidades/dia e cada videos.insert consome a maior
parte disso por upload (confira o valor atual no Google Cloud Console). Para postar mais,
solicite aumento de cota (exige auditoria do app).
"""
from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger(__name__)

# upload = enviar vídeos; readonly = só para confirmar qual canal está conectado
SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
]


class YouTubePublisher:
    name = "youtube"

    def __init__(self, token_file: str, privacy: str = "public", category_id: str = "20"):
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        if not Path(token_file).exists():
            raise FileNotFoundError(f"{token_file} não existe. Rode scripts/youtube_auth.py")
        creds = Credentials.from_authorized_user_file(token_file, SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            Path(token_file).write_text(creds.to_json(), encoding="utf-8")
        self.yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
        self.privacy = privacy
        self.category_id = category_id

    def upload(self, video: Path, title: str, description: str, tags: list[str]) -> str:
        from googleapiclient.http import MediaFileUpload

        body = {
            "snippet": {"title": title, "description": description, "tags": tags,
                        "categoryId": self.category_id},
            "status": {"privacyStatus": self.privacy, "selfDeclaredMadeForKids": False},
        }
        media = MediaFileUpload(str(video), mimetype="video/mp4", chunksize=-1, resumable=True)
        req = self.yt.videos().insert(part="snippet,status", body=body, media_body=media)
        response = None
        while response is None:
            _, response = req.next_chunk()
        video_id = response["id"]
        log.info("youtube: publicado https://youtube.com/shorts/%s", video_id)
        return video_id
