import re
import threading
import time
from pathlib import Path
from typing import ClassVar

import pytest
import yt_dlp

from agentic_editor import download


class FakeSite:
    """Stands in for yt-dlp: one video, which takes a moment to download."""

    def __init__(self):
        self.downloads = 0
        self.at_once = 0
        self.most_at_once = 0
        self.lock = threading.Lock()
        site = self

        class YoutubeDL:
            def __init__(self, options):
                self.options = options

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                pass

            def extract_info(self, url, download=False):
                if download:
                    with site.lock:
                        site.downloads += 1
                        site.at_once += 1
                        site.most_at_once = max(site.most_at_once, site.at_once)
                    time.sleep(0.2)
                    with open(self.options["outtmpl"].replace("%(ext)s", "mkv"), "w") as f:
                        f.write("video")
                    with site.lock:
                        site.at_once -= 1
                return {"extractor_key": "Youtube", "id": "abc", "title": "A song"}

        self.YoutubeDL = YoutubeDL


@pytest.fixture
def site(monkeypatch):
    fake = FakeSite()
    monkeypatch.setattr(yt_dlp, "YoutubeDL", fake.YoutubeDL)
    return fake


def test_two_jobs_for_one_link_download_it_once(tmp_path, site):
    found = []
    threads = [
        threading.Thread(target=lambda: found.append(download.fetch("https://x.test/v", tmp_path)))
        for _ in range(2)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert site.downloads == 1 and site.most_at_once == 1
    assert [s.path for s in found] == [tmp_path / "Youtube-abc" / "source.mkv"] * 2


def test_a_link_downloaded_before_needs_no_site(tmp_path, site, monkeypatch):
    first = download.fetch("https://x.test/v", tmp_path)

    def offline(options):
        raise OSError("no network")

    monkeypatch.setattr(yt_dlp, "YoutubeDL", offline)
    assert download.fetch("https://x.test/v", tmp_path) == first


def test_yt_dlp_signs_in_with_a_copy_of_the_cookies_file(tmp_path, monkeypatch):
    from agentic_editor.settings import get_settings

    cookies = tmp_path / "youtube-cookies.txt"
    cookies.write_text("# Netscape HTTP Cookie File\n")
    monkeypatch.setattr(get_settings(), "cookies_file", cookies)
    with download.signed_in({"quiet": True}) as options:
        copy = Path(options["cookiefile"])
        assert copy != cookies and copy.read_text() == cookies.read_text()
        assert options["quiet"]
    assert not copy.exists()  # yt-dlp wrote to its own copy, gone once done

    monkeypatch.setattr(get_settings(), "cookies_file", tmp_path / "gone.txt")
    with download.signed_in({"quiet": True}) as options:
        assert options == {"quiet": True}  # a missing file: as without one
    monkeypatch.setattr(get_settings(), "cookies_file", None)
    with download.signed_in({"quiet": True}) as options:
        assert options == {"quiet": True}


def test_youtube_asking_to_sign_in_says_what_to_do(tmp_path, monkeypatch):
    class Blocked:
        def __init__(self, options):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            pass

        def extract_info(self, url, download=False):
            raise yt_dlp.utils.DownloadError(
                "ERROR: [youtube] qzAm4LKWkBU: Sign in to confirm you’re not a bot. Use "
                "--cookies-from-browser or --cookies for the authentication."
            )

    monkeypatch.setattr(yt_dlp, "YoutubeDL", Blocked)
    with pytest.raises(RuntimeError, match="an admin adds a YouTube cookies file"):
        download.fetch("https://www.youtube.com/shorts/qzAm4LKWkBU", tmp_path)


SENSITIVE = (
    "ERROR: [Instagram] Df2AEd5RJ3U: This content isn't available to everyone: It can't be "
    "seen by certain audiences."
)
COOKIES = (
    "# Netscape HTTP Cookie File\n"
    ".instagram.com\tTRUE\t/\tTRUE\t1795000000\tsessionid\t123%3Aabc\n"
    ".instagram.com\tTRUE\t/\tTRUE\t1795000000\tcsrftoken\txyz\n"
    ".google.com\tTRUE\t/\tTRUE\t1795000000\tSID\tnot-instagram\n"
)


class Restricted:
    """Instagram through yt-dlp: a reel shown only to people signed in; signed in, it comes."""

    asked: ClassVar[list[dict]] = []

    def __init__(self, options):
        self.options = options
        Restricted.asked.append(options)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def extract_info(self, url, download=False):
        cookies = self.options.get("cookiefile")
        if not cookies or not re.search(r"sessionid\t\S", Path(cookies).read_text()):
            raise yt_dlp.utils.DownloadError(SENSITIVE)
        if download:
            Path(self.options["outtmpl"].replace("%(ext)s", "mp4")).write_text("video")
        return {"extractor_key": "Instagram", "id": "Df2AEd5RJ3U", "title": "Video by kick"}


@pytest.fixture
def instagram(tmp_path, monkeypatch):
    from agentic_editor.settings import get_settings

    Restricted.asked = []
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Restricted)
    monkeypatch.setattr(get_settings(), "instagram_cookies_file", tmp_path / "ig-cookies.txt")
    return tmp_path / "ig-cookies.txt"


def test_a_reel_instagram_shows_only_signed_in_says_what_to_do(tmp_path, instagram):
    url = "https://www.instagram.com/reel/Df2AEd5RJ3U/?cnvx=abc"
    with pytest.raises(RuntimeError, match="no Instagram login is set up for downloads yet"):
        download.fetch(url, tmp_path / "sources")


def test_with_an_instagram_login_such_a_reel_downloads_signed_in(tmp_path, instagram):
    state = download.save_instagram_cookies(COOKIES)
    assert state["set"] and state["until"].year == 2026
    kept = instagram.read_text()
    assert "sessionid" in kept and "google" not in kept  # only Instagram's are kept
    assert instagram.stat().st_mode & 0o777 == 0o600
    url = "https://www.instagram.com/reel/Df2AEd5RJ3U/"
    source = download.fetch(url, tmp_path / "sources")
    assert source.path.read_text() == "video"
    # Asked without the login first; signed in after, and straight away for the download.
    assert [("cookiefile" in o) for o in Restricted.asked] == [False, True, True]


def test_an_expired_instagram_login_says_so(tmp_path, instagram):
    download.save_instagram_cookies(COOKIES)
    instagram.write_text(COOKIES.replace("sessionid\t123%3Aabc", "sessionid\t"))
    with pytest.raises(RuntimeError, match="login has most likely expired"):
        download.fetch("https://www.instagram.com/reel/Df2AEd5RJ3U/", tmp_path / "sources")


def test_other_sites_never_get_the_instagram_login(tmp_path, instagram):
    download.save_instagram_cookies(COOKIES)
    with pytest.raises(yt_dlp.utils.DownloadError):
        download.fetch("https://kick.com/x/clips/clip_1", tmp_path / "sources")
    assert not any("cookiefile" in o for o in Restricted.asked)


def test_instagram_cookies_are_checked_before_they_are_kept(instagram):
    with pytest.raises(ValueError, match="no Instagram sign-in"):
        download.save_instagram_cookies(".google.com\tTRUE\t/\tTRUE\t0\tSID\tx\n" * 2)
    assert not instagram.exists()
    exported = (
        '[{"domain": ".instagram.com", "name": "sessionid", "value": "1%3Aq", "path": "/", '
        '"secure": true, "expirationDate": 1795000000.5}]'
    )
    assert download.save_instagram_cookies(exported)["set"]  # a JSON export works too
    assert download.forget_instagram_cookies() == {"set": False, "since": None, "until": None}


def test_a_reel_refused_to_the_login_too_says_instagrams_reason(tmp_path, instagram, monkeypatch):
    """Signed in, Instagram answers a reel it won't show that account with a 400 (all yt-dlp
    says); its own reason is asked for and passed on, with what to change."""

    class Gated(Restricted):
        def extract_info(self, url, download=False):
            if self.options.get("cookiefile"):
                raise yt_dlp.utils.DownloadError(
                    "ERROR: [Instagram] DdeOTl4M7tx: Video info extraction failed: HTTP Error "
                    "400: Bad Request (caused by <HTTPError 400: Bad Request>)"
                )
            return super().extract_info(url, download)

    download.save_instagram_cookies(COOKIES)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Gated)
    asked = []
    monkeypatch.setattr(
        download, "instagram_gate",
        lambda url, cookies: asked.append(url) or "People under 25 can't see this content",
    )  # fmt: skip
    with pytest.raises(RuntimeError) as failed:
        download.fetch("https://www.instagram.com/reel/DdeOTl4M7tx/", tmp_path / "sources")
    assert '"People under 25 can\'t see this content"' in str(failed.value)
    assert "Sensitive content to More" in str(failed.value) and asked


def test_instagrams_reason_is_read_from_its_api(tmp_path, monkeypatch):
    import httpx

    cookies = tmp_path / "c.txt"
    cookies.write_text(COOKIES)
    seen = {}

    class Reply:
        def json(self):
            return {"message": "geoblock_required", "gating_type": "unappealable",
                    "title": "This content may be inappropriate",
                    "description": "It's unavailable for certain audiences."}  # fmt: skip

    def get(url, cookies=None, headers=None, timeout=None):
        seen.update(url=url, cookies=cookies, headers=headers)
        return Reply()

    monkeypatch.setattr(httpx, "get", get)
    said = download.instagram_gate("https://www.instagram.com/reel/Df2AEd5RJ3U/?x=1", cookies)
    assert said == "This content may be inappropriate It's unavailable for certain audiences."
    assert seen["url"].endswith("/media/4032410823334075860/info/")  # the reel's own number
    assert seen["cookies"]["sessionid"] == "123%3Aabc" and "SID" not in seen["cookies"]
    assert download.instagram_gate("https://kick.com/x", cookies) is None


def test_a_busy_instagram_is_asked_once_more_after_a_pause(tmp_path, instagram, monkeypatch):
    """A retried job failed on "HTTP Error 560" and downloaded a minute later."""
    tries = []

    class Busy(Restricted):
        def extract_info(self, url, download=False):
            if self.options.get("cookiefile"):
                tries.append(download)
                if len(tries) == 1:
                    raise yt_dlp.utils.DownloadError(
                        "ERROR: [Instagram] DdeOTl4M7tx: Video info extraction failed: HTTP Error "
                        "560: - (caused by <HTTPError 560: ->)"
                    )
            return super().extract_info(url, download)

    download.save_instagram_cookies(COOKIES)
    monkeypatch.setattr(yt_dlp, "YoutubeDL", Busy)
    monkeypatch.setattr(download, "INSTAGRAM_PAUSE", 0)
    source = download.fetch("https://www.instagram.com/reel/DdeOTl4M7tx/", tmp_path / "sources")
    assert source.path.read_text() == "video" and tries == [False, False, True]
