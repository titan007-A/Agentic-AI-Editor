"""The researcher: Instagram reels for what a person describes, ranked by how well they fit
it and how far they have spread.

Instagram has no open search, so it is searched the way a chat assistant does: web search
engines with "site:instagram.com/reel ..." (the `ddgs` library, free, no key, over several
engines). The planning model turns the description into searches, each reel found is
looked up with yt-dlp without signing in (account, caption, likes, comments, date; views
and length aren't given then), and the planning model rates how well each fits.

Engines index only part of Instagram, mostly popular reels a few days old or more, so a
research finds plenty, never everything."""

import logging
import math
import re
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime

from pydantic import BaseModel, Field

from agentic_editor.llm import LLMError, parse

log = logging.getLogger(__name__)

# A reel's link, also one under an account ("instagram.com/<name>/reel/<code>").
REEL = re.compile(r"https?://(?:www\.|m\.)?instagram\.com/(?:[\w.]+/)?(?:reels?|p)/([\w-]{5,})")
SITE = "site:instagram.com/reel"
MAX_QUERIES = 16
PER_QUERY = 30  # results asked of the engines for each search
MAX_REELS = 60  # reels looked up and rated, in the order the searches found them
PAUSE = 1.0  # seconds between searches, so the engines don't turn it away
LOOKUPS = 4  # reels looked up at once
RATE_BATCH = 10
# Relevance leads: a reel that doesn't fit stays low however viral, and among those that
# fit the viral ones come first.
VIRAL_SHARE = 0.5

Progress = Callable[[str, float], None]


@dataclass
class Reel:
    code: str
    url: str
    query: str  # the search that found it first
    title: str = ""  # as the search engine showed it
    snippet: str = ""
    account: str = ""
    caption: str = ""
    likes: int | None = None
    comments: int | None = None
    posted: datetime | None = None
    thumbnail: str = ""
    looked_up: bool = False
    relevance: float | None = None  # 0-1, None when the model couldn't rate it
    why: str = ""
    virality: float = 0.0  # 0-1 within this research
    score: float = 0.0
    seen: bool = False  # shown by an earlier research


@dataclass
class Result:
    queries: list[str]
    reels: list[Reel]
    used: int = 0  # found but already edited, so left out
    failed_searches: list[str] = field(default_factory=list)


class Searches(BaseModel):
    queries: list[str] = Field(min_length=1, max_length=MAX_QUERIES * 2)


class Rating(BaseModel):
    n: int
    relevance: int = Field(ge=0, le=10)
    why: str = Field(default="", max_length=200)


class Ratings(BaseModel):
    ratings: list[Rating]


PLAN = (
    "You find Instagram reels for a video editor who reposts and edits them. From what they "
    "describe, write {count} short web searches (2-6 words each) that would find such reels: "
    "the artists, songs, concerts, eras, genres, hashtags (without #) and the words people "
    "use in captions, also in the languages the description asks for. Vary them so they find "
    "different reels; don't add 'instagram' or 'reel'.\n\nWhat they want: {description}"
)
RATE = (
    "Rate how well each Instagram reel fits what the editor wants, from its account, caption "
    "and search snippet: 10 = exactly it, 5 = related, 0 = unrelated. Judge only the content "
    "asked for, not popularity. Give one short reason each.\n\nWhat they want: {description}"
    "\n\nReels:\n{reels}"
)


def code_of(link: str) -> str | None:
    match = REEL.match(link.strip())
    return match.group(1) if match else None


def reel_url(code: str) -> str:
    return f"https://www.instagram.com/reel/{code}/"


def plan(llm, description: str) -> list[str]:
    """Searches for the description, the description itself first; just that one when the
    model can't answer."""
    queries = [description.strip()[:120]]
    try:
        prompt = PLAN.format(count=MAX_QUERIES - 1, description=description.strip())
        messages = [{"role": "user", "content": prompt}]
        reply = parse(Searches, llm.chat_json(messages, Searches.model_json_schema()))
        queries += reply.queries
    except LLMError as exc:
        log.warning("couldn't plan searches, searching the description alone: %s", exc)
    tidy = (" ".join(q.replace("#", " ").split()) for q in queries)
    return list(dict.fromkeys(q for q in tidy if q))[:MAX_QUERIES]


def _engines(query: str, count: int) -> list[dict]:
    from ddgs import DDGS
    from ddgs.exceptions import DDGSException

    try:
        return DDGS(timeout=10).text(query, max_results=count)
    except DDGSException as exc:
        # ddgs says an empty answer the way it says a failure: that one is no reels.
        if str(exc).startswith("No results"):
            return []
        raise


def search(
    queries: list[str], find=_engines, pause: float | None = None, progress: Progress | None = None
) -> tuple[list[Reel], list[str]]:
    """Reels the searches find, each once, in the order found; and the searches that
    failed (an engine turned it away, or none answered)."""
    pause = PAUSE if pause is None else pause
    found: dict[str, Reel] = {}
    failed = []
    for n, query in enumerate(queries):
        if n and pause:
            time.sleep(pause)
        if progress:
            progress(f"searching: {query}", 0.05 + 0.35 * n / len(queries))
        try:
            hits = find(f"{SITE} {query}", PER_QUERY)
        except Exception as exc:  # the engines are third parties: one failing is one less
            log.warning("search %r failed: %s", query, exc)
            failed.append(query)
            continue
        for hit in hits:
            code = code_of(hit.get("href", ""))
            if code and code not in found:
                found[code] = Reel(
                    code=code,
                    url=reel_url(code),
                    query=query,
                    title=hit.get("title", "")[:200],
                    snippet=hit.get("body", "")[:500],
                )
    return list(found.values()), failed


class _Quiet:
    """yt-dlp prints its errors even when quiet; a reel that needs signing in is expected."""

    def debug(self, message: str) -> None:
        pass

    info = warning = error = debug


def _info(url: str) -> dict:
    from yt_dlp import YoutubeDL

    options = {
        "quiet": True,
        "no_warnings": True,
        "noplaylist": True,
        "socket_timeout": 15,
        "logger": _Quiet(),
    }
    with YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def look_up(reels: list[Reel], info=_info, workers: int = LOOKUPS) -> None:
    """Fill in each reel's account, caption, likes, comments and date. One Instagram won't
    show without signing in keeps what the search engine said about it."""

    def one(reel: Reel) -> None:
        try:
            meta = info(reel.url)
        except Exception as exc:
            log.info("couldn't look up %s: %s", reel.url, str(exc)[:200])
            return
        reel.account = meta.get("uploader") or meta.get("channel") or ""
        reel.caption = (meta.get("description") or meta.get("title") or "")[:1000]
        reel.likes = _count(meta.get("like_count"))
        reel.comments = _count(meta.get("comment_count"))
        if stamp := meta.get("timestamp"):
            reel.posted = datetime.fromtimestamp(stamp, UTC)
        reel.thumbnail = meta.get("thumbnail") or ""
        reel.looked_up = True

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, reels))


def _count(value) -> int | None:
    return int(value) if isinstance(value, int | float) and value >= 0 else None


def rate(llm, description: str, reels: list[Reel], batch: int = RATE_BATCH) -> None:
    """Each reel's relevance, 0-1, from the planning model. A batch it can't rate keeps
    None, ranked as half relevant."""
    for start in range(0, len(reels), batch):
        group = reels[start : start + batch]
        lines = "\n".join(
            f"{n}. account: {r.account or '?'} | caption: {_short(r.caption or r.title, 300)}"
            f" | search: {_short(r.snippet, 200)}"
            for n, r in enumerate(group, 1)
        )
        prompt = RATE.format(description=description.strip(), reels=lines)
        try:
            reply = parse(
                Ratings,
                llm.chat_json(
                    [{"role": "user", "content": prompt}],
                    Ratings.model_json_schema(),
                    temperature=0.0,
                ),
            )
        except LLMError as exc:
            log.warning("couldn't rate reels %d-%d: %s", start + 1, start + len(group), exc)
            continue
        for rating in reply.ratings:
            if 1 <= rating.n <= len(group):
                group[rating.n - 1].relevance = rating.relevance / 10
                group[rating.n - 1].why = rating.why.strip()


def _short(text: str, limit: int) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def spread(reels: list[Reel], now: datetime | None = None) -> None:
    """Each reel's virality, 0-1 within the research: how many reacted (likes, comments
    counting double), and how fast (per day since it was posted), both on a log scale."""
    now = now or datetime.now(UTC)

    def reactions(r: Reel) -> float:
        return (r.likes or 0) + 2 * (r.comments or 0)

    def per_day(r: Reel) -> float:
        days = max(1.0, (now - r.posted).total_seconds() / 86400) if r.posted else 30.0
        return reactions(r) / days

    total = [math.log1p(reactions(r)) for r in reels]
    fast = [math.log1p(per_day(r)) for r in reels]
    most, fastest = max(total, default=0) or 1, max(fast, default=0) or 1
    for r, t, f in zip(reels, total, fast, strict=True):
        r.virality = round(0.6 * t / most + 0.4 * f / fastest, 3)


def rank(reels: list[Reel]) -> list[Reel]:
    for r in reels:
        relevance = 0.5 if r.relevance is None else r.relevance
        r.score = round(relevance * (1 - VIRAL_SHARE + VIRAL_SHARE * r.virality), 3)
    return sorted(reels, key=lambda r: r.score, reverse=True)


def run(
    description: str,
    planner: Callable,
    used: set[str],
    seen: set[str],
    progress: Progress,
    find=_engines,
    info=_info,
    pause: float | None = None,
) -> Result:
    """Search, look up, rate and rank. `planner()` opens the planning model, which holds
    the GPU, so it is opened only to plan and to rate, never while waiting on the web.
    Reels in `used` (already edited) are left out; those in `seen` are marked."""
    progress("planning the searches", 0.02)
    with planner() as llm:
        queries = plan(llm, description)
    reels, failed = search(queries, find, pause, progress)
    if not reels and failed:
        raise RuntimeError("the search engines didn't answer; try again in a few minutes")
    fresh = [r for r in reels if r.code not in used]
    skipped, reels = len(reels) - len(fresh), fresh[:MAX_REELS]
    progress(f"looking up {len(reels)} reels", 0.45)
    look_up(reels, info)
    progress(f"rating {len(reels)} reels", 0.7)
    if reels:
        with planner() as llm:
            rate(llm, description, reels)
    for r in reels:
        r.seen = r.code in seen
    spread(reels)
    return Result(queries, rank(reels), used=skipped, failed_searches=failed)
