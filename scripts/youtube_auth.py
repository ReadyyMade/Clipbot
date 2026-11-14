"""Gera o token OAuth do YouTube (rode UMA vez, no seu computador, com navegador).

1. Google Cloud Console -> crie projeto -> ative "YouTube Data API v3"
2. Tela de consentimento OAuth (publique o app para o token não expirar em 7 dias)
3. Credenciais -> ID do cliente OAuth -> "App para computador" -> baixe o JSON
4. Salve em secrets/youtube_client_secret.json e rode: python scripts/youtube_auth.py

No navegador, escolha a conta E o canal que vão receber os vídeos.
Ao final, o script mostra o canal conectado para você conferir.
"""
import os
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clipbot.publishers.youtube import SCOPES  # noqa: E402

secrets = os.environ.get("YOUTUBE_CLIENT_SECRETS", "secrets/youtube_client_secret.json")
token = Path(os.environ.get("YOUTUBE_TOKEN_FILE", "secrets/youtube_token.json"))

flow = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES)
creds = flow.run_local_server(port=8080, prompt="select_account consent", access_type="offline")

yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
items = yt.channels().list(part="snippet", mine=True).execute().get("items", [])
if not items:
    print("\nATENÇÃO: a conta autorizada NÃO tem canal do YouTube.")
    print("Crie o canal nessa conta (youtube.com -> Criar canal) ou rode de novo")
    print("escolhendo a conta/canal certo. Token NÃO foi salvo.")
    sys.exit(1)

token.parent.mkdir(parents=True, exist_ok=True)
token.write_text(creds.to_json(), encoding="utf-8")
ch = items[0]
print(f"\nCanal conectado: {ch['snippet']['title']}  (https://youtube.com/channel/{ch['id']})")
print(f"Token salvo em {token}")
