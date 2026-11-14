import shutil
import subprocess
from datetime import timedelta
from pathlib import Path

import pytest

from clipbot import text
from clipbot.config import DEFAULTS, Config, _merge
from clipbot.db import Database
from clipbot.models import Clip, utcnow
from clipbot.pipeline import Pipeline
from clipbot.publishers.tiktok import MIN_CHUNK, chunk_plan
from clipbot.ranking import collapse_duplicates, rank, score
from clipbot.render import render_vertical
from clipbot.sources.kick import KickSource
from clipbot.sources.twitch import TwitchSource

NOW = utcnow()


def mk(cid="a", views=1000, age_h=1.0, who="Gaules", platform="twitch", dur=30, lang="pt"):
    return Clip(platform=platform, clip_id=cid, url=f"https://clips.twitch.tv/{cid}", title=f"clip {cid}",
                broadcaster=who, views=views, duration=dur, created_at=NOW - timedelta(hours=age_h),
                language=lang)


def cfg(**over):
    return _merge(DEFAULTS, over)


def test_score_favors_velocity():
    fresh = score(mk(views=5000, age_h=1), now=NOW)
    old = score(mk(views=20000, age_h=20), now=NOW)
    assert fresh > old


def test_platform_weight():
    assert score(mk(platform="kick"), {"kick": 2.0}, NOW) == pytest.approx(2 * score(mk(), {}, NOW))


def test_filters_and_reasons():
    c = cfg(discovery={"languages": ["pt"], "min_views": 100},
            streamers={"mode": "open", "allowlist": [], "blocklist": ["banido"]})
    clips = [mk("ok"), mk("en", lang="en"), mk("low", views=10), mk("ban", who="Banido"), mk("long", dur=120)]
    ok, rej = rank(clips, c, NOW)
    assert [x.clip_id for x in ok] == ["ok"]
    assert rej == {"idioma": 1, "poucas views": 1, "blocklist": 1, "duração": 1}


def test_allowlist():
    c = cfg(streamers={"mode": "allowlist", "allowlist": ["casimiro"], "blocklist": []})
    ok, _ = rank([mk("a", who="Casimiro"), mk("b", who="Outro")], c, NOW)
    assert [x.broadcaster for x in ok] == ["Casimiro"]


def test_collapse_same_moment_keeps_best():
    a, b, c = mk("a", views=900), mk("b", views=3000), mk("c", views=500, age_h=3)
    b.created_at = a.created_at + timedelta(seconds=40)
    for x in (a, b, c):
        x.score = score(x, now=NOW)
    kept = collapse_duplicates([a, b, c], 120)
    assert {k.clip_id for k in kept} == {"b", "c"}


def test_youtube_title_limit_and_credit():
    c = mk()
    c.title = "x" * 300
    t = text.youtube_title(c, ["#GTA6"])
    assert len(t) <= 100 and t.endswith("| Gaules #GTA6")
    assert "twitch.tv/gaules" in text.description(c, ["#GTA6"])
    assert "#Shorts" not in text.tiktok_caption(c, ["#GTA6", "#Shorts"])


def test_chunk_plan():
    assert chunk_plan(1000) == (1000, 1)
    size, n = chunk_plan(25 * 1024 * 1024)
    assert size >= MIN_CHUNK and n == 2  # último chunk absorve o resto


def test_parsers():
    t = TwitchSource.parse({"id": "X", "url": "u", "title": " t ", "broadcaster_name": "B", "view_count": 7,
                            "duration": 20.5, "created_at": "2026-11-19T10:00:00Z", "language": "pt"})
    assert (t.uid, t.views, t.title, t.created_at.tzinfo is not None) == ("twitch:X", 7, "t", True)
    k = KickSource.parse({"id": "clip_1", "title": "k", "views": 3, "duration": 15,
                          "created_at": "2026-11-19T10:00:00Z", "video_url": "m3u8",
                          "channel": {"slug": "loud", "username": "LOUD"}})
    assert k.url == "https://kick.com/loud/clips/clip_1" and k.broadcaster == "LOUD"
    assert KickSource.parse({"id": "x"}) is None


class FakeSource:
    name = "twitch"

    def __init__(self, clips):
        self.clips = clips

    def resolve_game_id(self, name):
        return "1"

    def fetch_clips(self, game_id, since):
        return self.clips


class FakePublisher:
    def __init__(self):
        self.calls = []

    def upload(self, video, *a):
        self.calls.append(video)
        return f"remote{len(self.calls)}"


def test_pipeline_flow(tmp_path, monkeypatch):
    c = Config(cfg(paths={"data_dir": str(tmp_path)},
                   discovery={"max_per_streamer_per_day": 1},
                   publishing={"min_minutes_between_posts": 0}))
    clips = [mk("a", views=5000), mk("b", views=4000, age_h=5), mk("c", views=3000, who="Outro", age_h=2)]
    yt, tt = FakePublisher(), FakePublisher()
    p = Pipeline(c, Database(tmp_path / "t.db"), sources=[FakeSource(clips)],
                 publishers={"youtube": yt, "tiktok": tt})
    s = p.discover()
    assert s["novos"] == 3 and s["auto_aprovados"] == 0       # require_approval=True
    assert p.discover()["novos"] == 0                          # idempotente

    assert p.approve_top(3) == 2                               # limite 1 por streamer/dia
    monkeypatch.setattr(p, "prepare", lambda row: tmp_path / f"{row['clip_id']}.mp4")
    res = p.process(max_items=5)
    assert len(res) == 2 and len(yt.calls) == len(tt.calls) == 2
    assert p.db.stats().get("published") == 2
    assert p.db.export_csv(tmp_path / "u.csv") == 4


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="ffmpeg ausente")
def test_render_vertical(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "2", "-shortest", str(src)], check=True)
    c = mk()
    c.title = "Ele fugiu da polícia: 5 estrelas 'insano'"
    out = render_vertical(src, tmp_path / "out.mp4", DEFAULTS["render"],
                          title=text.overlay_title(c), credit=text.overlay_credit(c))
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(out)],
                           capture_output=True, text=True, check=True).stdout.strip()
    assert probe == "1080,1920"


def test_filter_uses_relative_textfiles():
    from clipbot.render import build_command
    cmd = build_command(Path("in.mp4"), Path("out.mp4"), DEFAULTS["render"],
                        Path("data/rendered/a.title.txt"), Path("data/rendered/a.credit.txt"),
                        Path("data/rendered/_font.ttf"))
    graph = cmd[cmd.index("-filter_complex") + 1]
    assert "textfile='a.title.txt'" in graph and "textfile='a.credit.txt'" in graph
    assert "data/" not in graph
    assert "fontfile='_font.ttf'" in graph


class BrokenPublisher:
    def upload(self, *a):
        raise RuntimeError("sem token")


def test_one_platform_failing_does_not_block_other(tmp_path, monkeypatch):
    c = Config(cfg(paths={"data_dir": str(tmp_path)}, publishing={"min_minutes_between_posts": 0}))
    yt = FakePublisher()
    p = Pipeline(c, Database(tmp_path / "t.db"), sources=[FakeSource([mk("a", views=5000)])],
                 publishers={"youtube": yt, "tiktok": BrokenPublisher()})
    p.discover(); p.approve_top(1)
    monkeypatch.setattr(p, "prepare", lambda row: tmp_path / "a.mp4")
    res = p.process()
    assert res[0]["publicado_em"] == ["youtube"] and "tiktok" in res[0]["erros"]
    assert p.db.get("twitch:a")["status"] == "rendered"
    p._publishers["tiktok"] = FakePublisher()
    p.process()                                   # retenta só o TikTok
    assert len(yt.calls) == 1 and p.db.get("twitch:a")["status"] == "published"


# ---------- busca por streamers ao vivo no idioma (item 1) ----------
class FakeResponse:
    def __init__(self, body, status=200):
        self._body, self.status_code, self.headers = body, status, {}

    def json(self):
        return self._body

    def raise_for_status(self):
        assert self.status_code < 400


class FakeTwitchHTTP:
    """Simula a API da Twitch e registra as chamadas."""

    def __init__(self):
        self.calls = []

    def post(self, url, **kw):
        return FakeResponse({"access_token": "t", "expires_in": 3600})

    def get(self, url, params=None, **kw):
        path = url.rsplit("/", 1)[-1]
        self.calls.append((path, dict(params or {})))
        if path == "streams":
            return FakeResponse({"data": [
                {"user_id": "10", "user_name": "Gaules", "language": "pt", "viewer_count": 900},
                {"user_id": "11", "user_name": "Outro", "language": "pt", "viewer_count": 50},
            ], "pagination": {}})
        if path == "users":
            return FakeResponse({"data": [{"id": "77", "login": "parceiro", "display_name": "Parceiro"}]})
        if path == "clips" and "broadcaster_id" in params:
            bid = params["broadcaster_id"]
            return FakeResponse({"data": [
                {"id": f"c{bid}", "url": "u", "title": "t", "broadcaster_name": f"B{bid}", "game_id": "1",
                 "view_count": 800, "duration": 30, "created_at": NOW.isoformat(), "language": "pt"},
                {"id": f"x{bid}", "url": "u", "title": "outro jogo", "broadcaster_name": f"B{bid}",
                 "game_id": "999", "view_count": 9999, "duration": 30, "created_at": NOW.isoformat()},
            ], "pagination": {}})
        return FakeResponse({"data": [], "pagination": {}})  # top global vazio


def twitch_pipeline(tmp_path, **over):
    http = FakeTwitchHTTP()
    c = Config(cfg(paths={"data_dir": str(tmp_path)}, game={"twitch_game_id": "1"}, **over))
    p = Pipeline(c, Database(tmp_path / "t.db"), sources=[TwitchSource("id", "secret", session=http)],
                 publishers={})
    return p, http


def test_discovery_scans_live_streamers_in_language(tmp_path):
    p, http = twitch_pipeline(tmp_path, discovery={"languages": ["pt"]})
    s = p.discover()
    assert s["novos"] == 2                                   # 1 clipe por streamer; outro jogo é ignorado
    streams = [prm for path, prm in http.calls if path == "streams"][0]
    assert streams["language"] == ["pt"] and streams["game_id"] == "1"
    assert {prm["broadcaster_id"] for path, prm in http.calls if "broadcaster_id" in prm} == {"10", "11"}
    # quem esteve ao vivo fica registrado: continua sendo varrido depois que a live acaba
    assert len(p.db.recent_streamers("twitch", NOW - timedelta(hours=1), 10)) == 2


def test_allowlist_is_always_scanned(tmp_path):
    p, http = twitch_pipeline(tmp_path, discovery={"languages": []},
                              streamers={"mode": "allowlist", "allowlist": ["Parceiro"], "blocklist": []})
    p.fetch()
    assert not [1 for path, _ in http.calls if path == "streams"]      # sem idioma, não lista lives
    assert [prm["broadcaster_id"] for path, prm in http.calls if "broadcaster_id" in prm] == ["77"]


def test_scan_limit_zero_disables_streamer_scan(tmp_path):
    p, http = twitch_pipeline(tmp_path, discovery={"languages": ["pt"], "streamer_scan_limit": 0})
    p.fetch()
    assert [path for path, _ in http.calls] == ["clips"]


# ---------- loop resiliente (item 2) ----------
def test_run_loop_survives_errors(tmp_path):
    from clipbot.cli import run_cycle, run_loop

    class Boom(Pipeline):
        processed = 0

        def discover(self):
            raise RuntimeError("twitch fora do ar")

        def process(self, dry_run=False, max_items=1):
            Boom.processed += 1
            return []

    p = Boom(Config(cfg(paths={"data_dir": str(tmp_path)})), Database(tmp_path / "t.db"), sources=[], publishers={})
    assert run_cycle(p) is False and Boom.processed == 1     # busca falhou, publicação rodou mesmo assim
    assert run_loop(p, 1, once=True) == 1
    naps = []

    def fake_sleep(sec):
        naps.append(sec)
        if len(naps) == 3:
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        run_loop(p, 15, sleep=fake_sleep)
    assert naps == [900, 900, 900]                           # seguiu rodando após 3 falhas


# ---------- fila honesta (item 3) ----------
def test_old_clips_expire_and_scores_decay(tmp_path):
    c = Config(cfg(paths={"data_dir": str(tmp_path)}, discovery={"max_age_hours": 36}))
    db = Database(tmp_path / "t.db")
    old, fresh = mk("old", views=90000, age_h=40), mk("fresh", views=2000, age_h=1)
    for x in (old, fresh):
        x.score = 99999.0                                    # score "congelado" de uma busca antiga
        db.upsert_clip(x)
    p = Pipeline(c, db, sources=[FakeSource([])], publishers={})
    assert p.discover()["expirados"] == 1
    assert db.get("twitch:old")["status"] == "expired"
    assert db.get("twitch:fresh")["score"] == pytest.approx(score(fresh), rel=0.01)
    assert p.approve_top(5) == 1                             # o expirado não volta para a fila


def test_view_update_does_not_touch_status_timestamp(tmp_path):
    db = Database(tmp_path / "t.db")
    clip = mk("a")
    db.upsert_clip(clip)
    before = db.get("twitch:a")["updated_at"]
    clip.views = 9999
    db.upsert_clip(clip)
    row = db.get("twitch:a")
    assert row["views"] == 9999 and row["updated_at"] == before


# ---------- aprovação automática just-in-time (item 4) ----------
def test_auto_approve_keeps_small_buffer(tmp_path, monkeypatch):
    c = Config(cfg(paths={"data_dir": str(tmp_path)},
                   publishing={"require_approval": False, "auto_approve_buffer": 2,
                               "min_minutes_between_posts": 0}))
    clips = [mk(f"c{i}", views=5000 - i * 100, who=f"S{i}") for i in range(8)]
    yt, tt = FakePublisher(), FakePublisher()
    p = Pipeline(c, Database(tmp_path / "t.db"), sources=[FakeSource(clips)],
                 publishers={"youtube": yt, "tiktok": tt})
    assert p.discover()["auto_aprovados"] == 2               # não aprova os 8 de uma vez
    assert p.discover()["auto_aprovados"] == 0               # estoque cheio
    monkeypatch.setattr(p, "prepare", lambda row: tmp_path / "v.mp4")
    p.process()
    assert p.discover()["auto_aprovados"] == 1               # repõe só a vaga usada
    assert len(p.db.by_status("approved", "rendered")) == 2
