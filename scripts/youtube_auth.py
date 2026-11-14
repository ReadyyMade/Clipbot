"""Gera o token OAuth do YouTube (rode UMA vez, no seu computador, com navegador).

1. Google Cloud Console -> crie projeto -> ative "YouTube Data API v3"
2. Tela de consentimento OAuth (adicione sua conta como usuário de teste)
3. Credenciais -> ID do cliente OAuth -> "App para computador" -> baixe o JSON
4. Salve em secrets/youtube_client_secret.json e rode: python scripts/youtube_auth.py
"""
import os
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]

secrets = os.environ.get("YOUTUBE_CLIENT_SECRETS", "secrets/youtube_client_secret.json")
token = Path(os.environ.get("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json"))

flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
creds = flow.run_local_server(port=8080, prompt="consent", access_type="offline")
token.parent.mkdir(parents=True, exist_ok=True)
token.write_text(creds.to_json(), encoding="utf-8")
print(f"Token salvo em {token}")
