"""TikTok, through the TikTok gateway.

TikTok is blocked from India: the networks answer its names with their own "restricted"
address, on the live Mac's as on the PC's. A second Mac on a VPN is the gateway. Nothing of
the editor runs there: the live Mac keeps an SSH tunnel to it over Tailscale
(scripts/macos-tiktok-gateway.sh), a SOCKS proxy on this machine that Settings.tiktok_proxy
names (socks5h://127.0.0.1:1080), and TikTok's links, and nothing else, are fetched through
it. socks5h, not socks5: the gateway looks TikTok's names up too, past India's answers.
Setting the gateway Mac up: scripts/setup-tiktok-gateway.sh, docs/tiktok-gateway.md.

With no gateway a TikTok link is refused at once, saying why, rather than left to time
out; through one, a failure says whether the tunnel or the VPN behind it is down."""

import logging
import re
import socket
import time
import urllib.parse
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from agentic_editor.settings import get_settings

log = logging.getLogger(__name__)

DIRECT = "direct"  # Settings.tiktok_proxy for a server TikTok isn't blocked from
# TikTok's names a link may have: www, m, and vm/vt (short links), and tiktokv.com (the
# app's share links).
HOST = re.compile(r"(?:[\w-]+\.)*tiktokv?\.com")
# Asked to see whether TikTok answers: any answer counts, an error page too.
CHECK = "https://www.tiktok.com/robots.txt"
TIMEOUT = 15.0  # s, for the check through the gateway
# A failure on the way to TikTok rather than TikTok's own answer (a 404, a private video).
NETWORK = re.compile(
    r"connection (?:refused|reset|aborted)|timed out|EOF occurred|remote end closed|socks|"
    r"proxy|network is unreachable|no route to host|name or service not known|"
    r"nodename nor servname|temporary failure in name resolution|handshake",
    re.IGNORECASE,
)
# Where a TikTok video's picture is kept for the Researcher's cards (see keep_picture): a
# browser in India can't load TikTok's own copy either.
PICTURE = re.compile(r"tt-\d{5,25}\.jpg")
PICTURE_WIDTH = 360

REFUSED = (
    "TikTok is blocked from here, so TikTok needs the TikTok gateway (a second Mac on a VPN), "
    "and it isn't set up on this server yet. The owner sets it up once "
    "(docs/tiktok-gateway.md in the editor's code)."
)
NO_GATEWAY = (
    "TikTok is blocked from here: TikTok links download only through the TikTok gateway (a "
    "second Mac on a VPN), and it isn't set up on this server yet. The owner sets it up once "
    "(docs/tiktok-gateway.md in the editor's code); then tap Retry."
)
TUNNEL_DOWN = (
    "TikTok links download through the TikTok gateway, and its tunnel is down just now "
    "({said}): the gateway Mac may be off, asleep or off Tailscale. Once the System page says "
    "the tunnel is up, tap Retry."
)
UNREACHABLE = (
    "TikTok didn't answer through the TikTok gateway ({said}). The tunnel to the gateway Mac "
    "is up, so its VPN is most likely off (or TikTok is down). Turn the VPN on there, check "
    "the System page, then tap Retry."
)


class GatewayError(RuntimeError):
    pass


def is_link(url: str) -> bool:
    """Whether `url` is TikTok's (www.tiktok.com/@name/video/..., vm.tiktok.com/...)."""
    host = urllib.parse.urlsplit(url.strip()).hostname or ""
    return HOST.fullmatch(host.lower()) is not None


def proxy(settings=None) -> str:
    """The gateway TikTok goes through, DIRECT, or "" when there is none."""
    return (settings or get_settings()).tiktok_proxy


def available(settings=None) -> bool:
    """Whether TikTok can be reached: through a gateway, or directly."""
    return bool(proxy(settings))


def link(url: str) -> str:
    """A TikTok link as yt-dlp knows it best: its video pages are www.tiktok.com's (one
    without "www." or on m.tiktok.com is otherwise read as some web page)."""
    url = url.strip()
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    found = re.fullmatch(r"/v/(\d+)(?:\.html)?/?", parts.path)
    if found and host in ("m.tiktok.com", "tiktok.com", "www.tiktok.com"):
        return f"https://www.tiktok.com/@_/video/{found.group(1)}"
    if host in ("tiktok.com", "m.tiktok.com"):
        return urllib.parse.urlunsplit(parts._replace(scheme="https", netloc="www.tiktok.com"))
    return url


def routed(url: str, options: dict, settings=None) -> dict:
    """yt-dlp's `options` for `url`: a TikTok link's go through the gateway; any other
    link's are left as they are. A TikTok link with no gateway raises at once."""
    if not is_link(url):
        return options
    through = proxy(settings)
    if not through:
        raise GatewayError(NO_GATEWAY)
    return options if through == DIRECT else {**options, "proxy": through}


# What a failure on the way says, in yt-dlp's long words ("Unable to download webpage:
# (<SocksHTTPSConnection(...) at 0x...>, 'Connection to www.tiktok.com timed out. ...').
PLAIN = re.compile(
    r"connection to [\w.-]+ timed out|connection (?:refused|reset by peer|aborted)|"
    r"EOF occurred in violation of protocol|remote end closed connection without response|"
    r"network is unreachable|no route to host|name or service not known|"
    r"nodename nor servname provided, or not known|temporary failure in name resolution|"
    r"timed out",
    re.IGNORECASE,
)


def _said(exc: Exception) -> str:
    text = " ".join(str(exc).split()).removeprefix("ERROR: ")
    if found := PLAIN.search(text):
        return found.group(0)
    return text[:160] or type(exc).__name__


@contextmanager
def explained(url: str, settings=None) -> Iterator[None]:
    """yt-dlp's failures on the way to TikTok, said as what to do: the tunnel is down, or
    the VPN behind it. TikTok's own answers (gone, private) are left as they are."""
    try:
        yield
    except GatewayError:
        raise
    except Exception as exc:
        through = proxy(settings)
        if not is_link(url) or through in ("", DIRECT) or not NETWORK.search(str(exc)):
            raise
        if down := tunnel(through):
            raise GatewayError(TUNNEL_DOWN.format(said=down)) from exc
        raise GatewayError(UNREACHABLE.format(said=_said(exc))) from exc


def tunnel(through: str, timeout: float = 3.0) -> str | None:
    """None when the proxy `through` answers (a SOCKS5 one as SOCKS5 does), else why not."""
    parts = urllib.parse.urlsplit(through)
    where = f"{parts.hostname}:{parts.port}"
    try:
        with socket.create_connection((parts.hostname, parts.port), timeout) as conn:
            if parts.scheme.startswith("socks5"):
                conn.settimeout(timeout)
                conn.sendall(b"\x05\x01\x00")  # version 5, one way to sign in: none
                if conn.recv(2) != b"\x05\x00":
                    return f"something answers at {where}, but not as the tunnel does"
    except OSError as exc:
        return f"nothing answers at {where}: {exc.strerror or exc}"
    return None


def reach(through: str, url: str = CHECK, timeout: float = TIMEOUT) -> str | None:
    """None when TikTok answers `url` through `through` (DIRECT: without a proxy), as the
    downloads ask it (yt-dlp), with any status; else what went wrong."""
    from yt_dlp import YoutubeDL
    from yt_dlp.networking.exceptions import HTTPError

    options = {"quiet": True, "no_warnings": True, "socket_timeout": timeout}
    if through != DIRECT:
        options["proxy"] = through
    try:
        with YoutubeDL(options) as ydl, ydl.urlopen(url) as reply:
            reply.read(256)
    except HTTPError:
        return None  # TikTok's own error page: it answered
    except Exception as exc:
        return _said(exc)
    return None


def status(settings=None, probe=tunnel, check=reach) -> dict:
    """The gateway as the System page shows it: whether one is set, whether its tunnel is
    up, and whether TikTok answers through it (and how fast)."""
    through = proxy(settings)
    found = {"set": bool(through), "proxy": through, "direct": through == DIRECT,
             "tunnel": None, "tunnel_said": "", "tiktok": None, "tiktok_said": "",
             "seconds": None}  # fmt: skip
    if not through:
        return found
    if through != DIRECT:
        down = probe(through)
        found["tunnel"], found["tunnel_said"] = down is None, down or ""
        if down:
            found["tiktok"], found["tiktok_said"] = False, "not asked: the tunnel is down"
            return found
    began = time.monotonic()
    said = check(through)
    found["tiktok"], found["tiktok_said"] = said is None, said or ""
    found["seconds"] = round(time.monotonic() - began, 2)
    return found


def keep_picture(ydl, meta: dict, folder: Path) -> str:
    """A TikTok video's picture (`meta`'s, from yt-dlp `ydl`, which goes through the
    gateway), kept small in `folder` and given as the editor's own address
    (/api/thumbs/<name>); TikTok's own address when it can't be fetched."""
    from io import BytesIO

    from PIL import Image

    address, name = meta.get("thumbnail") or "", f"tt-{meta.get('id')}.jpg"
    if not address or not PICTURE.fullmatch(name):
        return address
    path = folder / name
    if not path.is_file():
        try:
            with ydl.urlopen(address) as reply:
                data = reply.read(5_000_000)
            with Image.open(BytesIO(data)) as picture:
                picture = picture.convert("RGB")
                picture.thumbnail((PICTURE_WIDTH, PICTURE_WIDTH * 2))
                folder.mkdir(parents=True, exist_ok=True)
                partial = path.with_suffix(".part")
                picture.save(partial, "JPEG", quality=82)
                partial.replace(path)
        except Exception as exc:
            log.info("couldn't keep the picture of TikTok video %s: %s", meta.get("id"), exc)
            return address
    return f"/api/thumbs/{name}"
