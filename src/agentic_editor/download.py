import json
import logging
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from agentic_editor.settings import get_settings

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Source:
    path: Path
    title: str
    uploader: str = ""
    url: str = ""
    description: str = ""


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
    }
    if settings.ffmpeg_bin != "ffmpeg":
        options["ffmpeg_location"] = str(Path(settings.ffmpeg_bin).parent)
    return options


def fetch(url: str, sources_dir: Path) -> Source:
    """Download a video at the best available quality. A URL that was already
    downloaded is not fetched again."""
    from yt_dlp import YoutubeDL

    with YoutubeDL({"quiet": True, "no_warnings": True, "noplaylist": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    key = _slug(f"{info.get('extractor_key', 'web')}-{info.get('id') or url}")
    dest = sources_dir / key
    meta_file = dest / "source.json"
    if meta_file.exists():
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        meta["path"] = Path(meta["path"])
        if meta["path"].exists():
            log.info("already downloaded: %s", meta["title"])
            return Source(**meta)

    dest.mkdir(parents=True, exist_ok=True)
    with YoutubeDL(_ytdlp_options(dest)) as ydl:
        info = ydl.extract_info(url, download=True)
    path = next(p for p in dest.iterdir() if p.stem == "source" and p.suffix != ".json")
    source = Source(
        path=path,
        title=info.get("title") or key,
        uploader=info.get("uploader") or "",
        url=url,
        description=(info.get("description") or "")[:1000],
    )
    meta_file.write_text(json.dumps({**asdict(source), "path": str(path)}), encoding="utf-8")
    return source


def local(path: Path) -> Source:
    if not path.exists():
        raise FileNotFoundError(path)
    return Source(path=path.resolve(), title=path.stem)


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
