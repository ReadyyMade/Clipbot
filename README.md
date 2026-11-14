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
- **Fila honesta**: a cada busca o score é recalculado com a idade atual, e clipes com mais de
  `max_age_hours` (36h) sem publicação expiram. A coluna `idade` do `queue` mostra isso.

## Como a busca funciona

A Twitch só devolve os clipes **mais vistos do mundo** por jogo, quase todos em inglês. Filtrar
idioma depois disso deixa pouquíssimo conteúdo BR. Por isso o bot soma duas buscas:

1. **Top global** do jogo (até 500 clipes).
2. **Por streamer**: lista quem está ao vivo no jogo nos idiomas de `discovery.languages`
   (até `streamer_scan_limit` canais, maiores audiências primeiro) e busca os clipes de cada um.
   Quem saiu do ar continua sendo acompanhado durante a janela de busca, e os canais da
   `streamers.allowlist` são sempre incluídos.

## Setup rápido

Requisitos: **Python 3.12** (versões muito novas, como 3.15, ainda não têm pacotes prontos
para todas as dependências), **ffmpeg** e Git.

**Windows (CMD):**

```bat
git clone -b claude/elegant-feynman-b0vfp8 https://github.com/henriiikk/Clipbot.git
cd Clipbot
py -3.12 -m venv .venv
.venv\Scripts\activate
python -m pip install -r requirements.txt
winget install ffmpeg
copy config.example.yaml config.yaml
copy .env.example .env
```

**Mac/Linux:**

```bash
python3.12 -m venv .venv && source .venv/bin/activate
python -m pip install -r requirements-dev.txt
cp config.example.yaml config.yaml && cp .env.example .env
```

Atualizar para a versão mais recente: `git pull`.

### Credenciais

| Plataforma | Onde | Observação |
|---|---|---|
| Twitch | dev.twitch.tv/console/apps | Client ID + Secret. Gratuito, sem aprovação. |
| YouTube | Google Cloud Console → YouTube Data API v3 → OAuth "App para computador" → JSON em `secrets/youtube_client_secret.json` | Publique o app OAuth ("Em produção"), senão o token expira em 7 dias. Rode `python scripts/youtube_auth.py`: ele pede a conta, mostra o **canal conectado** e só salva o token se a conta tiver canal. Cota padrão ≈ 6 uploads/dia. **Projeto não auditado só publica como privado**: peça a auditoria (YouTube API Services Audit). |
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
python -m clipbot clear                    # limpa a fila (mantém aprovados e publicados)
python -m clipbot stats                    # status da fila e slots restantes
python -m clipbot export --out data/uploads.csv   # base para Power BI
```

Modo 100% automático: `publishing.require_approval: false` no `config.yaml`. Nesse modo o bot
aprova **just-in-time**: mantém só `auto_approve_buffer` (2) clipes aprovados por vez, para cada
vaga ser preenchida pelo melhor clipe do momento, e não por um aprovado horas antes.

Comportamentos importantes:
- **Plataformas independentes**: se o TikTok falhar, o YouTube publica mesmo assim; o clipe fica
  `rendered` e o próximo ciclo tenta só a plataforma que faltou (sem duplicar).
- `approve --top N` também retenta clipes que falharam.
- `min_minutes_between_posts` (padrão 45) faz o `process` **pular** uploads feitos antes do intervalo.
- Desligue no `config.yaml` a plataforma que ainda não tem credencial (`tiktok.enabled: false`).

## Visual do vídeo

1080x1920, vídeo original **inteiro** no centro (sem cortar HUD, minimapa ou webcam), fundo
desfocado, título no topo e crédito `twitch.tv/streamer` embaixo.

| Opção (`render:`) | Padrão | Efeito |
|---|---|---|
| `zoom` | `1.0` | >1 amplia o vídeo central e corta as laterais (máx. 1.78) |
| `max_seconds` | `59` | Duração máxima |
| `font_file` | `""` | Vazio = detecta (Windows: Arial Negrito) |

## Site (privacidade e termos)

A pasta `docs/` é publicada pelo GitHub Pages em https://henriiikk.github.io/Clipbot/ e
atende às exigências do Google (app OAuth e auditoria) e do TikTok:

- Início: `https://henriiikk.github.io/Clipbot/`
- Privacidade: `https://henriiikk.github.io/Clipbot/privacy.html`
- Termos: `https://henriiikk.github.io/Clipbot/terms.html`

## Deploy (24/7)

O bot precisa de estado persistente (SQLite + tokens) e ffmpeg, então **VPS simples > serverless**.

1. Gere os tokens **no seu PC** (`scripts/youtube_auth.py` precisa de navegador) e copie para a
   VPS as pastas/arquivos `secrets/`, `.env` e `config.yaml`.
2. Na VPS:

```bash
docker build -t clipbot .
docker run -d --name clipbot --restart unless-stopped --env-file .env \
  -v $PWD/config.yaml:/app/config.yaml -v $PWD/data:/app/data -v $PWD/secrets:/app/secrets clipbot
docker logs -f clipbot
```

- `.env`, `secrets/`, `data/` e `config.yaml` **não entram na imagem** (`.dockerignore`): chegam
  só por volume/variável, então a imagem pode ser compartilhada sem vazar credenciais.
- O loop `run` não morre com erros pontuais (API fora do ar, banco ocupado): registra o erro e
  tenta de novo no ciclo seguinte. Com 3 ciclos seguidos em erro, o log mostra um `ATENÇÃO`.
- `--restart unless-stopped` religa o bot se a VPS reiniciar.
- Atualize o `yt-dlp` de tempos em tempos (reconstrua a imagem): a Twitch muda e versões antigas param de baixar.

## Riscos e limites (leia antes de ligar o modo automático)

1. **Direitos autorais / política de conteúdo reutilizado.** O clipe pertence ao streamer. Repostar sem
   autorização pode gerar strike, remoção ou desmonetização (YouTube "reused content"). Para um canal
   sustentável use `streamers.mode: allowlist` com streamers que autorizaram, mantenha o crédito e
   considere agregar valor (cortes comentados, compilados, tops do dia).
2. **Música e trilha do jogo** podem disparar Content ID mesmo com autorização do streamer.
3. **Kick** não tem API oficial de clipes; o módulo usa endpoint interno, que pode quebrar
   ou ser bloqueado pelo Cloudflare. Falhas são isoladas (o resto continua rodando).
4. **Cotas**: YouTube ~6 uploads/dia na cota padrão; TikTok tem limite de posts por criador/dia.

## Problemas comuns

| Erro | Causa / solução |
|---|---|
| `No module named 'yaml'` | Dependências fora do `.venv`: ative o `.venv` e rode `python -m pip install -r requirements.txt` |
| `Microsoft Visual C++ 14.0 is required` | Python novo demais (3.15). Use o 3.12 |
| `'cp' não é reconhecido` | No CMD do Windows use `copy` |
| `youtubeSignupRequired` | A conta autorizada não tem canal. Crie o canal e rode `scripts/youtube_auth.py` de novo |
| `secrets/youtube_client_secret.json` não encontrado | JSON fora da pasta ou com nome `.json.json` (`dir secrets` para conferir) |
| `queue` vazia | Filtros apertados para o volume atual: teste `languages: []` e `min_views` menor |
| Parece travado no `process` | Está renderizando (30s a 2min por clipe); os logs mostram a etapa |

## Testes

```bash
python -m pytest -q
```
