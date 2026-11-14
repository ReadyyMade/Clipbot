"""Gera o token OAuth do TikTok (Login Kit, escopo video.publish).

Uso:
  python scripts/tiktok_auth.py url            # imprime a URL de autorização
  python scripts/tiktok_auth.py code <CODE>    # troca o ?code= do redirect pelo token

O redirect URI precisa estar cadastrado no app em developers.tiktok.com.
"""
import os
import secrets
import sys
import urllib.parse

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from clipbot.publishers.tiktok import API, TikTokTokenStore  # noqa: E402

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

KEY = os.environ["TIKTOK_CLIENT_KEY"]
SECRET = os.environ["TIKTOK_CLIENT_SECRET"]
REDIRECT = os.environ["TIKTOK_REDIRECT_URI"]
TOKEN_FILE = os.environ.get("TIKTOK_TOKEN_FILE", "secrets/tiktok_token.json")

if len(sys.argv) >= 2 and sys.argv[1] == "url":
    q = urllib.parse.urlencode({
        "client_key": KEY, "scope": "user.info.basic,video.publish", "response_type": "code",
        "redirect_uri": REDIRECT, "state": secrets.token_urlsafe(16),
    })
    print(f"https://www.tiktok.com/v2/auth/authorize/?{q}")
elif len(sys.argv) == 3 and sys.argv[1] == "code":
    http = requests.Session()
    r = http.post(f"{API}/oauth/token/", data={
        "client_key": KEY, "client_secret": SECRET, "code": sys.argv[2],
        "grant_type": "authorization_code", "redirect_uri": REDIRECT,
    }, headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=20)
    r.raise_for_status()
    TikTokTokenStore(TOKEN_FILE, KEY, SECRET, http).save(r.json())
    print(f"Token salvo em {TOKEN_FILE}")
else:
    print(__doc__)
