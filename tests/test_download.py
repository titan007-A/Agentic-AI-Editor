import threading
import time
from pathlib import Path

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
