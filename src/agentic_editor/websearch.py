"""Web search for the researcher, the account finder and post captions, in one place.

- Yahoo (Bing's index) first, read straight from its result pages. Yahoo serves two page
  layouts at random and ddgs reads only one of them, so about half of its Yahoo answers
  came back "no results"; DuckDuckGo meanwhile timed out or reset most of the time and
  Startpage refused. Both layouts are read here, and a search asks as many Yahoo pages as
  it takes to give the results asked for (Yahoo shows about 7 a page).
- When Yahoo fails, ddgs's engines; SEARXNG_URL, when set, is asked before both.
- A "site:" search answered with pages from other sites is a failure, not an answer: Yahoo,
  after many searches from one address, sends unrelated pages (a WhatsApp login, office
  furniture) and ignores "site:". Such an answer is neither used nor kept, and the next
  engine is asked; the pages from other sites in a mixed answer are left out.
- Likewise for another search: a result with none of the search's own words (those left
  when "best", "instagram", "accounts" and the like are taken out) is about something else
  (Yahoo answered "best vocal coach instagram accounts to follow" with dictionary pages for
  "best"); it is left out, and an answer of only such results is a failure.
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


# The site a search is limited to ("site:instagram.com/reel ..." -> instagram.com).
SITE = re.compile(r"(?:^|\s)site:([^\s/]+)", re.I)
SAME_SITE = {"x.com": ("x.com", "twitter.com"), "twitter.com": ("x.com", "twitter.com")}


def on_site(query: str, hits: list[dict]) -> list[dict] | None:
    """The hits that are on the site a "site:" query asks for (all hits for another
    query); None when there were hits and none of them is: the engine ignored "site:"."""
    found = SITE.search(query)
    if not found or not hits:
        return hits
    site = found.group(1).lower().removeprefix("www.")
    sites = SAME_SITE.get(site, (site,))

    def ours(href: str) -> bool:
        host = (urllib.parse.urlsplit(href).hostname or "").lower()
        return any(host == s or host.endswith(f".{s}") for s in sites)

    kept = [h for h in hits if ours(h.get("href", ""))]
    return kept or None


# Words a search has that don't say what it is about: a result needn't have them.
_COMMON = """a an and at best by for from in is of on or the to with top instagram insta ig
account accounts follow followers following post posts page pages profile song songs video
videos reel reels official viral channel creator creators influencer influencers list famous
popular"""
COMMON = frozenset(_COMMON.split())
WORD = re.compile(r"\w+")
OPERATOR = re.compile(r"(?:^|\s)-?(?:site|intitle|inurl|intext):\S*", re.I)
STEM = 5  # letters a longer word and a result's word share to count as one ("pakistani")


def _words(text: str) -> set[str]:
    return set(WORD.findall(text.casefold()))


def _one(word: str) -> str:
    """A word without a plural "s" ("memes" and "meme" are one)."""
    return word[:-1] if len(word) > 3 and word.endswith("s") else word


def on_topic(query: str, hits: list[dict]) -> list[dict] | None:
    """The hits with at least one of the query's own words (in the title, the text or the
    address); None when there were hits and none has one: the engine answered something
    else. A query with no words of its own keeps every hit."""
    asked = {
        _one(w)
        for w in _words(OPERATOR.sub(" ", query))
        if len(w) > 1 and w not in COMMON and not w.isdigit()
    }
    if not asked or not hits:
        return hits

    def about(hit: dict) -> bool:
        address = hit.get("href", "").casefold()
        said = {_one(w) for w in _words(f"{hit.get('title', '')} {hit.get('body', '')} {address}")}
        stems = {w[:STEM] for w in said if len(w) >= STEM}
        return any(
            (w[:STEM] in stems if len(w) >= STEM else w in said)
            # Addresses run words together (instagram.com/gymfails).
            or (len(w) > 2 and w[:STEM] in address)
            for w in asked
        )

    kept = [h for h in hits if about(h)]
    return kept or None


def answers(query: str, hits: list[dict]) -> list[dict] | None:
    """The hits that answer the query (see on_site, on_topic); None when none does."""
    return on_site(query, hits) if SITE.search(query) else on_topic(query, hits)


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


# One client a thread: searches running at once on one connection were turned away by
# Yahoo ("excessive load", too many streams on one HTTP/2 connection).
_yahoo_clients = threading.local()


def _client(fresh: bool = False):
    """A browser-like client for this thread, kept between searches (its cookies too); a
    fresh one, with another browser's fingerprint, after a failure."""
    import primp

    client = getattr(_yahoo_clients, "client", None)
    if fresh or client is None:
        client = primp.Client(
            impersonate="random", impersonate_os="random", timeout=TIMEOUT,
            follow_redirects=True,
        )  # fmt: skip
        _yahoo_clients.client = client
    return client


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
        mine = answers(query, got)
        if mine is None:  # Yahoo turned to unrelated pages: what came before them stands
            if hits:
                break
            raise SearchError(f"yahoo answered unrelated pages ({got[0]['href'][:60]})")
        new = [h for h in mine if h["href"] not in seen]
        for hit in new:
            seen.add(hit["href"])
        hits += new
        if not new or len(hits) >= count:
            break
        first += len(got)
    return hits[:count]


# --- the others ----------------------------------------------------------------------


def _searxng_url() -> str:
    """SEARXNG_URL from the environment or the .env file (the server reads only the file)."""
    if url := os.environ.get("SEARXNG_URL", "").strip():
        return url
    try:
        from agentic_editor.settings import get_settings

        return get_settings().searxng_url.strip()
    except Exception:
        return ""


def searxng(query: str, count: int, page: int = 1) -> list[dict]:
    base = _searxng_url()
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
    if _searxng_url():
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
    hit = _cached(query, count, page)
    if hit is not None and (hit := answers(query, hit)) is not None:
        return hit  # an answer kept before these checks may be all unrelated: asked again
    problems = []
    for name, engine in _engines():
        if _resting(name):
            problems.append(f"{name} failed just now")
            continue
        try:
            hits = engine(query, count, page)
            if (mine := answers(query, hits)) is None:
                other = hits[0].get("href", "")[:60]
                raise SearchError(f"answered unrelated pages ({other})")
            hits = mine
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
