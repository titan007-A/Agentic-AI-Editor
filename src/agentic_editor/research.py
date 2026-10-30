"""The researcher: Instagram reels for what a person describes, ranked by how well they fit
it and how far they have spread.

Instagram has no open search, so it is searched the way a chat assistant does: web search
engines with "site:instagram.com/reel ..." (the `ddgs` library, free, no key, over several
engines). The planning model turns the description into searches, each reel found is
looked up with yt-dlp without signing in (account, caption, likes, comments, date; views
and length aren't given then), and the planning model rates how well each fits.

Engines index only part of Instagram, mostly popular reels a few days old or more, so a
research finds plenty, never everything.

A research can be for one of the owner's Instagram accounts: the account is learned first
(its own reels, found the same way, and what the planning model reads in them), and the
searches and ratings aim at reels like its best ones, for the presets the owner edits it
with. Each research gives its best few: never a reel shown before, the account's own or
one already edited, and no two of the same song."""

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
from agentic_editor.presets import Preset

log = logging.getLogger(__name__)

# A reel's link, also one under an account ("instagram.com/<name>/reel/<code>").
REEL = re.compile(r"https?://(?:www\.|m\.)?instagram\.com/(?:[\w.]+/)?(?:reels?|p)/([\w-]{5,})")
SITE = "site:instagram.com/reel"
MAX_QUERIES = 16
PER_QUERY = 30  # results asked of the engines for each search
MAX_REELS = 40  # reels looked up and rated, in the order the searches found them
SHOWN = 8  # the best this many are kept: the owner picks the ones to edit from them
PER_ACCOUNT = 2  # of them at most from one account, or about one artist
LEARN_REELS = 24  # reels looked up to find an account's own
PAUSE = 1.0  # seconds between searches, so the engines don't turn it away
LOOKUPS = 4  # reels looked up at once
RATE_BATCH = 10
# Reels the model rates below this fit are left out, not shown at the bottom: a search
# also finds what only shares a word with the request.
MIN_FIT = 0.5
MIN_QUERIES = 8  # fewer from the model, and it is asked once more
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
    handle: str = ""  # the account's Instagram name, from the lookup
    looked_up: bool = False
    relevance: float | None = None  # 0-1, None when the model couldn't rate it
    why: str = ""
    song: str = ""  # "Song - Artist" when the model could tell
    artist: str = ""  # the singer or band it is about
    matches: str = ""  # what of the account (or the description) it matches
    preset: str = ""  # the preset it suits best
    preset_why: str = ""
    per_day: float | None = None  # likes and comments a day since it was posted
    virality: float = 0.0  # 0-1 within this research
    score: float = 0.0
    seen: bool = False  # shown by an earlier research


@dataclass
class Result:
    queries: list[str]
    reels: list[Reel]
    used: int = 0  # found but already edited, so left out
    failed_searches: list[str] = field(default_factory=list)
    brief: str = ""  # the request as the model understood it, in English
    unrelated: int = 0  # rated below MIN_FIT, so left out
    repeats: int = 0  # shown by an earlier research, so left out
    considered: int = 0  # reels looked up and rated
    # Reels found but not given, by why: "edited", "shown before", "the account's own",
    # "didn't fit", "same account", "same artist", "same song".
    left_out: dict[str, int] = field(default_factory=dict)
    cutoff: float | None = None  # the best score of the reels that fit but weren't given


@dataclass
class Context:
    """Whom a research is for: an Instagram account the owner runs (its name, what it
    posts, its own reels) and the presets they edit with, by name with what each makes."""

    handle: str = ""
    summary: str = ""
    presets: dict[str, str] = field(default_factory=dict)
    own: set[str] = field(default_factory=set)


@dataclass
class Learned:
    """What a look at an account found: what it posts, its own reels, the best liked."""

    summary: str
    own: list[str]
    best: list[dict]


class Searches(BaseModel):
    brief: str = Field(max_length=400)  # required: left optional, the model skipped it
    queries: list[str] = Field(min_length=1, max_length=MAX_QUERIES * 2)


class Rating(BaseModel):
    n: int
    relevance: int = Field(ge=0, le=10)
    why: str = Field(default="", max_length=200)
    song: str = Field(default="", max_length=120)
    artist: str = Field(default="", max_length=120)
    matches: str = Field(default="", max_length=200)
    preset: str = Field(default="", max_length=80)
    preset_why: str = Field(default="", max_length=200)


class Ratings(BaseModel):
    ratings: list[Rating]


class Summary(BaseModel):
    summary: str = Field(max_length=1500)


# How searches are written, for a request and for an account alike.
SEARCHES = (
    "Then write {count} short web searches (2-6 words each) that find reels of exactly that: "
    "name real, well-known artists and songs of it (for Italian songs, say, Ricchi e Poveri "
    "'Sarà perché ti amo', Eros Ramazzotti, Laura Pausini), its genres, hashtags (without #) "
    "and the words people write in captions about it, in the content's own language too. "
    "Every search must be about the requested content on its own: never a bare city or "
    "place name, never a word of the request's language (such as 'gaane') unless the "
    "content is in that language. Vary them so they find different reels; don't add "
    "'instagram' or 'reel'."
)
PLAN = (
    "You find Instagram reels for a video editor who reposts and edits them. Their request "
    "may be in Hindi, Hinglish or any language ('gaane' means songs, 'dundh' find, 'bdia' "
    "good). First write `brief`: in one English sentence, exactly what content they want "
    "(the music, artists or topic, its language or country, live or studio, any era). "
    + SEARCHES
    + "\n\nTheir request: {description}"
)
PLAN_FOR = (
    "You find Instagram reels for a video editor who runs the Instagram account @{handle} "
    "and reposts reels edited with their presets.\n\nWhat @{handle} posts: {summary}\n\n"
    "Their presets:\n{presets}\n\n{focus}First write `brief`: in one English sentence, "
    "exactly what content to find: reels like the account's best liked ones that are going "
    "viral now and suit the presets (the music, artists or topic, its language or country, "
    "live or studio, any era); their request for this time, which may be in Hindi or "
    "Hinglish, comes first. " + SEARCHES
)
RATE = (
    "Rate how well each Instagram reel fits what the editor wants, from its account, caption "
    "and search snippet. Judge only the content asked for, not popularity: music of another "
    "language or country than the one asked for is 0, and so is a reel that only shares a "
    "word or a place name with the request. For each reel give:\n"
    "- n: its number\n"
    "- relevance: 10 = exactly it, 5 = related, 0 = unrelated\n"
    "- why: one sentence (under 25 words) on why it fits or doesn't\n"
    "- song: the song's title when the caption names one, else ''\n"
    "- artist: the main singer or band it is about, else ''\n"
    "- matches: a few words on which part of what they want it matches, else ''\n"
    "- preset: the name of the preset it suits best, exactly as listed, else ''\n"
    "- preset_why: a few words on why that preset\n"
    "Judge each reel by its own caption: reels found by the same search are often about "
    "different songs and fit differently.\n\n"
    "What they want: {wish}\n\nTheir presets:\n{presets}\n\nReels:\n{reels}"
)
LEARN = (
    "These are reels the Instagram account @{handle} posted, with their likes. In a few "
    "sentences say what the account posts: the artists, songs, eras, genres and languages, "
    "the format (for example isolated vocals, live concerts, the story behind a song), the "
    "words and hashtags its captions use, and which kind of its reels got the most likes."
    "\n\nReels:\n{reels}"
)


def code_of(link: str) -> str | None:
    match = REEL.match(link.strip())
    return match.group(1) if match else None


def reel_url(code: str) -> str:
    return f"https://www.instagram.com/reel/{code}/"


def handle_of(text: str) -> str | None:
    """An Instagram account's name from "@name", "name" or its profile link."""
    text = text.strip()
    if match := re.match(r"https?://(?:www\.|m\.)?instagram\.com/([\w.]+)/?(?:\?.*)?$", text):
        text = match.group(1)
    text = text.removeprefix("@").lower()
    return text if re.fullmatch(r"[a-z0-9._]{1,30}", text) else None


def describe_presets(presets: dict[str, Preset]) -> dict[str, str]:
    """What each preset makes, in a line the planning model can match reels to."""
    lines = {}
    for name, preset in presets.items():
        parts = [preset.label]
        if preset.audio.source == "vocals":
            parts.append("isolated vocals, the music removed")
        texts = [" ".join(b.text.split()) for b in preset.text_blocks if b.text.strip()]
        if texts:
            parts.append("text on the reel: " + _short(" / ".join(texts), 200))
        if preset.headline.hint and any("{headline}" in b.text for b in preset.text_blocks):
            parts.append("headline: " + _short(preset.headline.hint, 150))
        if preset.post_caption.enabled:
            parts.append(f"post caption in '{preset.post_caption.language}'")
        if preset.style_notes:
            parts.append(_short(preset.style_notes, 150))
        lines[name] = "; ".join(parts)
    return lines


def _preset_lines(presets: dict[str, str]) -> str:
    return "\n".join(f"- {name}: {about}" for name, about in presets.items()) or "(none)"


def plan(llm, description: str, context: Context | None = None) -> tuple[str, list[str]]:
    """The request as the model understood it, in English, and the searches for it. The
    request itself, in whatever words, isn't searched ("italian gaane" finds Hindi songs),
    unless the model can't answer at all. For an account the description is an optional
    focus; without one, a model that can't answer leaves the account's name and the
    presets to search."""
    focus = description.strip()
    if context and context.handle:
        prompt = PLAN_FOR.format(
            handle=context.handle,
            summary=context.summary or "(not known yet: go by the presets)",
            presets=_preset_lines(context.presets),
            focus=f"Their request for this time: {focus}\n\n" if focus else "",
            count=MAX_QUERIES,
        )
        fallback = [focus[:120]] if focus else [context.handle]
        fallback += [about.split(";")[0] for about in context.presets.values()]
    else:
        prompt = PLAN.format(count=MAX_QUERIES, description=focus)
        fallback = [focus[:120]]
    brief, queries = "", []
    messages = [{"role": "user", "content": prompt}]
    for _ in range(2):
        try:
            reply = parse(Searches, llm.chat_json(messages, Searches.model_json_schema()))
        except LLMError as exc:
            log.warning("couldn't plan searches: %s", exc)
            continue
        brief = brief or reply.brief.strip()
        queries += reply.queries
        if len(_tidy(queries)) >= MIN_QUERIES:
            break
    return brief, (_tidy(queries) or _tidy(fallback))[:MAX_QUERIES]


def _tidy(queries: list[str]) -> list[str]:
    tidy = (" ".join(q.replace("#", " ").split()) for q in queries)
    return list(dict.fromkeys(q for q in tidy if q))


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
        reel.handle = (meta.get("channel") or "").lower()
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


def rate(
    llm,
    description: str,
    reels: list[Reel],
    batch: int = RATE_BATCH,
    context: Context | None = None,
) -> None:
    """Each reel's relevance, 0-1, from the planning model, with why, its song, what it
    matches and the preset it suits best. A batch it can't rate keeps None, ranked as half
    relevant."""
    context = context or Context()
    wish = description.strip()
    if context.handle:
        account = f"reels like those of @{context.handle}, which posts: " + (
            context.summary or "(not known: go by the presets)"
        )
        wish = f"{wish}\nThat is, {account}" if wish else account
    for start in range(0, len(reels), batch):
        group = reels[start : start + batch]
        lines = "\n".join(
            f"{n}. account: {r.account or '?'} | caption: {_short(r.caption or r.title, 300)}"
            f" | search: {_short(r.snippet, 200)}"
            for n, r in enumerate(group, 1)
        )
        prompt = RATE.format(wish=wish, presets=_preset_lines(context.presets), reels=lines)
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
                reel = group[rating.n - 1]
                reel.relevance = rating.relevance / 10
                reel.why = rating.why.strip()
                reel.artist = rating.artist.strip()
                title = rating.song.strip()
                reel.song = f"{title} - {reel.artist}" if title and reel.artist else title
                reel.matches = rating.matches.strip()
                if rating.preset.strip() in context.presets:
                    reel.preset = rating.preset.strip()
                    reel.preset_why = rating.preset_why.strip()


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

    for r in reels:
        r.per_day = round(per_day(r), 1) if r.posted and reactions(r) else None
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


def _same(text: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def pick(
    ranked: list[Reel], count: int = SHOWN, per_account: int = PER_ACCOUNT
) -> tuple[list[Reel], dict[str, int], float | None]:
    """The best `count` of the ranked reels: at most `per_account` from one account and as
    many about one artist, and one of each song; how many were passed over for each, and
    the best score left."""
    picked: list[Reel] = []
    passed = {"same account": 0, "same artist": 0, "same song": 0}
    accounts: dict[str, int] = {}
    artists: dict[str, int] = {}
    songs: set[str] = set()
    rest: list[Reel] = []
    for reel in ranked:
        if len(picked) == count:
            rest.append(reel)
            continue
        who, artist, song = _same(reel.handle or reel.account), _same(reel.artist), _same(reel.song)
        if who and accounts.get(who, 0) >= per_account:
            passed["same account"] += 1
            continue
        if artist and artists.get(artist, 0) >= per_account:
            passed["same artist"] += 1
            continue
        if song and song in songs:
            passed["same song"] += 1
            continue
        picked.append(reel)
        accounts[who] = accounts.get(who, 0) + 1
        if artist:
            artists[artist] = artists.get(artist, 0) + 1
        if song:
            songs.add(song)
    cutoff = rest[0].score if rest else None
    return picked, {k: v for k, v in passed.items() if v}, cutoff


def learn(
    handle: str,
    planner: Callable,
    progress: Progress,
    find=_engines,
    info=_info,
    pause: float | None = None,
) -> Learned:
    """Find the account's own reels and read what it posts. With none found the summary
    is empty, and a research goes by the presets."""
    progress(f"looking for @{handle}'s own reels", 0.03)
    reels, failed = search([f'"{handle}"', handle], find, pause)
    if not reels and failed:
        raise RuntimeError("the search engines didn't answer; try again in a few minutes")
    reels = reels[:LEARN_REELS]
    progress(f"looking up {len(reels)} reels to find @{handle}'s own", 0.08)
    look_up(reels, info)
    own = sorted((r for r in reels if r.handle == handle), key=lambda r: r.likes or 0)[::-1]
    if not own:
        return Learned("", [], [])
    progress(f"reading what @{handle} posts", 0.15)
    lines = "\n".join(
        f"- {'?' if r.likes is None else r.likes} likes: {_short(r.caption or r.title, 250)}"
        for r in own[:15]
    )
    summary = ""
    try:
        with planner() as llm:
            messages = [{"role": "user", "content": LEARN.format(handle=handle, reels=lines)}]
            summary = parse(Summary, llm.chat_json(messages, Summary.model_json_schema())).summary
    except LLMError as exc:
        log.warning("couldn't read what @%s posts: %s", handle, exc)
    best = [
        {"code": r.code, "likes": r.likes, "caption": _short(r.caption or r.title, 200)}
        for r in own[:5]
    ]
    return Learned(summary.strip(), [r.code for r in own], best)


def run(
    description: str,
    planner: Callable,
    used: set[str],
    seen: set[str],
    progress: Progress,
    find=_engines,
    info=_info,
    pause: float | None = None,
    context: Context | None = None,
) -> Result:
    """Search, look up, rate, rank and pick the best few. `planner()` opens the planning
    model, which holds the GPU, so it is opened only to plan and to rate, never while
    waiting on the web. Reels in `used` (already edited), in `seen` (shown by an earlier
    research), the account's own and those that don't fit are left out, so each research
    brings new ones."""
    context = context or Context()
    progress("planning the searches", 0.02)
    with planner() as llm:
        brief, queries = plan(llm, description, context)
    reels, failed = search(queries, find, pause, progress)
    if not reels and failed:
        raise RuntimeError("the search engines didn't answer; try again in a few minutes")
    left_out = {
        "edited": sum(r.code in used for r in reels),
        "shown before": sum(r.code in seen and r.code not in used for r in reels),
        "the account's own": sum(r.code in context.own - used - seen for r in reels),
    }
    reels = [r for r in reels if r.code not in used | seen | context.own][:MAX_REELS]
    progress(f"looking up {len(reels)} reels", 0.45)
    look_up(reels, info)
    if context.handle:
        left_out["the account's own"] += sum(r.handle == context.handle for r in reels)
        reels = [r for r in reels if r.handle != context.handle]
    progress(f"rating {len(reels)} reels", 0.7)
    if reels:
        with planner() as llm:
            # The brief says it plainly; the request keeps what the brief may have lost.
            wanted = description.strip()
            if brief:
                wanted = f"{brief} (their words: {wanted})" if wanted else brief
            rate(llm, wanted, reels, context=context)
    fitting = [r for r in reels if r.relevance is None or r.relevance >= MIN_FIT]
    left_out["didn't fit"] = len(reels) - len(fitting)
    spread(fitting)
    picked, passed, cutoff = pick(rank(fitting))
    left_out = {k: v for k, v in {**left_out, **passed}.items() if v}
    return Result(
        queries,
        picked,
        used=left_out.get("edited", 0),
        failed_searches=failed,
        brief=brief,
        unrelated=left_out.get("didn't fit", 0),
        repeats=left_out.get("shown before", 0),
        considered=len(reels),
        left_out=left_out,
        cutoff=cutoff,
    )
