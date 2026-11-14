# Clipbot — clipes de GTA VI para Shorts e TikTok

Bot que encontra os clipes de GTA VI que estão **crescendo mais rápido** na Twitch (e, de forma
experimental, na Kick), converte para vertical 9:16 com crédito ao streamer e publica no
YouTube Shorts e no TikTok, respeitando cotas e intervalos.

```
Twitch API ─┐                                      ┌─> YouTube Shorts
            ├─> filtro ─> dedup ─> ranking ─> fila ─> aprovação ─> yt-dlp ─> ffmpeg 9:16 ─┤
Kick (exp.) ┘                         (SQLite)       (você/auto)                          └─> TikTok
```

## Como o ranking funciona

`score = views × peso_plataforma / (idade_horas + 2)^1.5`

- Prioriza **velocidade** (views/hora), não volume acumulado: pega a tendência antes dos outros canais.
- **Dedup do mesmo momento**: vários viewers clipam o mesmo lance; fica só o de maior score
  (mesmo streamer, criados em até `dedup_window_s` segundos).
- **Diversidade**: no máximo `max_per_streamer_per_day` clipes do mesmo streamer em 24h.
- Peso por plataforma equaliza Kick (audiência menor) com Twitch.

## Setup rápido

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt     # precisa de ffmpeg no sistema
cp config.example.yaml config.yaml
cp .env.example .env                     # preencha as credenciais
```

### Credenciais

| Plataforma | Onde | Observação |
|---|---|---|
| Twitch | dev.twitch.tv/console/apps | Client ID + Secret. Gratuito, sem aprovação. |
| YouTube | Google Cloud Console → YouTube Data API v3 → OAuth "App para computador" | Rode `python scripts/youtube_auth.py` uma vez no seu PC. Cota padrão ≈ 6 uploads/dia. Para publicar vídeos públicos via API, o projeto pode precisar passar na auditoria do Google. |
| TikTok | developers.tiktok.com → app com Login Kit + Content Posting API (`video.publish`) | `python scripts/tiktok_auth.py url` → autorize → `python scripts/tiktok_auth.py code <CODE>`. **Sem auditoria, só posta como privado (SELF_ONLY).** |

## Uso

```bash
python -m clipbot discover                 # busca e ranqueia
python -m clipbot queue                    # vê a fila (score, views, link)
python -m clipbot approve --top 5          # aprova os 5 melhores
python -m clipbot approve twitch:AbCdEf    # ou um específico
python -m clipbot reject  twitch:XyZ
python -m clipbot process --dry-run        # só renderiza em data/rendered/ para conferir
python -m clipbot process --max 2          # publica de verdade
python -m clipbot run --interval 15        # loop contínuo
python -m clipbot stats                    # status da fila e slots restantes
python -m clipbot export --out data/uploads.csv   # base para Power BI
```

Modo 100% automático: `publishing.require_approval: false` no `config.yaml`.

## Deploy (24/7)

O bot precisa de estado persistente (SQLite + tokens) e ffmpeg, então **VPS simples > serverless**.

```bash
docker build -t clipbot .
docker run -d --name clipbot --env-file .env \
  -v $PWD/config.yaml:/app/config.yaml -v $PWD/data:/app/data -v $PWD/secrets:/app/secrets clipbot
```

## Riscos e limites (leia antes de ligar o modo automático)

1. **Direitos autorais / política de conteúdo reutilizado.** O clipe pertence ao streamer. Repostar sem
   autorização pode gerar strike, remoção ou desmonetização (YouTube "reused content"). Para um canal
   sustentável use `streamers.mode: allowlist` com streamers que autorizaram, mantenha o crédito e
   considere agregar valor (cortes comentados, compilados, tops do dia).
2. **Música e trilha do jogo** podem disparar Content ID mesmo com autorização do streamer.
3. **Kick** não tem API oficial de clipes; o módulo usa endpoint interno, que pode quebrar
   ou ser bloqueado pelo Cloudflare. Falhas são isoladas (o resto continua rodando).
4. **Cotas**: YouTube ~6 uploads/dia na cota padrão; TikTok tem limite de posts por criador/dia.

## Testes

```bash
python -m pytest -q
```
