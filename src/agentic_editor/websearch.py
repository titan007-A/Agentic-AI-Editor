"""Web search for the researcher, the account finder and post captions, in one place.

- Yahoo (Bing's index) first, read straight from its result pages. Yahoo serves two page
  layouts at random and ddgs reads only one of them, so about half of its Yahoo answers
  came back "no results"; DuckDuckGo meanwhile timed out or reset most of the time and
  Startpage refused. Both layouts are read here, and a search asks as many Yahoo pages as
  it takes to give the results asked for (Yahoo shows about 7 a page).
- When Yahoo fails, ddgs's engines; SEARXNG_URL, when set, is asked before both.
- Answers are kept on disk for CACHE_DAYS: a schedule or a research run again asks many
  of the same searches, and those come back at once without asking the engines.
- An engine that fails FAILS_TO_REST times running is left alone for REST seconds, and a
  search fails at once while every engine rests, instead of each search waiting out its
  own timeouts (a run once spent 50 minutes on searches that were all refused)."""

import contextlib
import hashlib
import json
import logging
import os
import re
import threading
import time
import urllib.parse
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from secrets import token_urlsafe

import httpx

log = logging.getLogger(__name__)

CACHE_DAYS = 3
FAILS_TO_REST = 3  # failures in a row before an engine is rested
REST = 120.0  # s
TIMEOUT = 12.0  # s, one request
YAHOO_PAGES = 5  # Yahoo pages asked at most for one search
YAHOO_PAUSE = 0.4  # s between the pages of one search
WORKERS = 3  # searches at once, for a caller with many


class SearchError(RuntimeError):
    pass


# --- Yahoo ---------------------------------------------------------------------------

YAHOO = "https://search.yahoo.com/search"
# A result's own link, inside Yahoo's redirect: .../RU=<quoted link>/RK=...
REDIRECT = re.compile(r"/RU=([^/]+)/R[KS]=")
# What Yahoo says when nothing matches: a fine answer, not a failure.
NOTHING = re.compile(r"We did not find results for|did not match any documents", re.I)
TAG = re.compile(r"</?[a-z]+[^>]*>", re.I)


def _link(href: str) -> str:
    found = REDIRECT.search(href or "")
    return urllib.parse.unquote_plus(found.group(1)) if found else (href or "")


def _text(node) -> str:
    # Marked words come as their own pieces ("gym <b>fail</b>.video"): joined as they are.
    return " ".join("".join(node.itertext()).split()) if node is not None else ""


def yahoo_results(page: str) -> list[dict]:
    """The results (href, title, body) on a Yahoo result page, of either layout."""
    from lxml import html

    tree = html.fromstring(page)
    items = tree.xpath(
        "//*[contains(concat(' ', normalize-space(@class), ' '), ' algo ')"
        " or contains(@class, 'relsrch')]"
    )
    seen: set[str] = set()
    hits = []
    for item in items:
        # The first title link in document order is this result's own: the layout without
        # closing tags nests the next result inside this one.
        anchors = item.xpath(
            ".//a[contains(@class, 's-title')] | .//h3//a | .//div[contains(@class, 'Title')]//a"
        )
        if not anchors:
            continue
        anchor = anchors[0]
        href = _link(anchor.get("href", ""))
        if not href.startswith("http") or "bing.com/aclick" in href or "yahoo.com" in href:
            continue
        if href in seen:
            continue
        seen.add(href)
        # One layout labels the link with the title (marked words in <b> tags as text); the
        # other has it in the link's heading, after the address shown above it.
        title = TAG.sub("", anchor.get("aria-label") or "")
        if not title:
            heads = item.xpath(".//h3")
            title = _text(heads[0]) if heads else _text(anchor)
        bodies = item.xpath(
            ".//p[contains(@class, 's-desc')] | .//div[contains(@class, 'compText')]"
            " | .//div[contains(@class, 'Text')]"
        )
        body = _text(bodies[0]) if bodies else ""
        hits.append({"href": href, "title": title.strip(), "body": body})
    return hits


_yahoo_client = None
_yahoo_lock = threading.Lock()


def _client(fresh: bool = False):
    """One browser-like client, kept between searches (its cookies too); a fresh one, with
    another browser's fingerprint, after a failure."""
    global _yahoo_client
    import primp

    with _yahoo_lock:
        if fresh or _yahoo_client is None:
            _yahoo_client = primp.Client(
                impersonate="random", impersonate_os="random", timeout=TIMEOUT,
                follow_redirects=True,
            )  # fmt: skip
        return _yahoo_client


def _yahoo_page(query: str, first: int) -> list[dict]:
    params = {"p": query}
    if first > 1:
        params["b"] = str(first)
    # Fresh tokens in the address, as Yahoo's own links have.
    url = f"{YAHOO};_ylt={token_urlsafe(18)};_ylu={token_urlsafe(35)}"
    for attempt in range(2):
        try:
            response = _client(fresh=attempt > 0).get(url, params=params)
        except Exception as exc:
            if attempt:
                raise SearchError(f"yahoo: {type(exc).__name__}: {exc}") from exc
            continue
        if response.status_code != 200:
            if attempt:
                raise SearchError(f"yahoo answered {response.status_code}")
            continue
        page = response.text
        hits = yahoo_results(page)
        if hits or NOTHING.search(page):
            return hits
        # Neither results nor "nothing found": a page we can't read (a check, a new layout).
        if attempt:
            raise SearchError("yahoo sent a page without results")
    return []


def yahoo(query: str, count: int, page: int = 1) -> list[dict]:
    """Up to `count` results: those from `(page - 1) * count + 1` on, from as many Yahoo
    pages as that takes."""
    first = (page - 1) * count + 1
    hits: list[dict] = []
    seen: set[str] = set()
    for n in range(YAHOO_PAGES):
        if n:
            time.sleep(YAHOO_PAUSE)
        got = _yahoo_page(query, first)
        new = [h for h in got if h["href"] not in seen]
        for hit in new:
            seen.add(hit["href"])
        hits += new
        if not new or len(hits) >= count:
            break
        first += len(got)
    return hits[:count]


# --- the others ----------------------------------------------------------------------


def searxng(query: str, count: int, page: int = 1) -> list[dict]:
    base = os.environ.get("SEARXNG_URL", "").strip()
    url = f"{base.rstrip('/')}/search?" + urllib.parse.urlencode(
        {"q": query, "format": "json", "pageno": page}
    )
    response = httpx.get(url, timeout=TIMEOUT)
    response.raise_for_status()
    data = response.json()
    hits = [
        {"href": r.get("url", ""), "title": r.get("title", ""), "body": r.get("content", "")}
        for r in data.get("results", [])
    ]
    if not hits and data.get("unresponsive_engines"):
        down = ", ".join(e[0] for e in data["unresponsive_engines"][:4])
        raise SearchError(f"no engine answered ({down})")
    return hits[:count]


def ddgs(query: str, count: int, page: int = 1) -> list[dict]:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    try:
        # Not Yahoo again (asked first), nor the encyclopedias ddgs puts first otherwise.
        return DDGS(timeout=10).text(
            query, max_results=count, page=page,
            backend="duckduckgo,brave,mojeek,startpage",
        )  # fmt: skip
    except DDGSException as exc:
        if str(exc).startswith("No results"):
            return []
        raise


Engine = Callable[[str, int, int], list[dict]]


def _engines() -> list[tuple[str, Engine]]:
    order: list[tuple[str, Engine]] = []
    if os.environ.get("SEARXNG_URL", "").strip():
        order.append(("searxng", searxng))
    order += [("yahoo", yahoo), ("ddgs", ddgs)]
    return order


# --- state: failures and the cache ---------------------------------------------------

_lock = threading.Lock()
_fails: dict[str, int] = {}
_rest_until: dict[str, float] = {}


def _resting(name: str) -> bool:
    with _lock:
        return time.monotonic() < _rest_until.get(name, 0.0)


def _failed(name: str, exc: Exception) -> None:
    with _lock:
        _fails[name] = _fails.get(name, 0) + 1
        if _fails[name] >= FAILS_TO_REST:
            _rest_until[name] = time.monotonic() + REST
            _fails[name] = 0
            log.warning("search engine %s failed %d times running (%s): resting it %.0f s",
                        name, FAILS_TO_REST, exc, REST)  # fmt: skip


def _answered(name: str) -> None:
    with _lock:
        _fails[name] = 0


def reset() -> None:
    """Forget failures and rests (tests)."""
    with _lock:
        _fails.clear()
        _rest_until.clear()


def _cache_dir() -> Path | None:
    try:
        from agentic_editor.settings import get_settings

        folder = get_settings().cache_dir / "search"
        folder.mkdir(parents=True, exist_ok=True)
        return folder
    except Exception:
        return None


def _key(query: str, count: int, page: int) -> str:
    return hashlib.sha256(f"{query}\n{count}\n{page}".encode()).hexdigest()[:32]


def _cached(query: str, count: int, page: int) -> list[dict] | None:
    folder = _cache_dir()
    if folder is None:
        return None
    path = folder / f"{_key(query, count, page)}.json"
    try:
        if time.time() - path.stat().st_mtime > CACHE_DAYS * 86400:
            return None
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _keep(query: str, count: int, page: int, hits: list[dict]) -> None:
    folder = _cache_dir()
    if folder is None or not hits:
        return  # nothing found isn't kept: the next try may find something
    with contextlib.suppress(OSError):
        (folder / f"{_key(query, count, page)}.json").write_text(json.dumps(hits))


def search(query: str, count: int = 30, page: int = 1) -> list[dict]:
    """Results (href, title, body) for a query: from the cache when asked lately, else from
    the first engine that answers. An empty list is an answer (nothing found); raises
    SearchError when every engine failed or is resting after failing."""
    if (hit := _cached(query, count, page)) is not None:
        return hit
    problems = []
    for name, engine in _engines():
        if _resting(name):
            problems.append(f"{name} failed just now")
            continue
        try:
            hits = engine(query, count, page)
        except Exception as exc:
            _failed(name, exc)
            problems.append(f"{name}: {exc}"[:200])
            continue
        _answered(name)
        _keep(query, count, page, hits)
        return hits
    raise SearchError("; ".join(problems))


def each(items: list, work: Callable, pause: float, workers: int = WORKERS) -> Iterator:
    """(item, work(item) or the exception it raised) for each item, in order, `workers` at a
    time; each worker pauses `pause` after its own, so the engines aren't asked too fast."""

    def paced(item):
        try:
            return work(item)
        except Exception as exc:  # the engines are third parties: one failing is one less
            return exc
        finally:
            if pause:
                time.sleep(pause)

    if workers <= 1 or len(items) <= 1:
        for item in items:
            yield item, paced(item)
        return
    with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="search") as pool:
        yield from zip(items, pool.map(paced, items), strict=True)
