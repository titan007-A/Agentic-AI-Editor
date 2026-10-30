import hashlib
import json
import logging
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from agentic_editor import slots
from agentic_editor.settings import ROOT, get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    path: Path
    title: str
    uploader: str = ""
    url: str = ""
    description: str = ""


@contextmanager
def signed_in(options: dict) -> Iterator[dict]:
    """yt-dlp's `options` with the settings' cookies file (see Settings.cookies_file), when
    there is one. Each gets a copy: yt-dlp writes its cookies back when done, and lookups
    run at once would write one file together."""
    cookies = get_settings().cookies_file
    if cookies is None:
        yield options
        return
    path = cookies.expanduser() if cookies.expanduser().is_absolute() else ROOT / cookies
    if not path.is_file():
        log.warning("the cookies file %s isn't there; YouTube may ask to sign in", path)
        yield options
        return
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "cookies.txt"
        shutil.copyfile(path, copy)
        yield {**options, "cookiefile": str(copy)}


# What YouTube says when it wants this machine signed in before it gives a video.
BOT_CHECK = "confirm you’re not a bot"
SIGN_IN = (
    "YouTube is asking this computer to sign in before it gives videos (\"confirm you're "
    'not a bot"), so no YouTube video can be downloaded until an admin adds a YouTube '
    'cookies file: see "YouTube asks to sign in" in the README'
)


@contextmanager
def _explained() -> Iterator[None]:
    """yt-dlp's errors, with YouTube's request to sign in said in words an editor can act
    on (see SIGN_IN)."""
    try:
        yield
    except Exception as exc:
        if BOT_CHECK in str(exc).replace("'", "’"):
            raise RuntimeError(SIGN_IN) from exc
        raise


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "-", text).strip("-")[:80] or "video"


def _ytdlp_options(dest: Path) -> dict:
    settings = get_settings()
    options = {
        "format": "bv*+ba/b",
        "merge_output_format": "mkv",
        "outtmpl": str(dest / "source.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
    }
    if settings.ffmpeg_bin != "ffmpeg":
        options["ffmpeg_location"] = str(Path(settings.ffmpeg_bin).parent)
    return options


def _saved(dest: Path) -> Source | None:
    meta_file = dest / "source.json"
    if not meta_file.exists():
        return None
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    meta["path"] = Path(meta["path"])
    return Source(**meta) if meta["path"].exists() else None


def _link_file(url: str, sources_dir: Path) -> Path:
    """Where the folder a link was downloaded to is noted."""
    return sources_dir / "links" / f"{hashlib.sha256(url.encode()).hexdigest()[:20]}.txt"


def fetch(url: str, sources_dir: Path) -> Source:
    """Download a video at the best available quality. A video that was already
    downloaded is not fetched again, and a link used before isn't looked up on the site
    again, so it still works once the site is unreachable or the video is gone."""
    link = _link_file(url, sources_dir)
    if link.exists():
        source = _saved(sources_dir / link.read_text(encoding="utf-8").strip())
        if source is not None:
            log.info("already downloaded: %s", source.title)
            return source

    from yt_dlp import YoutubeDL

    options = {"quiet": True, "no_warnings": True, "noplaylist": True}
    with _explained(), signed_in(options) as options, YoutubeDL(options) as ydl:
        info = ydl.extract_info(url, download=False)
    key = _slug(f"{info.get('extractor_key', 'web')}-{info.get('id') or url}")
    dest = sources_dir / key
    dest.mkdir(parents=True, exist_ok=True)
    # Two jobs given the same video would download it into the same files at once.
    with slots.video(dest).turn("download"):
        source = _saved(dest)
        if source is not None:
            log.info("already downloaded: %s", source.title)
        else:
            options = _ytdlp_options(dest)
            with _explained(), signed_in(options) as options, YoutubeDL(options) as ydl:
                info = ydl.extract_info(url, download=True)
            path = next(p for p in dest.iterdir() if p.stem == "source" and p.suffix != ".json")
            source = Source(
                path=path,
                title=info.get("title") or key,
                uploader=info.get("uploader") or "",
                url=url,
                description=(info.get("description") or "")[:1000],
            )
            meta = json.dumps({**asdict(source), "path": str(path)})
            (dest / "source.json").write_text(meta, encoding="utf-8")
    link.parent.mkdir(parents=True, exist_ok=True)
    partial = link.with_suffix(f".{threading.get_ident()}.part")
    partial.write_text(key, encoding="utf-8")
    partial.replace(link)
    return source


def local(path: Path) -> Source:
    """A file on this machine, titled by its name; an upload by the name it was sent with,
    kept beside it, since the stored one is made safe for a path."""
    if not path.exists():
        raise FileNotFoundError(path)
    sent = path.parent / ".name"
    title = Path(sent.read_text(encoding="utf-8")).stem if sent.is_file() else path.stem
    return Source(path=path.resolve(), title=title or path.stem)


def update_ytdlp() -> str:
    """Upgrade yt-dlp inside the running environment and return the installed version.

    Sites change often enough that the locked version goes stale between releases."""
    uv = shutil.which("uv")
    if uv:
        cmd = [uv, "pip", "install", "--python", sys.executable, "--upgrade", "yt-dlp"]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--upgrade", "yt-dlp"]
    subprocess.run(cmd, check=True, capture_output=True)
    result = subprocess.run(
        [sys.executable, "-c", "import yt_dlp.version; print(yt_dlp.version.__version__)"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()
