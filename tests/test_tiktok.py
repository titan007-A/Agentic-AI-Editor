import io
import os
import plistlib
import socket
import subprocess
import threading
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
import yt_dlp
from PIL import Image
from pydantic import ValidationError

from agentic_editor import autopilot, cleanup, download, research, tiktok, websearch
from agentic_editor.db import make_sessionmaker
from agentic_editor.settings import ROOT, Settings, get_settings
from tests.test_research import Planner, hit

TUNNEL = "socks5h://127.0.0.1:1080"
VIDEO = "https://www.tiktok.com/@khaby.lame/video/7234567890123456789"


@pytest.fixture
def gateway(monkeypatch):
    """The TikTok gateway set, as TIKTOK_PROXY in .env sets it."""
    monkeypatch.setattr(get_settings(), "tiktok_proxy", TUNNEL)
    return TUNNEL


@pytest.fixture
def no_gateway(monkeypatch):
    monkeypatch.setattr(get_settings(), "tiktok_proxy", "")


class FakeSite:
    """Stands in for yt-dlp: notes the options and link each YoutubeDL is given."""

    def __init__(self, fail=None, info=None):
        self.made: list[dict] = []
        self.asked: list[str] = []
        site = self

        class YoutubeDL:
            def __init__(self, options):
                site.made.append(options)
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def extract_info(self, url, download=False):
                site.asked.append(url)
                if fail:
                    raise yt_dlp.utils.DownloadError(fail)
                if download:
                    Path(self.options["outtmpl"].replace("%(ext)s", "mp4")).write_text("video")
                return info or {"extractor_key": "TikTok", "id": "7234567890123456789",
                                "title": "A video"}  # fmt: skip

        self.YoutubeDL = YoutubeDL


def test_tiktok_links_are_known_by_their_host():
    for link in (VIDEO, "https://vm.tiktok.com/ZMabc123/", "https://vt.tiktok.com/ZSx/",
                 "https://tiktok.com/@a/video/1", "https://m.tiktok.com/v/7234567890.html",
                 "https://www.tiktokv.com/share/video/7234567890/"):  # fmt: skip
        assert tiktok.is_link(link), link
    for link in ("https://www.instagram.com/reel/A/", "https://nottiktok.com/@a/video/1",
                 "https://tiktok.com.example.org/@a/video/1", "https://x.test/v"):  # fmt: skip
        assert not tiktok.is_link(link), link
    # Its video pages are www.tiktok.com's, the way yt-dlp reads them as TikTok's.
    assert tiktok.link("https://tiktok.com/@a/video/123456") == (
        "https://www.tiktok.com/@a/video/123456"
    )
    assert tiktok.link("https://m.tiktok.com/v/7234567890.html") == (
        "https://www.tiktok.com/@_/video/7234567890"
    )
    assert tiktok.link("https://vm.tiktok.com/ZMabc/") == "https://vm.tiktok.com/ZMabc/"


def test_the_gateway_setting_takes_a_proxy_or_nothing():
    for value in ("", "direct", TUNNEL, " socks5://10.0.0.2:1081 ", "http://proxy:3128"):
        assert Settings(tiktok_proxy=value).tiktok_proxy == value.strip()
    for value in ("127.0.0.1:1080", "socks5h://127.0.0.1", "ftp://x:1", "yes"):
        with pytest.raises(ValidationError):
            Settings(tiktok_proxy=value)


def test_only_tiktok_links_go_through_the_gateway(gateway):
    options = {"quiet": True}
    assert tiktok.routed(VIDEO, options) == {"quiet": True, "proxy": TUNNEL}
    assert tiktok.routed("https://www.youtube.com/shorts/abcdefghijk", options) is options


def test_without_a_gateway_a_tiktok_link_is_refused_at_once(no_gateway, tmp_path, monkeypatch):
    site = FakeSite()
    monkeypatch.setattr(yt_dlp, "YoutubeDL", site.YoutubeDL)
    began = time.monotonic()
    with pytest.raises(tiktok.GatewayError, match="TikTok gateway"):
        download.fetch(VIDEO, tmp_path)
    assert time.monotonic() - began < 1 and site.made == []  # nothing asked, nothing waited
    download.fetch("https://x.test/v", tmp_path)  # every other link as before
    assert "proxy" not in site.made[0]


def test_direct_reaches_tiktok_without_a_proxy(monkeypatch, tmp_path):
    monkeypatch.setattr(get_settings(), "tiktok_proxy", tiktok.DIRECT)
    site = FakeSite()
    monkeypatch.setattr(yt_dlp, "YoutubeDL", site.YoutubeDL)
    download.fetch(VIDEO, tmp_path)
    assert all("proxy" not in options for options in site.made)


def test_a_tiktok_download_goes_through_the_gateway_and_others_never(
    gateway, tmp_path, monkeypatch
):
    site = FakeSite()
    monkeypatch.setattr(yt_dlp, "YoutubeDL", site.YoutubeDL)
    source = download.fetch("https://tiktok.com/@khaby.lame/video/7234567890123456789", tmp_path)
    assert source.path.name == "source.mp4" and source.title == "A video"
    # The lookup and the download both, and the link as yt-dlp knows TikTok's.
    assert [o.get("proxy") for o in site.made] == [TUNNEL, TUNNEL]
    assert site.asked == [VIDEO, VIDEO]
    assert all("cookiefile" not in o for o in site.made)  # never the YouTube login
    site.made.clear()
    download.fetch("https://www.youtube.com/watch?v=abcdefghijk", tmp_path / "other")
    assert site.made and all("proxy" not in o for o in site.made)


def test_a_failure_on_the_way_to_tiktok_says_which_part_is_down(gateway, tmp_path, monkeypatch):
    refused = "Unable to download webpage: [Errno 111] Connection refused (caused by ...)"
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeSite(fail=refused).YoutubeDL)
    monkeypatch.setattr(tiktok, "tunnel", lambda through: "nothing answers at 127.0.0.1:1080")
    with pytest.raises(tiktok.GatewayError, match="tunnel is down") as down:
        download.fetch(VIDEO, tmp_path)
    assert "nothing answers at 127.0.0.1:1080" in str(down.value)

    timed_out = ("Unable to download webpage: (<SocksHTTPSConnection(host='www.tiktok.com', "
                 "port=443) at 0x7f>, 'Connection to www.tiktok.com timed out. (connect "
                 "timeout=20.0)')")  # fmt: skip
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeSite(fail=timed_out).YoutubeDL)
    monkeypatch.setattr(tiktok, "tunnel", lambda through: None)
    with pytest.raises(tiktok.GatewayError, match="VPN is most likely off") as vpn:
        download.fetch(VIDEO, tmp_path)
    assert "(Connection to www.tiktok.com timed out)" in str(vpn.value)

    # TikTok's own answer stays as TikTok said it.
    gone = "[TikTok] 7234567890123456789: Video not available"
    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeSite(fail=gone).YoutubeDL)
    with pytest.raises(yt_dlp.utils.DownloadError, match="Video not available"):
        download.fetch(VIDEO, tmp_path)


class SocksServer:
    """A SOCKS5 server on this machine that only greets, as an SSH tunnel's -D does."""

    def __init__(self, reply=b"\x05\x00"):
        self.sock = socket.socket()
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen()
        self.port = self.sock.getsockname()[1]
        self.reply = reply
        threading.Thread(target=self.serve, daemon=True).start()

    def serve(self):
        while True:
            try:
                conn, _ = self.sock.accept()
            except OSError:
                return
            with conn:
                if conn.recv(3) == b"\x05\x01\x00":
                    conn.sendall(self.reply)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_the_tunnel_is_up_when_a_socks_proxy_answers_there():
    up = SocksServer()
    assert tiktok.tunnel(f"socks5h://127.0.0.1:{up.port}") is None
    assert "nothing answers" in tiktok.tunnel(f"socks5h://127.0.0.1:{free_port()}")
    other = SocksServer(reply=b"HTTP/1.1 400 Bad Request\r\n")
    assert "not as the tunnel does" in tiktok.tunnel(f"socks5h://127.0.0.1:{other.port}")


def test_the_system_page_says_whether_the_tunnel_is_up_and_tiktok_answers():
    def settings(value):
        return Settings(tiktok_proxy=value)

    assert tiktok.status(settings(""))["set"] is False
    down = tiktok.status(settings(TUNNEL), probe=lambda p: "nothing answers", check=None)
    assert (down["tunnel"], down["tiktok"]) == (False, False)
    assert down["tunnel_said"] == "nothing answers"
    vpn_off = tiktok.status(settings(TUNNEL), probe=lambda p: None, check=lambda p: "timed out")
    assert (vpn_off["tunnel"], vpn_off["tiktok"], vpn_off["tiktok_said"]) == (
        True, False, "timed out",
    )  # fmt: skip
    asked = []
    fine = tiktok.status(settings(TUNNEL), probe=lambda p: None, check=asked.append)
    assert fine["tunnel"] and fine["tiktok"] and asked == [TUNNEL]
    assert isinstance(fine["seconds"], float)
    direct = tiktok.status(settings("direct"), probe=None, check=lambda p: None)
    assert direct["direct"] and direct["tunnel"] is None and direct["tiktok"]


@pytest.mark.parametrize(
    ("link", "code"),
    [
        (VIDEO, "tt:7234567890123456789"),
        (f"{VIDEO}?is_from_webapp=1&sender_device=pc", "tt:7234567890123456789"),
        ("https://tiktok.com/@a.b_c/video/7234567890123456789", "tt:7234567890123456789"),
        # as the engines link some: another TikTok host, the account left out
        ("https://support16-normal-useast1a.tiktok.com/@/video/7465287121637526790",
         "tt:7465287121637526790"),
        ("https://m.tiktok.com/v/7234567890123456789.html", "tt:7234567890123456789"),
        ("https://www.tiktokv.com/share/video/7234567890123456789/", "tt:7234567890123456789"),
        ("https://vm.tiktok.com/ZMabc123/", None),  # a short link names no video
        ("https://www.tiktok.com/@khaby.lame", None),
        ("https://www.tiktok.com/@khaby.lame/photo/7234567890123456789", None),
        ("https://www.tiktok.com/discover/bryan-adams-live", None),
    ],
)  # fmt: skip
def test_a_tiktok_video_is_known_by_its_code(link, code):
    assert research.code_of(link) == code
    if code:
        assert research.kind_of(code) == "TikTok video"
        assert research.code_of(research.reel_url(code)) == code


def tiktok_hit(path):
    return {"href": f"https://www.tiktok.com/{path}", "title": "Bryan Adams live", "body": ""}


TIKTOK_INFO = {
    "id": "7234567890123456789", "uploader": "BryanAdamsFan", "channel": "Bryan's Fan Page",
    "uploader_id": "6691488002098119685", "description": "Bryan Adams live at Wembley #live",
    "track": "Summer of '69", "artists": ["Bryan Adams"], "view_count": 2_400_000,
    "like_count": 310_000, "comment_count": 1200, "timestamp": 1761782400, "duration": 31,
    "formats": [{"width": 1080, "height": 1920, "tbr": 2400}],
    "thumbnail": "/api/thumbs/tt-7234567890123456789.jpg",
}  # fmt: skip


def test_a_research_finds_tiktok_videos_through_the_gateway(gateway):
    llm = Planner()
    asked, looked = [], []

    def find(query, count):
        asked.append(query)
        if query.startswith("site:tiktok.com"):
            return [
                tiktok_hit("@bryanadamsfan/video/7234567890123456789?lang=en"),
                tiktok_hit("@bryanadamsfan"),  # an account, not a video
                tiktok_hit("discover/bryan-adams-live"),  # a page of many
                tiktok_hit("@someone/video/7111111111111111111"),
            ]
        return [hit("ADAMS1")]

    def info(url):
        looked.append(url)
        if "7111111111111111111" in url:
            raise yt_dlp.utils.DownloadError("Video not available")  # nothing read: left out
        if "tiktok.com" in url:
            return TIKTOK_INFO
        return {"channel": "attic", "description": "Bryan Adams live", "like_count": 900}

    result = research.run(
        "Bryan Adams live", lambda: nullcontext(llm), set(), set(), lambda *a: None, find, info,
        0, sites=("instagram", "tiktok"),
    )  # fmt: skip
    assert any(q.startswith("site:tiktok.com video ") for q in asked)
    assert sorted(r.code for r in result.reels) == ["ADAMS1", "tt:7234567890123456789"]
    assert result.left_out.get("couldn't be looked up") == 1
    video = next(r for r in result.reels if r.on_tiktok)
    assert not video.on_instagram and not video.youtube and video.kind == "TikTok video"
    assert video.url == "https://www.tiktok.com/@bryanadamsfan/video/7234567890123456789"
    assert (video.account, video.handle, video.views, video.likes, video.comments) == (
        "Bryan's Fan Page", "bryanadamsfan", 2_400_000, 310_000, 1200,
    )  # fmt: skip
    assert video.caption.endswith("Sound: Summer of '69 - Bryan Adams")
    assert (video.width, video.height, video.duration) == (1080, 1920, 31.0)
    assert video.thumbnail == "/api/thumbs/tt-7234567890123456789.jpg"
    assert "TikTok video | account: Bryan's Fan Page" in llm.asked[-1]
    # Looked up by the link the search found.
    assert "https://www.tiktok.com/@bryanadamsfan/video/7234567890123456789" in looked


def test_without_the_gateway_a_research_doesnt_search_tiktok(no_gateway):
    llm, asked = Planner(), []

    def find(query, count):
        asked.append(query)
        return [hit("ADAMS1")]

    info = lambda url: {"channel": "attic", "description": "Bryan Adams live"}  # noqa: E731
    research.run("Bryan Adams live", lambda: nullcontext(llm), set(), set(), lambda *a: None,
                 find, info, 0, sites=("instagram", "tiktok"))  # fmt: skip
    assert asked and not any("tiktok" in q for q in asked)
    assert "tiktok" in research.SITES and "tiktok" not in research.DAILY_SITES


def test_once_the_gateway_fails_the_other_tiktok_videos_arent_asked():
    reels = [research.Reel(f"tt:72345678901234567{n:02d}", f"{VIDEO[:-2]}{n:02d}", "q")
             for n in range(8)]  # fmt: skip
    reels.append(research.Reel("ADAMS1", "https://www.instagram.com/reel/ADAMS1/", "q"))
    asked = []

    def info(url):
        asked.append(url)
        if "tiktok" in url:
            time.sleep(0.05)
            raise tiktok.GatewayError("the tunnel is down")
        return {"channel": "attic"}

    research.look_up(reels, info, workers=2)
    assert sum("tiktok" in u for u in asked) <= 2  # those already asking, no more
    assert reels[-1].looked_up and not any(r.looked_up for r in reels[:-1])


class Searches:
    def __init__(self):
        self.asked: list[tuple[str, str]] = []

    def engine(self, name, fail=False):
        def ask(query, count, page=1):
            self.asked.append((name, query))
            if fail:
                raise websearch.SearchError(f"{name} failed")
            return [{"href": href, "title": "bryan adams", "body": ""} for href in (
                "https://www.tiktok.com/@a/video/7234567890123456789",
                "https://www.instagram.com/reel/ADAMS1/",
            )]  # fmt: skip

        return name, ask


def test_only_tiktok_searches_are_asked_through_the_gateway(gateway, monkeypatch):
    searches, given = Searches(), []
    monkeypatch.setattr(websearch, "_engines", lambda: [searches.engine("here")])

    def through(via):
        given.append(via)
        return [searches.engine("gateway")]

    monkeypatch.setattr(websearch, "_gateway_engines", through)
    websearch.search("site:tiktok.com video bryan adams", 30)
    websearch.search("site:instagram.com/reel bryan adams", 30)
    websearch.search("bryan adams tiktok", 30)  # about TikTok, not on it
    assert [name for name, _ in searches.asked] == ["gateway", "here", "here"]
    assert given == [TUNNEL]


def test_a_gateway_down_doesnt_rest_the_engines_everything_else_uses(gateway, monkeypatch):
    searches = Searches()
    monkeypatch.setattr(websearch, "_engines", lambda: [searches.engine("yahoo")])
    monkeypatch.setattr(
        websearch, "_gateway_engines",
        lambda via: [searches.engine("yahoo through the TikTok gateway", fail=True)],
    )  # fmt: skip
    for n in range(websearch.FAILS_TO_REST + 1):
        with pytest.raises(websearch.SearchError):
            websearch.search(f"site:tiktok.com video query {n}", 30)
    assert websearch.search("bryan adams wembley", 30)  # yahoo itself never rested
    assert [name for name, _ in searches.asked].count("yahoo") == 1


def test_with_direct_tiktok_searches_are_asked_from_here(monkeypatch):
    monkeypatch.setattr(get_settings(), "tiktok_proxy", tiktok.DIRECT)
    assert websearch._via("site:tiktok.com video bryan adams") == ""
    monkeypatch.setattr(get_settings(), "tiktok_proxy", TUNNEL)
    assert websearch._via("site:tiktok.com video bryan adams") == TUNNEL
    assert websearch._via("site:instagram.com/reel tiktok dance") == ""


class Picture:
    def __init__(self, data=None, fail=False):
        self.data, self.fail, self.asked = data, fail, []

    def urlopen(self, url):
        self.asked.append(url)
        if self.fail:
            raise OSError("timed out")
        return nullcontext(io.BytesIO(self.data))


def jpeg(width=720, height=1280) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 90)).save(out, "JPEG")
    return out.getvalue()


def test_a_tiktok_videos_picture_is_kept_here_small(tmp_path):
    meta = {"id": "7234567890123456789", "thumbnail": "https://p16-sign-va.tiktokcdn.com/x.jpeg"}
    ydl = Picture(jpeg())
    assert tiktok.keep_picture(ydl, meta, tmp_path) == "/api/thumbs/tt-7234567890123456789.jpg"
    with Image.open(tmp_path / "tt-7234567890123456789.jpg") as kept:
        assert kept.size == (tiktok.PICTURE_WIDTH, 640)
    assert ydl.asked == [meta["thumbnail"]]
    again = Picture(fail=True)  # kept already: not fetched again
    assert tiktok.keep_picture(again, meta, tmp_path).startswith("/api/thumbs/")
    assert again.asked == []
    other = {**meta, "id": "7111111111111111111"}
    assert tiktok.keep_picture(Picture(fail=True), other, tmp_path) == meta["thumbnail"]
    assert tiktok.keep_picture(Picture(jpeg()), {"id": "../x", "thumbnail": "u"}, tmp_path) == "u"


def test_old_kept_pictures_are_cleaned_up(tmp_path):
    settings = Settings(media_dir=tmp_path / "media", cache_dir=tmp_path / "cache",
                        database=tmp_path / "app.db")  # fmt: skip
    thumbs = settings.cache_dir / "thumbs"
    thumbs.mkdir(parents=True)
    old, new = thumbs / "tt-1111111111.jpg", thumbs / "tt-2222222222.jpg"
    old.write_bytes(b"a")
    new.write_bytes(b"b")
    week_ago = (datetime.now(UTC) - timedelta(days=7)).timestamp()
    os.utime(old, (week_ago, week_ago))
    gone = cleanup.sweep(make_sessionmaker(settings.database), settings)
    assert gone["pictures"] == 1 and not old.exists() and new.exists()


def test_a_tiktok_account_feed_is_listed_through_the_gateway(gateway, monkeypatch):
    listing = {"_type": "playlist", "extractor_key": "TikTokUser", "entries": [
        {"url": VIDEO, "id": "7234567890123456789", "ie_key": "TikTok", "title": "one"},
    ]}  # fmt: skip
    site = FakeSite(info=listing)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", site.YoutubeDL)
    entries = autopilot.link_entries("https://www.tiktok.com/@khaby.lame")
    assert [(e.key, e.source) for e in entries] == [("TikTok-7234567890123456789", VIDEO)]
    assert site.made[0]["proxy"] == TUNNEL
    assert site.asked == ["https://www.tiktok.com/@khaby.lame"]  # not a channel's /videos


SETUP = ROOT / "scripts" / "setup-tiktok-gateway.sh"
TUNNEL_SCRIPT = ROOT / "scripts" / "macos-tiktok-gateway.sh"


def test_the_gateway_scripts_are_valid_bash():
    for script in (SETUP, TUNNEL_SCRIPT):
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_the_gateway_lets_the_live_macs_key_in_for_the_tunnel_only():
    key = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBCPTl6wuV1MNUZtiuwj7klCiowKuOnRQLmDSl live"
    shown = subprocess.run(
        ["bash", "-c", f'source "{SETUP}"; key_entry "$1" 100.94.146.32; valid_key "x y" || '
         'echo refused', "_", key],
        capture_output=True, text=True, check=True,
    ).stdout.splitlines()  # fmt: skip
    assert shown == [
        'restrict,port-forwarding,permitopen="*:80",permitopen="*:443",command="/usr/bin/false",'
        'from="100.94.146.32" ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIBCPTl6wuV1MNUZtiuwj7klCiowKu'
        "OnRQLmDSl aiedit-tiktok-gateway",
        "refused",
    ]


def test_the_live_macs_tunnel_is_an_ssh_socks_proxy_kept_up_by_launchd(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path), "GATEWAY_PORT": "1081"}
    out = subprocess.run(["bash", str(TUNNEL_SCRIPT), "plist", "me@100.1.2.3"],
                         capture_output=True, check=True, env=env).stdout  # fmt: skip
    agent = plistlib.loads(out)
    args = agent["ProgramArguments"]
    assert args[:4] == ["/usr/bin/ssh", "-N", "-D", "127.0.0.1:1081"] and args[-1] == "me@100.1.2.3"
    assert f"{tmp_path}/.ssh/aiedit_tiktok_gateway" in args and "BatchMode=yes" in args
    assert agent["KeepAlive"] and agent["RunAtLoad"]
