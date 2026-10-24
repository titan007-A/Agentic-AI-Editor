import threading
import time

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
