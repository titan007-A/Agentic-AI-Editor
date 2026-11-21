import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
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
def _with_cookies(options: dict, path: Path) -> Iterator[dict]:
    """`options` with a copy of the cookies file at `path`: yt-dlp writes its cookies back
    when done, and lookups run at once would write one file together."""
    with tempfile.TemporaryDirectory() as folder:
        copy = Path(folder) / "cookies.txt"
        shutil.copyfile(path, copy)
        yield {**options, "cookiefile": str(copy)}


@contextmanager
def signed_in(options: dict) -> Iterator[dict]:
    """yt-dlp's `options` with the settings' cookies file (see Settings.cookies_file), when
    there is one, as a copy (see _with_cookies)."""
    cookies = get_settings().cookies_file
    if cookies is None:
        yield options
        return
    path = cookies.expanduser() if cookies.expanduser().is_absolute() else ROOT / cookies
    if not path.is_file():
        log.warning("the cookies file %s isn't there; YouTube may ask to sign in", path)
        yield options
        return
    with _with_cookies(options, path) as signed:
        yield signed


# A post's description is kept whole up to the most Instagram allows in a caption, so a
# preset that posts it again (PostCaption.about "original") has all of it.
CAPTION_LIMIT = 2200

# --- Instagram reels shown only to people signed in ---------------------------------------

# What Instagram (through yt-dlp) says of a reel it shows only to people signed in: one
# marked sensitive or not for everyone ("can't be seen by certain audiences"), or a check
# that wants a login.
LOGIN_WALL = re.compile(
    r"isn't available to everyone|can't be seen by certain audiences|empty media response|"
    r"login required|log in to|rate-limit reached|requested content is not available|"
    r"--cookies",
    re.I,
)
INSTAGRAM_LOGIN = (
    "Instagram shows this reel only to people signed in (it is marked sensitive, or not for "
    "everyone), and no Instagram login is set up for downloads yet. The primary account can "
    "add one on the System page; then tap Retry."
)
INSTAGRAM_EXPIRED = (
    "Instagram turned this reel away even with the Instagram login on the System page: the "
    "login has most likely expired or been signed out. Add fresh cookies there, then tap "
    "Retry."
)
INSTAGRAM_GATED = (
    "Instagram won't show this reel even to the Instagram login on the System page: it says "
    '"{said}". That is the account\'s settings: in it, set Sensitive content to More and '
    "make sure its birthday makes it 25 or older, then tap Retry. If Instagram still says so, "
    "it doesn't show this reel in this country."
)
INSTAGRAM_COOKIES = "instagram-cookies.txt"
# Instagram now and then answers a signed-in request with a server error ("HTTP Error 560")
# that is gone a little later: asked once more after this pause (s).
INSTAGRAM_BUSY = re.compile(r"HTTP Error 5\d\d")
INSTAGRAM_PAUSE = 20.0
# The code in a reel's or post's link: instagram.com/reel/<code>/, /p/<code>/, /tv/<code>/
SHORTCODE = re.compile(r"instagram\.com/(?:[\w.]+/)?(?:reels?|p|tv)/([\w-]+)")
SHORTCODE_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
INSTAGRAM_APP_ID = "936619743392459"  # the website's own, which its API asks for


def instagram_gate(url: str, cookies: Path) -> str | None:
    """What Instagram tells the signed-in account when it won't show it a reel ("This
    content may be inappropriate. It's unavailable for certain audiences..."), from the API
    yt-dlp asks (yt-dlp only says "HTTP Error 400"); None when it says nothing of the kind."""
    import http.cookiejar

    import httpx

    found = SHORTCODE.search(url)
    if not found:
        return None
    pk = 0
    for letter in found.group(1):
        if letter not in SHORTCODE_ALPHABET:
            return None
        pk = pk * 64 + SHORTCODE_ALPHABET.index(letter)
    jar = http.cookiejar.MozillaCookieJar(str(cookies))
    try:
        jar.load(ignore_discard=True, ignore_expires=True)
        signed = {c.name: c.value for c in jar if "instagram.com" in c.domain}
        reply = httpx.get(
            f"https://i.instagram.com/api/v1/media/{pk}/info/",
            cookies=signed, timeout=15,
            headers={"X-IG-App-ID": INSTAGRAM_APP_ID, "X-CSRFToken": signed.get("csrftoken", ""),
                     "User-Agent": BROWSER, "Accept": "*/*"},
        ).json()  # fmt: skip
    except (OSError, ValueError, httpx.HTTPError, http.cookiejar.LoadError):
        return None
    if not isinstance(reply, dict) or not reply.get("gating_type"):
        return None
    said = " ".join(str(reply.get(k) or "").strip() for k in ("title", "description"))
    return said.strip() or None


# A browser's name, for the one request made to Instagram's API without yt-dlp.
BROWSER = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


def instagram_cookies_path(settings=None) -> Path:
    """Where the Instagram cookies are: Settings.instagram_cookies_file, else the file the
    System page saves in the media folder. `settings`: the server's (get_settings())."""
    settings = settings or get_settings()
    chosen = settings.instagram_cookies_file
    if chosen is None:
        return settings.media_dir / INSTAGRAM_COOKIES
    return chosen.expanduser() if chosen.expanduser().is_absolute() else ROOT / chosen


def _instagram(url: str) -> bool:
    host = re.sub(r"^https?://", "", url.lower()).split("/", 1)[0]
    return host == "instagram.com" or host.endswith(".instagram.com")


def _netscape(text: str) -> list[list[str]]:
    """The cookies in a cookies.txt (Netscape format: domain, subdomains, path, secure,
    expiry, name, value, by tabs), or in the JSON list some exporters write instead."""
    text = text.strip()
    rows: list[list[str]] = []
    if text.startswith("["):
        try:
            items = json.loads(text)
        except ValueError as exc:
            raise ValueError("that isn't a cookies file (broken JSON)") from exc
        for c in items if isinstance(items, list) else []:
            if not isinstance(c, dict) or not c.get("name"):
                continue
            domain = str(c.get("domain") or "")
            rows.append([
                domain, "TRUE" if domain.startswith(".") else "FALSE", str(c.get("path") or "/"),
                "TRUE" if c.get("secure") else "FALSE",
                str(int(float(c.get("expirationDate") or c.get("expires") or 0))),
                str(c["name"]), str(c.get("value") or ""),
            ])  # fmt: skip
        return rows
    for line in text.splitlines():
        line = line.strip().removeprefix("#HttpOnly_")
        if not line or line.startswith("#"):
            continue
        fields = line.split("\t")
        if len(fields) == 7:
            rows.append(fields)
    return rows


def save_instagram_cookies(text: str, settings=None) -> dict:
    """Keep the cookies of a browser signed in to Instagram (pasted cookies.txt or JSON) for
    the reels that need a login: only Instagram's, readable by this user alone. Raises
    ValueError when they hold no Instagram sign-in."""
    rows = [r for r in _netscape(text) if "instagram.com" in r[0]]
    if not any(r[5] == "sessionid" and r[6] for r in rows):
        raise ValueError(
            "no Instagram sign-in in those cookies (no sessionid for instagram.com): export "
            "them from instagram.com while signed in"
        )
    lines = ["# Netscape HTTP Cookie File", *("\t".join(r) for r in rows)]
    path = instagram_cookies_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    partial.unlink(missing_ok=True)
    fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as out:
        out.write("\n".join(lines) + "\n")
    partial.replace(path)
    log.info("Instagram cookies saved for downloads (%d cookies)", len(rows))
    return instagram_login(settings)


def forget_instagram_cookies(settings=None) -> dict:
    instagram_cookies_path(settings).unlink(missing_ok=True)
    return instagram_login(settings)


def instagram_login(settings=None) -> dict:
    """Whether Instagram cookies are set, since when, and until when the sign-in lasts (the
    sessionid's expiry); never the cookies themselves."""
    path = instagram_cookies_path(settings)
    if not path.is_file():
        return {"set": False, "since": None, "until": None}
    since = datetime.fromtimestamp(path.stat().st_mtime, UTC)
    until = None
    try:
        for row in _netscape(path.read_text(encoding="utf-8")):
            if row[5] == "sessionid" and row[4].isdigit() and int(row[4]) > 0:
                until = datetime.fromtimestamp(int(row[4]), UTC)
    except OSError:
        pass
    return {"set": True, "since": since, "until": until}


def _extract(url: str, options: dict, download: bool, signed: bool = False) -> tuple[dict, bool]:
    """yt-dlp's info for `url` (downloading it when asked), and whether it took the
    Instagram sign-in. An Instagram reel shown only to people signed in is asked for again
    with the Instagram cookies, when there are any; `signed` goes to them at once."""
    from yt_dlp import YoutubeDL

    cookies = instagram_cookies_path() if _instagram(url) else None
    if not signed:
        try:
            with _explained(), signed_in(options) as asked, YoutubeDL(asked) as ydl:
                return ydl.extract_info(url, download=download), False
        except Exception as exc:
            if cookies is None or not LOGIN_WALL.search(str(exc)):
                raise
            if not cookies.is_file():
                raise RuntimeError(INSTAGRAM_LOGIN) from exc
            log.info("Instagram shows %s only to people signed in: asking signed in", url)
    try:
        try:
            with _with_cookies(options, cookies) as asked, YoutubeDL(asked) as ydl:
                return ydl.extract_info(url, download=download), True
        except Exception as exc:
            if not INSTAGRAM_BUSY.search(str(exc)):
                raise
            log.info("Instagram was busy with %s (%s): asking again shortly", url, exc)
            time.sleep(INSTAGRAM_PAUSE)
            with _with_cookies(options, cookies) as asked, YoutubeDL(asked) as ydl:
                return ydl.extract_info(url, download=download), True
    except Exception as exc:
        # Signed in, a reel Instagram still won't show comes back as "HTTP Error 400";
        # Instagram's own words say why (the account's settings or age).
        if said := instagram_gate(url, cookies):
            raise RuntimeError(INSTAGRAM_GATED.format(said=said)) from exc
        if LOGIN_WALL.search(str(exc)):
            raise RuntimeError(INSTAGRAM_EXPIRED) from exc
        raise


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


# The best version up to 4K (3840 on the long side, either way up): official music videos
# come in 8K, many times the size and the decoding, for reels made 1080 wide. A video only
# offered larger still comes at its best; one whose size the site doesn't say passes.
FORMAT = "bv*[width<=?3840][height<=?3840]+ba/b[width<=?3840][height<=?3840]/bv*+ba/b"


def _ytdlp_options(dest: Path) -> dict:
    settings = get_settings()
    options = {
        "format": FORMAT,
        "merge_output_format": "mkv",
        "outtmpl": str(dest / "source.%(ext)s"),
        "noplaylist": True,
        # A post with a few videos (X) is a playlist: its first video is the source.
        "playlist_items": "1",
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

    options = {"quiet": True, "no_warnings": True, "noplaylist": True}
    info, signed = _extract(url, options, download=False)
    key = _slug(f"{info.get('extractor_key', 'web')}-{info.get('id') or url}")
    dest = sources_dir / key
    dest.mkdir(parents=True, exist_ok=True)
    # Two jobs given the same video would download it into the same files at once.
    with slots.video(dest).turn("download"):
        source = _saved(dest)
        if source is not None:
            log.info("already downloaded: %s", source.title)
        else:
            info, _ = _extract(url, _ytdlp_options(dest), download=True, signed=signed)
            path = next(p for p in dest.iterdir() if p.stem == "source" and p.suffix != ".json")
            source = Source(
                path=path,
                title=info.get("title") or key,
                uploader=info.get("uploader") or "",
                url=url,
                description=(info.get("description") or "")[:CAPTION_LIMIT],
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
