"""The researcher: Instagram reels and YouTube Shorts for what a person describes, ranked by
how well they fit it and how far they have spread.

Instagram has no open search, so it is searched the way a chat assistant does: web search
engines with "site:instagram.com/reel ..." (the `ddgs` library, free, no key, over several
engines). The planning model turns the description into searches, each reel found is
looked up with yt-dlp without signing in (account, caption, likes, comments, date; views
and length aren't given then), and the planning model rates how well each fits.

Engines index only part of Instagram, mostly popular reels a few days old or more, so a
research finds plenty, never everything. YouTube is searched the same way
("site:youtube.com/shorts ..."), for Shorts only: a link to a video, or one YouTube says is
longer than a Short, is left out.

A research can be for one of the owner's Instagram accounts: the account is learned first
(its own reels, found the same way, and what the planning model reads in them), and the
searches and ratings aim at reels like its best ones, for the presets the owner edits it
with. Each research gives its best few: never a reel shown before, the account's own or
one already edited, and at most two from one account or of one song.

The person's feedback teaches later researches: the reels they liked or edited and those
they didn't want (with why) go to planning and rating, and accounts they hid are left out."""

import logging
import math
import re
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from agentic_editor.llm import LLMError, parse
from agentic_editor.presets import Preset

log = logging.getLogger(__name__)

# A reel's link, also one under an account ("instagram.com/<name>/reel/<code>").
REEL = re.compile(r"https?://(?:www\.|m\.)?instagram\.com/(?:[\w.]+/)?(?:reels?|p)/([\w-]{5,})")
# A YouTube video's link; a research takes only Shorts ("/shorts/"), but an edited one may
# have been given as a watch link too.
VIDEO = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:shorts/|watch\?v=)|youtu\.be/)([\w-]{11})"
)
YOUTUBE = "yt:"  # a Short's code is this and its video id: Instagram codes have no colon
# Where a research searches, as the engines are asked.
SITES = {"instagram": "site:instagram.com/reel", "shorts": "site:youtube.com/shorts"}
SITE = SITES["instagram"]
MAX_SHORT = 180  # seconds: YouTube's longest Short; longer is a video, left out
# Picture quality, by the short side of the best version the site offers: below SD is left
# out (it looks soft once edited at 1080 wide), SD ranks a little lower than HD.
SD, HD = 480, 720
# A YouTube channel's link, of any of its tabs.
CHANNEL = re.compile(
    r"https?://(?:www\.|m\.)?youtube\.com/(@[\w.-]+|channel/[\w-]+)(?:/\w*)?/?(?:\?.*)?$"
)
MAX_SOURCES = 10  # source pages an account may have
PER_SOURCE = 5  # the latest reels taken from each
QUALITY = {"HD": 1.0, "SD": 0.85, "": 0.95}  # "" when the site didn't say
MAX_QUERIES = 16
PER_QUERY = 30  # results asked of the engines for each search
MAX_REELS = 40  # reels looked up and rated, in the order the searches found them
SHOWN = 8  # the best this many are kept: the owner picks the ones to edit from them
PER_ACCOUNT = 2  # of them at most from one account, so one page doesn't fill the research
PER_SONG = 2  # and from one song: the owner's "a song may come twice", not seven times
LEARN_REELS = 24  # reels looked up to find an account's own
PAUSE = 1.0  # seconds between searches, so the engines don't turn it away
LOOKUPS = 4  # reels looked up at once
RATE_BATCH = 10
# Reels the model rates below this fit are left out, not shown at the bottom: a search
# also finds what only shares a word with the request.
MIN_FIT = 0.5
MIN_QUERIES = 8  # fewer from the model, and it is asked once more
MAX_QUESTIONS = 6  # asked before a research
ANSWERED = "Their answers to the researcher's questions:"
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
    handle: str = ""  # the account's Instagram name (a YouTube channel's), from the lookup
    looked_up: bool = False
    views: int | None = None  # YouTube gives them; Instagram doesn't without signing in
    long: bool = False  # a YouTube video, not a Short: left out
    page: str = ""  # the Instagram source page it was searched for: kept only when its own
    width: int | None = None  # of the best version the site offers
    height: int | None = None
    bitrate: float | None = None  # kbit/s of that version
    duration: float | None = None  # seconds; YouTube gives it, Instagram doesn't
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

    @property
    def short(self) -> bool:
        return self.code.startswith(YOUTUBE)

    @property
    def quality(self) -> str:
        """ "HD", "SD" or "low" by its picture's short side; "" when not known."""
        if not (self.width and self.height):
            return ""
        side = min(self.width, self.height)
        return "HD" if side >= HD else "SD" if side >= SD else "low"


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
    # "didn't fit", "hidden account", "not a Short", "low quality", "same account".
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
    # Pages the owner takes reels from ("instagram:name", "youtube:@name"): their latest
    # are looked at in every research, besides what the searches find.
    sources: list[str] = field(default_factory=list)
    # What the person's feedback on earlier researches taught: reels they liked (or
    # edited) and didn't want, a line each (see taste_line), and accounts they hid.
    liked: list[str] = field(default_factory=list)
    disliked: list[str] = field(default_factory=list)
    hidden: set[str] = field(default_factory=set)


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
    # All required: the 9b model leaves out any it may, and then a research can't keep two
    # reels of one song at most, or explain its picks.
    why: str = Field(max_length=200)
    song: str = Field(max_length=120)
    artist: str = Field(max_length=120)
    matches: str = Field(max_length=200)
    preset: str = Field(max_length=80)
    preset_why: str = Field(max_length=200)


class Ratings(BaseModel):
    ratings: list[Rating]


class Summary(BaseModel):
    summary: str = Field(max_length=1500)


class Question(BaseModel):
    question: str = Field(max_length=200)
    choices: list[str] = Field(min_length=2, max_length=5)


class Questions(BaseModel):
    questions: list[Question] = Field(min_length=1, max_length=8)


class Reply(BaseModel):
    # All required, as for ratings: the model leaves out what it may.
    kind: Literal["answer", "refine"]
    reply: str = Field(max_length=800)
    request: str = Field(max_length=400)


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
    "Rate how well each reel (an Instagram reel or a YouTube Short) fits what the editor "
    "wants, from its account, caption "
    "and search snippet. Judge only the content asked for, not popularity: music of another "
    "language or country than the one asked for is 0, and so is a reel that only shares a "
    "word or a place name with the request. For each reel give:\n"
    "- n: its number\n"
    "- relevance: 10 = exactly it, 5 = related, 0 = unrelated\n"
    "- why: one sentence (under 25 words) on why it fits or doesn't\n"
    "- song: the song's title whenever the caption or search snippet lets you tell it (if "
    "your why names it, so does this), else ''\n"
    "- artist: the main singer or band it is about, whenever you can tell, else ''\n"
    "- matches: a few words on which part of what they want it matches, else ''\n"
    "- preset: the name of the preset it suits best, exactly as listed, else ''\n"
    "- preset_why: a few words on why that preset\n"
    "Judge each reel by its own caption: reels found by the same search are often about "
    "different songs and fit differently. Judge the music by its artist and song, not by "
    "the language its caption is written in: a Spanish caption about an Italian song is "
    "still an Italian song. A preset's language is the text the editor adds, not something "
    "the reel must have.\n\n"
    "What they want: {wish}\n\nTheir presets:\n{presets}\n\nReels:\n{reels}"
)
ASK = (
    "You find Instagram reels for a video editor who reposts and edits them. Before "
    "searching, ask them up to {count} short questions whose answers would change which "
    "reels to find: for example live or studio, which eras, which languages or countries, "
    "which of their presets it is for, well-known hits or hidden gems, which artists or "
    "songs. Don't ask what they already said. Never ask about captions, subtitles, text, "
    "format, aspect ratio, length, isolated vocals, instruments or editing: their presets "
    "decide those. Give each question 2-5 suggested answers of a few words, each a direct "
    "answer to that question that fits their request: artists you suggest must really "
    "match it (Italian artists for Italian songs). Reels are found days to years after "
    "they are posted, so never offer the last day or hours. Write the questions and the "
    "answers in English, whatever language they wrote in.\n\n{about}"
)
# What the presets decide; the model asks about it anyway at times.
EDITING = re.compile(
    r"\b(subtitles?|captions?|on-screen|text|aspect|ratio|vertical|format|length|duration|"
    r"seconds|continuous|translat\w*|isolated|instruments?|instrumental|a ?cappella|"
    r"edit(s|ed|ing)?|fonts?)\b",
    re.IGNORECASE,
)
CHAT = (
    "You are the researcher who found these Instagram reels for a video editor. Below is "
    "what you looked for and why you gave each reel. They wrote a message.\n"
    "- If they ask about the reels (why one, which is the most viral, which suits a "
    "preset...), set kind 'answer': answer in reply from what is below only, short and "
    "plain, naming reels by their number; request ''.\n"
    "- If they want other or more reels ('more Italian', 'older concerts', 'no Michael "
    "Jackson'), set kind 'refine': in request, one English sentence of what a new research "
    "should find, keeping what still holds of what was looked for; in reply, one line "
    "saying what you will look for.\n"
    "Reply in the language they wrote in.\n\n{about}\n\nThe chat so far:\n{history}\n\n"
    "Their message: {message}"
)
LEARN = (
    "These are reels the Instagram account @{handle} posted, with their likes. In a few "
    "sentences say what the account posts: the artists, songs, eras, genres and languages, "
    "the format (for example isolated vocals, live concerts, the story behind a song), the "
    "words and hashtags its captions use, and which kind of its reels got the most likes."
    "\n\nReels:\n{reels}"
)


def code_of(link: str) -> str | None:
    """An Instagram reel's code, or "yt:" and a YouTube video's id."""
    if match := REEL.match(link.strip()):
        return match.group(1)
    if match := VIDEO.match(link.strip()):
        return YOUTUBE + match.group(1)
    return None


def reel_url(code: str) -> str:
    if code.startswith(YOUTUBE):
        return f"https://www.youtube.com/shorts/{code.removeprefix(YOUTUBE)}"
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


def taste_line(song: str, caption: str, handle: str, reason: str = "") -> str:
    """A reel the person gave feedback on, as the model is told it."""
    what = song or _short(caption, 120) or "a reel"
    line = f"{what} (@{handle})" if handle else what
    return f"{line}: {reason}" if reason else line


def _taste(context: Context) -> str:
    """What the person's feedback taught, for planning and rating; '' without any."""
    parts = []
    if context.liked:
        parts.append(
            "Reels they liked before (find more like these):\n"
            + "\n".join(f"- {line}" for line in context.liked)
        )
    if context.disliked:
        parts.append(
            "Reels they didn't want, with why when they said (find fewer like these):\n"
            + "\n".join(f"- {line}" for line in context.disliked)
        )
    return "\n\n".join(parts)


def plan(llm, description: str, context: Context | None = None) -> tuple[str, list[str]]:
    """The request as the model understood it, in English, and the searches for it. The
    request itself, in whatever words, isn't searched ("italian gaane" finds Hindi songs),
    unless the model can't answer at all. For an account the description is an optional
    focus; without one, a model that can't answer leaves the account's name and the
    presets to search."""
    focus = description.strip()
    # Searched verbatim only as a last resort: the person's words, not the answers after them.
    asked = focus.partition(ANSWERED)[0].strip()
    if context and context.handle:
        prompt = PLAN_FOR.format(
            handle=context.handle,
            summary=context.summary or "(not known yet: go by the presets)",
            presets=_preset_lines(context.presets),
            focus=f"Their request for this time: {focus}\n\n" if focus else "",
            count=MAX_QUERIES,
        )
        fallback = [asked[:120]] if asked else [context.handle]
        fallback += [about.split(";")[0] for about in context.presets.values()]
    else:
        prompt = PLAN.format(count=MAX_QUERIES, description=focus)
        fallback = [asked[:120]]
    if taste := _taste(context or Context()):
        prompt += "\n\n" + taste
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


def _about(description: str, context: Context) -> str:
    """Whom a research is for and what they asked, as the model is told it."""
    parts = []
    if context.handle:
        summary = context.summary or "(not known yet)"
        parts.append(f"They run the Instagram account @{context.handle}, which posts: {summary}")
    if description.strip():
        parts.append(f"Their request: {description.strip()}")
    parts.append(f"Their presets:\n{_preset_lines(context.presets)}")
    return "\n\n".join(parts)


def ask(llm, description: str, context: Context | None = None) -> list[dict]:
    """A few questions whose answers change which reels to find, each with suggested
    answers; none when the model can't answer (the research then goes on without)."""
    prompt = ASK.format(count=MAX_QUESTIONS, about=_about(description, context or Context()))
    try:
        reply = parse(
            Questions,
            llm.chat_json([{"role": "user", "content": prompt}], Questions.model_json_schema()),
        )
    except LLMError as exc:
        log.warning("couldn't think of questions: %s", exc)
        return []
    asked = []
    for q in reply.questions[:MAX_QUESTIONS]:
        once: dict[str, str] = {}  # each choice once, whatever its case, as first written
        for choice in (c.strip() for c in q.choices):
            if choice:
                once.setdefault(choice.casefold(), choice)
        choices = list(once.values())
        if q.question.strip() and choices and not EDITING.search(q.question):
            asked.append({"question": q.question.strip(), "choices": choices})
    return asked


def with_answers(description: str, answers: list[dict]) -> str:
    """The request with the answers to the questions after it, for planning and rating."""
    lines = [
        f"- {a['question']} {a['answer'].strip()}" for a in answers if a.get("answer", "").strip()
    ]
    if not lines:
        return description
    answered = ANSWERED + "\n" + "\n".join(lines)
    return f"{description.strip()}\n{answered}" if description.strip() else answered


def describe(found) -> str:
    """A finished research (the database's) as the chat model is told it: what was looked
    for, and each pick with the reasoning stored for it."""
    lines = [f"What was looked for: {found.brief or found.description or '(not known)'}"]
    if found.handle:
        lines.append(f"For the Instagram account @{found.handle}")
    if found.description:
        lines.append(f"Their request: {found.description}")
    lines += [f"- {a['question']} {a['answer']}" for a in found.answers or []]
    if found.queries:
        lines.append("Searches made: " + "; ".join(found.queries))
    if found.left_out:
        left = ", ".join(f"{n} {why}" for why, n in found.left_out.items())
        lines.append(f"Reels found but not given: {left}")
    lines.append(f"Reels rated: {found.considered}; given, best first:")
    for item in found.items:
        who = f"@{item.handle}" if item.handle else item.account or "?"
        fit = "?" if item.relevance is None else f"{round(item.relevance * 10)}/10"
        lines.append(
            f"#{item.rank + 1} {'YouTube Short' if item.code.startswith(YOUTUBE) else 'reel'}"
            f" by {who}: song {item.song or '?'}; fit {fit} ({item.why}); "
            f"matches {item.matches or '?'}; preset {item.preset or '-'} ({item.preset_why}); "
            f"{item.likes if item.likes is not None else '?'} likes, "
            f"{item.comments if item.comments is not None else '?'} comments, "
            f"{item.per_day if item.per_day is not None else '?'} a day; "
            f"viral {round(item.virality * 100)}%; score {item.score:.2f}; "
            f"caption: {_short(item.caption, 160)}"
        )
    return "\n".join(lines)


def chat(llm, about: str, history: list[tuple[str, str]], message: str) -> Reply:
    """The researcher's answer to a chat message on a research `about` (see describe):
    an answer from its reasoning, or a request for a follow-up research."""
    said = "\n".join(f"{who}: {text}" for who, text in history) or "(nothing yet)"
    prompt = CHAT.format(about=about, history=said, message=message)
    return parse(
        Reply, llm.chat_json([{"role": "user", "content": prompt}], Reply.model_json_schema())
    )


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
    queries: list[str],
    find=_engines,
    pause: float | None = None,
    progress: Progress | None = None,
    sites: tuple[str, ...] = ("instagram",),
) -> tuple[list[Reel], list[str]]:
    """Reels the searches find on each of `sites` (see SITES), each once, in the order
    found; and the searches that failed (an engine turned it away, or none answered)."""
    pause = PAUSE if pause is None else pause
    found: dict[str, Reel] = {}
    failed = []
    asked = [(query, site) for query in queries for site in sites]
    for n, (query, site) in enumerate(asked):
        if n and pause:
            time.sleep(pause)
        if progress:
            where = " (YouTube Shorts)" if site == "shorts" else ""
            progress(f"searching: {query}{where}", 0.05 + 0.35 * n / len(asked))
        try:
            hits = find(f"{SITES[site]} {query}", PER_QUERY)
        except Exception as exc:  # the engines are third parties: one failing is one less
            log.warning("search %r on %s failed: %s", query, site, exc)
            failed.append(query)
            continue
        for hit in hits:
            link = hit.get("href", "")
            code = code_of(link)
            if code and code.startswith(YOUTUBE) and "/shorts/" not in link:
                continue  # a video, not a Short
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
        if reel.short:
            # A Short is vertical and at most MAX_SHORT long; YouTube says which it is.
            kind, length = meta.get("media_type"), meta.get("duration") or 0
            reel.long = kind not in (None, "short") or length > MAX_SHORT
            reel.account = meta.get("channel") or meta.get("uploader") or ""
            reel.handle = (meta.get("uploader_id") or "").removeprefix("@").lower()
            title, about = meta.get("title") or "", meta.get("description") or ""
            reel.caption = "\n".join(t for t in (title, about) if t)[:1000]
            reel.views = _count(meta.get("view_count"))
        else:
            reel.account = meta.get("uploader") or meta.get("channel") or ""
            reel.handle = (meta.get("channel") or "").lower()
            reel.caption = (meta.get("description") or meta.get("title") or "")[:1000]
        reel.likes = _count(meta.get("like_count"))
        reel.comments = _count(meta.get("comment_count"))
        if stamp := meta.get("timestamp"):
            reel.posted = datetime.fromtimestamp(stamp, UTC)
        reel.thumbnail = meta.get("thumbnail") or ""
        best = max(
            (f for f in meta.get("formats") or [] if f.get("height") and f.get("width")),
            key=lambda f: (f["height"] * f["width"], f.get("tbr") or 0),
            default=meta,
        )
        reel.width, reel.height = _count(best.get("width")), _count(best.get("height"))
        reel.bitrate = best.get("tbr") if isinstance(best.get("tbr"), int | float) else None
        if isinstance(meta.get("duration"), int | float):
            reel.duration = float(meta["duration"])
        reel.looked_up = True

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(one, reels))


def source_of(text: str) -> str | None:
    """A source page as stored: "youtube:@name" (or "youtube:channel/UC...") from a YouTube
    channel's link, "instagram:name" from an Instagram @name or profile link."""
    text = text.strip()
    if match := re.match(CHANNEL, text):
        return "youtube:" + match.group(1)
    if handle := handle_of(text):
        return "instagram:" + handle
    return None


def _channel_shorts(url: str, count: int) -> list[dict]:
    """A YouTube channel's latest Shorts, newest first, as its Shorts tab lists them."""
    from yt_dlp import YoutubeDL

    options = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
    with YoutubeDL({**options, "playlistend": count, "logger": _Quiet()}) as ydl:
        return ydl.extract_info(url, download=False).get("entries") or []


def from_sources(
    sources: list[str], find=_engines, pause: float | None = None, channel=_channel_shorts
) -> list[Reel]:
    """The latest reels of each source page: a YouTube channel's Shorts tab lists them; an
    Instagram page can't be listed without signing in, so it is searched for, and what the
    search finds is kept only when it is the page's own (see run)."""
    found: dict[str, Reel] = {}
    for source in sources[:MAX_SOURCES]:
        kind, name = source.split(":", 1)
        if kind == "youtube":
            try:
                entries = channel(f"https://www.youtube.com/{name}/shorts", PER_SOURCE)
            except Exception as exc:
                log.warning("couldn't list %s's Shorts: %s", name, exc)
                continue
            for entry in entries:
                link = entry.get("url") or ""
                code = code_of(link)
                if code and "/shorts/" in link and code not in found:  # never a video
                    title = entry.get("title") or ""
                    found[code] = Reel(code, reel_url(code), f"from {name}", title=title)
        else:
            reels, _ = search([f'"{name}"'], find, pause)
            for reel in reels[: PER_SOURCE * 2]:  # half of what a search finds is others'
                reel.query, reel.page = f"from @{name}", name
                found.setdefault(reel.code, reel)
    return list(found.values())


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
    if taste := _taste(context):
        wish = f"{wish}\n\n{taste}"
    for start in range(0, len(reels), batch):
        group = reels[start : start + batch]
        lines = "\n".join(
            f"{n}. {'YouTube Short' if r.short else 'Instagram reel'}"
            f" | account: {r.account or '?'} | caption: {_short(r.caption or r.title, 300)}"
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
        viral = 1 - VIRAL_SHARE + VIRAL_SHARE * r.virality
        r.score = round(relevance * viral * QUALITY.get(r.quality, 1.0), 3)
    return sorted(reels, key=lambda r: r.score, reverse=True)


def _same(text: str) -> str:
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def pick(
    ranked: list[Reel], count: int = SHOWN, per_account: int = PER_ACCOUNT, per_song: int = PER_SONG
) -> tuple[list[Reel], dict[str, int], float | None]:
    """The best `count` of the ranked reels, at most `per_account` from one account (by its
    Instagram name, its display name when that isn't known) and `per_song` of one song, so
    one page or one hit doesn't fill the research; how many were passed over for each, and
    the best score left. A reel itself is never given twice (see run)."""
    picked: list[Reel] = []
    passed = {"same account": 0, "same song": 0}
    accounts: dict[str, int] = {}
    songs: dict[str, int] = {}
    rest: list[Reel] = []
    for reel in ranked:
        if len(picked) == count:
            rest.append(reel)
            continue
        who, song = _same(reel.handle or reel.account), _same(reel.song)
        if who and accounts.get(who, 0) >= per_account:
            passed["same account"] += 1
            continue
        if song and songs.get(song, 0) >= per_song:
            passed["same song"] += 1
            continue
        picked.append(reel)
        accounts[who] = accounts.get(who, 0) + 1
        songs[song] = songs.get(song, 0) + 1
    cutoff = rest[0].score if rest else None
    return picked, {k: n for k, n in passed.items() if n}, cutoff


def suggest(reels: list[Reel], context: Context) -> None:
    """A preset for every reel: one the model left without gets the preset it chose most
    for the others, or the account's first."""
    if not context.presets:
        return
    chosen = Counter(r.preset for r in reels if r.preset)
    default = chosen.most_common(1)[0][0] if chosen else next(iter(context.presets))
    for r in reels:
        if not r.preset:
            r.preset = default
            r.preset_why = (
                "the model didn't choose one; the preset it chose most for the others"
                if chosen
                else "the model didn't choose one; the account's first preset"
            )


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
    sites: tuple[str, ...] = ("instagram",),
    channel=_channel_shorts,
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
    reels, failed = search(queries, find, pause, progress, sites)
    if not reels and failed:
        raise RuntimeError("the search engines didn't answer; try again in a few minutes")
    if context.sources:
        progress("looking at your source pages", 0.42)
        # The pages first, up to half of what is looked up, so the searches still count.
        picked = from_sources(context.sources, find, pause, channel)[: MAX_REELS // 2]
        taken = {r.code for r in picked}
        reels = picked + [r for r in reels if r.code not in taken]
    left_out = {
        "edited": sum(r.code in used for r in reels),
        "shown before": sum(r.code in seen and r.code not in used for r in reels),
        "the account's own": sum(r.code in context.own - used - seen for r in reels),
    }
    reels = [r for r in reels if r.code not in used | seen | context.own][:MAX_REELS]
    progress(f"looking up {len(reels)} reels", 0.45)
    look_up(reels, info)
    reels = [r for r in reels if not r.page or r.handle == r.page]  # another's, found by name
    if context.handle:
        left_out["the account's own"] += sum(r.handle == context.handle for r in reels)
        reels = [r for r in reels if r.handle != context.handle]
    left_out["hidden account"] = sum(r.handle in context.hidden for r in reels)
    reels = [r for r in reels if r.handle not in context.hidden]
    left_out["not a Short"] = sum(r.long for r in reels)
    reels = [r for r in reels if not r.long]
    left_out["low quality"] = sum(r.quality == "low" for r in reels)
    reels = [r for r in reels if r.quality != "low"]
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
    suggest(fitting, context)
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
