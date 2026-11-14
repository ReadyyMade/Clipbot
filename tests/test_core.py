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
