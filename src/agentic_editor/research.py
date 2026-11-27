"""The researcher: Instagram reels, YouTube Shorts and whole songs' YouTube videos for what a
person describes, ranked by how well they fit it and how far they have spread.

Instagram has no open search, so it is searched the way a chat assistant does: web search
engines with "site:instagram.com/reel ..." (free, no key: see websearch). The planning
model turns the description into searches, each reel found is looked up with yt-dlp
without signing in (account, caption, likes, comments, date; views and length aren't
given then), and the planning model rates how well each fits.

Engines index only part of Instagram, mostly popular reels a few days old or more, so a
research finds plenty, never everything. YouTube is searched the same way
("site:youtube.com/shorts ..."), for Shorts only: a link to a video, or one YouTube says is
longer than a Short, is left out.

Whole songs ("songs") are found with YouTube's own search instead (yt-dlp, no key), which
finds far more than the engines index: only a song's own video (not a Short, a jukebox, a
lyric or audio-only copy) on the singer's own channel, their VEVO or an official music
label's (T-Series, Sony Music...), never a fan's or a reupload. A search's results already
say each one's channel, its badge and length, so only those that may be a song are looked
up; a verified channel that is neither known nor named after the singer is the planning
model's to judge as it rates. The edit then cuts a song to its preset's length.

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
import unicodedata
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise, zip_longest
from typing import Literal, NamedTuple

from pydantic import BaseModel, Field

from agentic_editor import songlist
from agentic_editor.download import signed_in
from agentic_editor.llm import LLMError, parse
from agentic_editor.presets import Preset

log = logging.getLogger(__name__)

# A reel's link, also one under an account ("instagram.com/<name>/reel/<code>").
REEL = re.compile(r"https?://(?:www\.|m\.)?instagram\.com/(?:[\w.]+/)?(?:reels?|p)/([\w-]{5,})")
# A YouTube video's link: a Short ("/shorts/"), or a watch link (a whole song, or a Short an
# edit was given that way).
VIDEO = re.compile(
    r"https?://(?:www\.|m\.)?(?:youtube\.com/(?:shorts/|watch\?v=)|youtu\.be/)([\w-]{11})"
)
YOUTUBE = "yt:"  # a YouTube video's code is this and its id: Instagram codes have no colon
# A post on X (Twitter): its link, and its code ("x:" and the post's id).
POST = re.compile(
    r"https?://(?:www\.|mobile\.)?(?:x|twitter)\.com/(?:\w{1,15}|i(?:/web)?)/status/(\d{5,25})"
)
X = "x:"
# Where a research searches, as the engines are asked. On X only posts with a video are
# kept (see look_up): "video" in the search brings more of them.
SITES = {
    "instagram": "site:instagram.com/reel",
    "shorts": "site:youtube.com/shorts",
    "x": "site:x.com video",
    "songs": "ytsearch",  # YouTube's own search, not the engines (see _youtube_songs)
}
SONGS = "songs"
SITE = SITES["instagram"]
# What a daily research searches: X only when the person asks for it; whole songs only
# when what it finds is music (see Planned.music).
DAILY_SITES = ("instagram", "shorts", "songs")
# The owner: Instagram reels at most a tenth of what a research shows, the rest YouTube
# (Shorts, and whole songs for music). They take at most this share of what is looked
# up too, so YouTube's results get looked up and rated.
INSTAGRAM_SHOWN = 0.1
INSTAGRAM_LOOKED_UP = 0.2
MAX_SHORT = 180  # seconds: YouTube's longest Short; longer is a video, left out
# A whole song's video, in seconds: shorter is a clip or a teaser, longer a jukebox or a
# concert.
MIN_SONG, MAX_SONG = 90, 600
SONG_RESULTS = 40  # YouTube's first two pages of results, for each search
SONGS_SHOWN = 30  # with whole songs, the best this many are kept: the owner wants many
SONGS_REELS = 120  # and this many looked up and rated
SONGS_PER_CHANNEL = 5  # of them at most from one channel: a label posts many singers' songs
SONGS_PER_ARTIST = 4  # and of one singer: twice as many shown, twice PER_ARTIST
# The singers' own channels a songs search turned up, the most found first: their latest
# videos are taken too, as many songs as a search finds of them among the fans' copies.
OWN_CHANNELS = 3
OWN_SONGS = 30  # videos listed of each
# A title that says it's the song's own video: what a verified channel nobody knows needs,
# besides the model's word, when it isn't named after the singer.
OFFICIAL = re.compile(
    r"\b(?:official|ufficiale|oficial|officiel|offiziell|mv)\b",
    re.IGNORECASE,
)
# Not a song's own video, by its title: a compilation, a lyric, audio-only or altered copy
# (a remix, lo-fi), someone else's cover or tribute, a teaser. ("bts" is a band, so not
# "behind the scenes" by its letters.)
NOT_A_SONG = re.compile(
    r"\b(?:jukebox|lyrics?|lyrical|audio|visuali[sz]er|8d|slowed|reverb|sped up|nightcore|"
    r"karaoke|instrumental|teaser|trailer|making of|behind the scenes|reaction|full album|"
    r"non ?stop|playlist|compilation|back to back|mashup|medley|lo-?fi|remix|cover|tribute|"
    r"sing-?along)\b",
    re.IGNORECASE,
)
# Official music labels' channels, as compact lowercase names start: a singer's songs are
# often on their label's channel, not their own. Any other verified channel the model judges.
LABELS = (
    "tseries", "sonymusic", "zeemusic", "saregama", "tipsofficial", "tipsmusic",
    "yrf", "speedrecords", "desimelodies", "whitehillmusic", "timesmusic", "venusmovies",
    "universalmusic", "warnermusic", "emimusic", "atlanticrecords", "columbiarecords",
    "interscope", "republicrecords", "capitolrecords", "islandrecords", "rcarecords",
    "defjam", "epicrecords", "parlophone", "elektrarecords", "bigmachine", "ultramusic",
    "spinninrecords", "adityamusic", "laharimusic", "thinkmusic", "muzik247",
    "panoramamusic", "jjustmusic", "humblemusic", "geetmp3", "rhino",
)  # fmt: skip
# What a search for reels has that YouTube's search would take literally, or that finds
# copies rather than the song's own video (a lyric, status or slowed edit).
NOT_FOR_YOUTUBE = re.compile(
    r"\b(?:reels?|shorts|instagram|insta|tiktok|viral|status|whatsapp|lyrics?|lyrical|lo-?fi|"
    r"slowed|reverb|8d|aesthetic|edits?)\b",
    re.IGNORECASE,
)
# Picture quality, by the short side of the best version the site offers: below SD is left
# out (it looks soft once edited at 1080 wide), SD ranks a little lower than HD.
SD, HD = 480, 720
# A YouTube channel's link, of any of its tabs.
CHANNEL = re.compile(
    r"https?://(?:www\.|m\.)?youtube\.com/(@[\w.-]+|channel/[\w-]+)(?:/\w*)?/?(?:\?.*)?$"
)
PDF_ENTRIES = 40  # songs one PDF may list
PER_ENTRY = 4  # reels given for each of them
PDF_TEXT = 15_000  # characters of a PDF the model reads
# The words a PDF labels a song's lines with ("Top text:", "Sotto:"), in the languages
# editors write: which says above the video and which below.
ABOVE = {"top", "above", "upper", "sopra", "alto", "arriba", "encima", "oben", "haut", "upar",
         "ऊपर"}  # fmt: skip
BELOW = {"bottom", "below", "lower", "sotto", "basso", "abajo", "debajo", "unten", "bas",
         "niche", "नीचे"}  # fmt: skip
LABEL = re.compile(r"^\W*([^:]{1,30}):")
MAX_SOURCES = 10  # source pages an account may have
TREND_DAYS = 14  # the trend radar looks at reels posted, and researches made, this recently
TRENDING = 10  # reels it shows
PER_SOURCE = 5  # the latest reels taken from each, at least
QUALITY = {"HD": 1.0, "SD": 0.85, "": 0.95}  # "" when the site didn't say
MAX_QUERIES = 16
PER_QUERY = 30  # results asked of the engines for each search
MAX_REELS = 50  # reels looked up and rated, in the order the searches found them
SHOWN = 15  # the best this many are kept: the owner picks the ones to edit from them
# With source pages, this share of the reels rated and of those shown is theirs, the rest
# the searches' (owner: "40 reference, 60 online search"). A side with too few that fit
# leaves its places to the other.
SOURCE_SHARE = 0.4
PDF_REELS = 10  # reels looked up for one song of a PDF in each search round
PER_ACCOUNT = 2  # of them at most from one account, so one page doesn't fill the research
PER_SONG = 2  # and from one song: the owner's "a song may come twice", not seven times
PER_ARTIST = 2  # and of one singer or band: liked reels mustn't make it all one artist
LEARN_REELS = 24  # reels looked up to find an account's own
PAUSE = 1.0  # seconds between searches, so the engines don't turn it away
LOOKUPS = 4  # reels looked up at once
RATE_BATCH = 10
# Reels the model rates below this fit are left out, not shown at the bottom: a search
# also finds what only shares a word with the request.
MIN_FIT = 0.5
# When the person typed what they want, "related" (5/10) isn't it: a ranking, a compilation
# or a studio take when they asked for live performances.
ASKED_FIT = 0.6
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
    full: bool = False  # a whole song's YouTube video (site "songs"), not a Short
    # A whole song's channel, as its name, badge and title tell: "own" (the singer's, or
    # their VEVO), "label", "source" (a page the owner chose); "" when the model judges.
    channel_kind: str = ""
    usable: bool | None = None  # the model's word on a whole song's video (see RATE)
    channel_url: str = ""  # a whole song's channel, from the listing
    unfit: str = ""  # why a whole song's video can't be used ("not a song's video"...)
    no_video: bool = False  # a post on X without a video (or one yt-dlp can't read): left out
    page: str = ""  # the Instagram source page it was searched for: kept only when its own
    source: str = ""  # the source page it came from ("instagram:name"), "" when a search's
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
    def youtube(self) -> bool:
        return self.code.startswith(YOUTUBE)

    @property
    def short(self) -> bool:
        return self.youtube and not self.full

    @property
    def on_x(self) -> bool:
        return self.code.startswith(X)

    @property
    def on_instagram(self) -> bool:
        return not self.youtube and not self.on_x

    @property
    def kind(self) -> str:
        if self.full:
            return "YouTube song (whole video)"
        return "YouTube Short" if self.short else "X post" if self.on_x else "Instagram reel"

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
    # "didn't fit", "hidden account", "not a Short", "low quality", "same account", and
    # for whole songs "not a song's video", "not the artist's or a label's channel".
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
    # For an account its channel autopilot runs (channel/pilot.py): its best liked posts, a
    # line each with their likes; the songs it posted or has waiting, (song, artist); and
    # its posts' captions. A song posted before is never given again (see Posted).
    best: list[str] = field(default_factory=list)
    avoid: list[tuple[str, str]] = field(default_factory=list)
    posted: list[str] = field(default_factory=list)
    # The account wants surprising picks over each artist's most famous hits (SURPRISE).
    surprise: bool = False
    # The strike list's songs, (song, artist): a label blocked them, so none is given.
    strikes: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class SongTrend:
    """A song several accounts posted reels of lately, as the trend radar shows it."""

    song: str
    reels: int
    accounts: int
    per_day: float  # the reels' likes and comments a day, together


@dataclass
class Learned:
    """What a look at an account found: what it posts, its own reels, the best liked."""

    summary: str
    own: list[str]
    best: list[dict]
    # Each of its own reels found, with likes, comments and when it was posted: kept as a
    # snapshot of how the page does (see jobs.learn_account).
    stats: list[dict] = field(default_factory=list)


class Searches(BaseModel):
    brief: str = Field(max_length=400)  # required: left optional, the model skipped it
    # Whether what they want is music (songs, singers, bands, performances): whole songs'
    # YouTube videos are searched only then.
    music: bool
    queries: list[str] = Field(min_length=1, max_length=MAX_QUERIES * 2)


class Planned(NamedTuple):
    """A research's plan: the request in one English sentence, its searches, and whether
    it is about music (None when the model couldn't say)."""

    brief: str
    queries: list[str]
    music: bool | None = None


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
    usable: bool  # a whole song's video is one to edit, on the singer's or a label's channel


class Ratings(BaseModel):
    ratings: list[Rating]


class Summary(BaseModel):
    summary: str = Field(max_length=1500)


class Question(BaseModel):
    question: str = Field(max_length=200)
    choices: list[str] = Field(min_length=2, max_length=5)


class Questions(BaseModel):
    questions: list[Question] = Field(min_length=1, max_length=8)


class PdfEntry(BaseModel):
    # All required, as for ratings: the model leaves out what it may.
    singer: str = Field(max_length=120)
    song: str = Field(max_length=160)
    top: str = Field(max_length=400)
    bottom: str = Field(max_length=400)
    year: str = Field(max_length=4)


class PdfEntries(BaseModel):
    entries: list[PdfEntry] = Field(min_length=1, max_length=PDF_ENTRIES)


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
    "content is in that language. Vary them so they find different reels: spread them over "
    "many artists and songs, at most two searches naming the same artist; don't add "
    "'instagram' or 'reel'."
)
PLAN = (
    "You find Instagram reels for a video editor who reposts and edits them. Their request "
    "may be in Hindi, Hinglish or any language ('gaane' means songs, 'dundh' find, 'bdia' "
    "good). First write `brief`: in one English sentence, exactly what content they want "
    "(the music, artists or topic, its language or country, live or studio, any era). "
    "Then `music`: true when what they want is music (songs, singers, bands, "
    "performances), false for anything else (military, sports, history, comedy...). "
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
    "Hinglish, comes first. Then `music`: true when what to find is music (songs, "
    "singers, bands, performances), false for anything else (military, sports, history, "
    "comedy...). " + SEARCHES
)
RATE = (
    "Rate how well each reel (an Instagram reel, a YouTube Short or a YouTube song's whole "
    "video) fits what the editor "
    "wants, from its account, caption "
    "and search snippet. Judge only the content asked for, not popularity: music of another "
    "language or country than the one asked for is 0, and so is a reel that only shares a "
    "word or a place name with the request. For anything that isn't music (combat footage, "
    "sports, fails, animals...) the caption's language doesn't matter, as the editor writes "
    "their own words over it: judge what the video shows. Judge its kind too: when the "
    "account posts real footage, a narrated slideshow of still pictures, AI-made pictures or "
    "video, a text-to-speech explainer, a ranking ('top 5'), a quiz, a news presenter "
    "talking or a staged POV skit is at most 3, unless that kind is what they asked for or "
    "what the account posts; and when it posts singers' own performances, so is a lyric "
    "video and a cover or play-along by someone else. For each reel give:\n"
    "- n: its number\n"
    "- relevance: 10 = exactly it, 5 = related, 0 = unrelated\n"
    "- why: one sentence (under 25 words) on why it fits or doesn't\n"
    "- song: the song's title whenever the caption or search snippet lets you tell it (if "
    "your why names it, so does this), else ''\n"
    "- artist: the main singer or band it is about, whenever you can tell, else ''\n"
    "- matches: a few words on which part of what they want it matches, else ''\n"
    "- preset: the name of the preset it suits best, exactly as listed, else ''\n"
    "- preset_why: a few words on why that preset\n"
    "- usable: for a YouTube song, true only when it is one song's own video that a reel "
    "can be edited from (its official music video, or the singer performing it) and its "
    "account (the YouTube channel) is the singer's or band's own (or their VEVO) or an "
    "official music label's (T-Series, Sony Music, Universal Music, Atlantic...); false for "
    "a stream, a podcast or interview, a compilation or medley of many songs, a lyric or "
    "still-picture video, a cover by someone else, and a fan's, reupload, radio or TV "
    "show's channel; for any other reel, true\n"
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
READ_PDF = (
    "This is the text of a PDF a video editor wrote: a list of songs to make Instagram "
    "reels of, each with the words to write above the video and below it. For each song "
    "give singer, song, top (the words for above), bottom (the words for below) and year "
    "(the year written beside the song's name, four digits), copied exactly as written, in "
    "their language, with \\n where a new line starts; '' for any "
    "the PDF doesn't give. Don't translate, shorten or add anything. Leave out headings "
    "and anything that isn't a song.\n\nThe PDF:\n{text}"
)
LEARN = (
    "These are reels the Instagram account @{handle} posted, with their likes. In a few "
    "sentences say what the account posts: the artists, songs, eras, genres and languages, "
    "the format (for example isolated vocals, live concerts, the story behind a song), the "
    "words and hashtags its captions use, and which kind of its reels got the most likes."
    "\n\nReels:\n{reels}"
)


def code_of(link: str) -> str | None:
    """An Instagram reel's code, "yt:" and a YouTube video's id, or "x:" and an X post's."""
    if match := REEL.match(link.strip()):
        return match.group(1)
    if match := VIDEO.match(link.strip()):
        return YOUTUBE + match.group(1)
    if match := POST.match(link.strip()):
        return X + match.group(1)
    return None


def kind_of(code: str, url: str = "") -> str:
    """What a reel is, by its code and link: a YouTube video that isn't a Short's link is
    a whole song (see song_url)."""
    full = code.startswith(YOUTUBE) and bool(url) and "/shorts/" not in url
    return Reel(code=code, url=url, query="", full=full).kind


def song_url(code: str) -> str:
    """A whole song's video's link (a YouTube code's), where reel_url gives a Short's."""
    return f"https://www.youtube.com/watch?v={code.removeprefix(YOUTUBE)}"


NOT_SONG = "not a song's video"
# YouTube's own track of a song (a still picture with the sound, "Provided to YouTube by
# <distributor>"): nothing to edit.
ART_TRACK = re.compile(r"^\s*provided to youtube by\b", re.IGNORECASE | re.MULTILINE)
STILL = "a still picture with the song"
# Made with AI rather than filmed (a person asking for AI pictures says so: see not_wanted).
MADE_WITH_AI = re.compile(
    r"\bai[ -]?(?:shorts?|generated|made|art|video|cover|version|animation)\b|#ai\b|"
    r"\b(?:made|created) (?:with|by|using) ai\b",
    re.IGNORECASE,
)
ASKS_FOR_AI = re.compile(r"\b(?:ai|artificial intelligence)\b", re.IGNORECASE)
AI_MADE = "made with AI"
# In a music research, a Short or reel that isn't a singer singing: a lyric edit, a ranking
# or quiz, a compilation, someone's cover, an altered copy.
NOT_A_PERFORMANCE = re.compile(
    r"\b(?:lyrics?|lyrical|karaoke|top \d+|compilation|playlist|medley|mashup|nightcore|"
    r"slowed|sped up|8d|covers?|tribute|quiz|which .{1,40} would you|best .{1,30} songs)\b",
    re.IGNORECASE,
)
NOT_PERFORMED = "not the singer performing"
NOT_USABLE = "not a song video to edit"  # the model's word (see judge_songs)
LIVE = ("is_live", "is_upcoming", "was_live", "post_live")  # streams, on or past
OFF_CHANNEL = "not the artist's or a label's channel"


def _compact(text: str) -> str:
    return re.sub(r"[\W_]+", "", text.casefold())


def _bare(channel: str) -> str:
    """A compact channel name without the words channels add to a singer's name."""
    return re.sub(r"(?:official|music|vevo|tv|channel)+$", "", channel)


def channel_kind(channel: str, verified: bool | None, title: str) -> str | None:
    """Whose channel a whole song's video is on, as far as its name, badge and title tell:
    "own" (a VEVO, or the singer's: titles say "Singer - Song"), "label" (an official
    label's), "" (verified, the model judges), None (not one to take: a "- Topic" channel
    shows a still picture, and anyone may name a channel after a singer without YouTube's
    badge)."""
    name = _compact(channel)
    if not name or channel.casefold().rstrip().endswith("- topic"):
        return None
    if name.endswith("vevo"):
        return "own"
    if not verified:
        return None
    if name.startswith(LABELS):
        return "label"
    singer = re.split(r"\s[-–—]\s", title, maxsplit=1)
    bare = _bare(name)
    if len(singer) == 2 and len(bare) >= 3 and bare in _compact(singer[0]):
        return "own"
    return ""


def not_a_song(link: str, title: str, duration: float | None, media_type=None, live=None) -> str:
    """Why a YouTube video isn't a whole song's own (NOT_SONG), "" when it may be: a Short,
    a live stream, too short or too long (see MIN_SONG), or a title that says it's a
    compilation, a lyric or audio-only copy, a teaser (see NOT_A_SONG)."""
    if "/shorts/" in link or media_type == "short" or live in LIVE:
        return NOT_SONG
    if duration is not None and not MIN_SONG <= duration <= MAX_SONG:
        return NOT_SONG
    return NOT_SONG if NOT_A_SONG.search(title) else ""


def not_wanted(reel: Reel, music: bool, wants_ai: bool = False) -> str:
    """Why a looked-up reel is left out before the model rates it, "" when it may be
    wanted: YouTube's still-picture track of a song (STILL), one made with AI unless the
    person asked for AI (AI_MADE), and in a music research a Short or reel that isn't the
    singer performing (a lyric edit, a ranking, a cover: NOT_PERFORMED). The model let
    these through as fitting; their words say what they are."""
    text = f"{reel.title}\n{reel.caption}"
    if reel.youtube and (ART_TRACK.search(reel.caption) or ART_TRACK.search(reel.snippet)):
        return STILL
    if not wants_ai and MADE_WITH_AI.search(text):
        return AI_MADE
    if music and not reel.full and NOT_A_PERFORMANCE.search(text):
        return NOT_PERFORMED
    return ""


def instagram_share(picked: list[Reel], share: float = 0.1) -> tuple[list[Reel], int]:
    """`picked` with Instagram's reels at most `share` of the list as it ends up (at least
    one may stay): with fewer found than asked, a fixed number of them would be more than
    their share. The lowest-ranked go; how many did."""
    if share >= 1:
        return picked, 0
    others = sum(not r.on_instagram for r in picked)
    room = max(1, int(others * share / (1 - share) + 1e-9))
    kept, given, dropped = [], 0, 0
    for reel in picked:
        if reel.on_instagram:
            if given >= room:
                dropped += 1
                continue
            given += 1
        kept.append(reel)
    return kept, dropped


def _song(entry: dict, query: str, source: str = "", kind: str | None = None) -> Reel | None:
    """A whole song's video from a YouTube listing (a search's, a channel's), with what the
    listing says: its channel, views, length, and why it can't be used, if it can't. A
    verified channel named in the search ("Atif Aslam ..." finding "Atif Aslam") is the
    singer's own; `kind` says whose a channel's own listing is."""
    link = entry.get("url") or ""
    code = code_of(link) or (YOUTUBE + entry["id"] if entry.get("id") else None)
    if not code or not code.startswith(YOUTUBE):
        return None
    title = entry.get("title") or ""
    reel = Reel(code, song_url(code), query, title=title[:200], full=True)
    reel.snippet = (entry.get("description") or "")[:500]
    reel.account = entry.get("channel") or entry.get("uploader") or ""
    channel_id = entry.get("channel_id")
    reel.channel_url = entry.get("channel_url") or (
        f"https://www.youtube.com/channel/{channel_id}" if channel_id else ""
    )
    reel.views = _count(entry.get("view_count"))
    if isinstance(entry.get("duration"), int | float):
        reel.duration = float(entry["duration"])
    reel.unfit = not_a_song(link, title, reel.duration, live=entry.get("live_status"))
    if not reel.unfit:
        if source:
            kind = "source"
        elif kind is None:
            kind = channel_kind(reel.account, entry.get("channel_is_verified"), title)
            bare = _bare(_compact(reel.account))
            if kind == "" and len(bare) >= 3 and bare in _compact(query):
                kind = "own"
        reel.channel_kind, reel.unfit = (kind, "") if kind is not None else ("", OFF_CHANNEL)
    return reel


def own_channels_songs(reels: list[Reel], channel, count: int = OWN_CHANNELS) -> list[Reel]:
    """The latest videos (OWN_SONGS) of the singers' own channels among `reels`, the one
    found most first, as whole songs of theirs: a search finds a few of a singer's songs
    among many fans' copies, their channel lists them all."""
    found = Counter(
        (r.channel_url, r.account)
        for r in reels
        if r.full and not r.unfit and r.channel_kind == "own" and r.channel_url
    )
    songs: list[Reel] = []
    for (url, name), _ in found.most_common(count):
        try:
            entries = channel(f"{url.rstrip('/')}/videos", OWN_SONGS)
        except Exception as exc:
            log.warning("couldn't list %s's videos: %s", name, exc)
            continue
        for entry in entries:
            listed = {**entry, "channel": name, "channel_url": url}
            reel = _song(listed, f"{name}'s own channel", kind="own")
            if reel and not reel.unfit:
                songs.append(reel)
    return songs


def judge_songs(reels: list[Reel]) -> None:
    """After rating, the model's look at every whole song: one it says isn't a song's own
    video to edit (a stream, an interview, a medley, a fan's upload...), or didn't judge,
    is left out. Of the rest, one on a verified channel that is neither a VEVO, a known
    label's nor the title's singer's is the singer's own when it's named after the singer
    the model names; else it's kept ("judged") only when its title also says it's the
    official video or names the channel: the model alone takes a verified reupload too."""
    for r in reels:
        if not r.full or r.unfit:
            continue
        if not r.usable:
            r.unfit = NOT_USABLE
        elif not r.channel_kind:
            name, artist = _compact(r.account), _compact(r.artist)
            bare = _bare(name)
            own = artist and name and (artist in name or (len(bare) >= 3 and bare in artist))
            named = len(bare) >= 3 and bare in _compact(r.title or r.caption)
            if own:
                r.channel_kind = "own"
            elif named or OFFICIAL.search(r.title or r.caption):
                r.channel_kind = "judged"
            else:
                r.unfit = OFF_CHANNEL


def reel_url(code: str) -> str:
    if code.startswith(YOUTUBE):
        return f"https://www.youtube.com/shorts/{code.removeprefix(YOUTUBE)}"
    if code.startswith(X):
        return f"https://x.com/i/status/{code.removeprefix(X)}"
    return f"https://www.instagram.com/reel/{code}/"


def handle_of(text: str) -> str | None:
    """An Instagram account's name from "@name", "name" or its profile link."""
    text = text.strip()
    if match := re.match(r"https?://(?:www\.|m\.)?instagram\.com/([\w.]+)/?(?:\?.*)?$", text):
        text = match.group(1)
    text = text.removeprefix("@").lower()
    return text if re.fullmatch(r"[a-z0-9._]{1,30}", text) else None


def read_pdf(data: bytes) -> str:
    """The text of a PDF's pages; '' for one that is only pictures (a scan)."""
    import io

    from pypdf import PdfReader

    pages = PdfReader(io.BytesIO(data)).pages
    # Pages kept apart, so a numbered list's running headers can be told from its entries.
    return f"\n{songlist.PAGE_BREAK}\n".join(
        (page.extract_text() or "").strip() for page in pages
    ).strip()


def listed(text: str) -> list[songlist.Listed]:
    """A PDF's numbered song list, read by rule (see songlist.py); [] when it isn't one."""
    return songlist.read(text, {"top": ABOVE, "bottom": BELOW})


def _year(said: str, text: str) -> str:
    """The model's year for a song, kept only if four digits the PDF itself has."""
    said = said.strip()
    return said if re.fullmatch(r"(?:1[89]|20)\d\d", said) and said in text else ""


def pdf_entries(llm, text: str) -> list[dict]:
    """The songs a PDF lists, each {"singer", "song", "top", "bottom", "year"}, in its
    order."""
    prompt = READ_PDF.format(text=text.replace(songlist.PAGE_BREAK, "")[:PDF_TEXT])
    reply = parse(
        PdfEntries,
        llm.chat_json(
            [{"role": "user", "content": prompt}], PdfEntries.model_json_schema(), temperature=0.0
        ),
    )
    entries = []
    for e in reply.entries:
        if e.song.strip() or e.singer.strip():
            entries.append(
                {
                    "singer": e.singer.strip(),
                    "song": e.song.strip(),
                    "top": e.top.strip().replace("\\n", "\n"),
                    "bottom": e.bottom.strip().replace("\\n", "\n"),
                    "year": _year(e.year, text),
                }
            )
    return [placed(entry, text) for entry in entries]


def _label(line: str) -> str | None:
    """ "top" or "bottom" for a line the PDF labels as above or below the video."""
    if match := LABEL.match(line):
        words = set(re.findall(r"\w+", match.group(1).lower()))
        if words & ABOVE:
            return "top"
        if words & BELOW:
            return "bottom"
    return None


def placed(entry: dict, text: str) -> dict:
    """The entry with each line of its words above or below the video as the PDF labels
    it: the model often runs a "Bottom text:" line into the top. A line found under no
    label goes with the labelled one before it, and one the PDF doesn't have stays put."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    song = entry["song"].lower()
    start = next((n for n, line in enumerate(lines) if song and song in line.lower()), 0)
    where: dict[str, list[str]] = {"top": [], "bottom": []}
    current = None
    for place in ("top", "bottom"):
        for part in filter(None, (p.strip() for p in entry[place].split("\n"))):
            line = next((line for line in lines[start:] if part in line), None)
            if line is not None:
                current = _label(line) or current
            where[current if line is not None and current else place].append(part)
    return {**entry, "top": "\n".join(where["top"]), "bottom": "\n".join(where["bottom"])}


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


def _taste(context: Context, rating: bool = False) -> str:
    """What the person's feedback taught, for planning (or `rating`); '' without any.
    Liked reels show what works for them, not all they want: the owner asked for variety,
    so the searches keep a share for something else that fits, and the rating doesn't mark
    a fitting reel down for being unlike them."""
    parts = []
    if context.liked:
        guide = (
            "Reels they liked before, which show their taste. They also want variety: a "
            "reel that fits what they want but is unlike these (another artist, song, era "
            "or style) fits just as well; don't rate it lower for that"
            if rating
            else "Reels they liked before, which show their taste. They also want variety: "
            "make about two thirds of the searches find reels like these, and the rest "
            "deliberately different ones that still fit what they want (other artists, "
            "songs, eras or styles than these)"
        )
        parts.append(f"{guide}:\n" + "\n".join(f"- {line}" for line in context.liked))
    if context.disliked:
        parts.append(
            "Reels they didn't want, with why when they said (find fewer like these):\n"
            + "\n".join(f"- {line}" for line in context.disliked)
        )
    if context.best:
        parts.append(
            "The account's best liked posts, which show what works on it. It never posts a "
            "song twice: find other songs like these, never these songs"
            + (":\n" if rating else " (so don't search for them):\n")
            + "\n".join(f"- {line}" for line in context.best)
        )
    if context.surprise:
        parts.append(SURPRISE_RATE if rating else SURPRISE_PLAN)
    if not rating and (named := Posted(context.avoid, context.posted).names(AVOID_LISTED)):
        parts.append(
            "Songs the account already posted or has waiting, never to be posted again: "
            "don't search for these, find other songs.\n" + "; ".join(named)
        )
    return "\n\n".join(parts)


# For an account whose best posts were surprising picks (the owner's channel data): its
# searches and ratings go past each artist's signature hit.
SURPRISE_PLAN = (
    "This account does best with SURPRISING picks: recognisable songs with a striking voice "
    "that people don't expect, not each artist's single most famous, most-streamed hit (the "
    "song everyone already knows them for). For each artist you search, name another of their "
    "recognisable songs: an album track, a forgotten hit, a crossover, a famous live take or a "
    "duet; at most a quarter of the searches may name an artist's signature hit. Spread the "
    "searches over the 1960s to the 2010s, not mostly recent hits."
)
SURPRISE_RATE = (
    "Rate surprise into the fit. An artist's single most famous, most-streamed hit (the song "
    "everyone knows them for) or a hit of the last few years everyone has heard is at most 6. A "
    "recognisable song with a striking voice that people don't expect (an album track, a "
    "forgotten hit, a crossover, a famous live take) is 8-10. Use the whole scale."
)
AVOID_LISTED = 60  # songs posted before the planning model is told of, at most
COMMON = {"band", "with", "from", "orchestra", "group", "music", "official"}
NAMED_LINE = 80  # characters of a caption's first line that names a song, at most
SEPARATED = re.compile(r"\s[-–—]\s|\sby\s", re.IGNORECASE)
# What makes a group in a song's name a note about the take, not its name (shazam.NOTE).
TAKE = re.compile(r"\s*[(\[{][^)\]}]*[)\]}]|\s+[-–—]\s+.*$|\s+(?:feat\.?|ft\.?|featuring)\s.*$",
                  re.IGNORECASE)  # fmt: skip
ARTISTS = re.compile(r"\s*(?:,|&|\+|/|\band\b|\bx\b|\bfeat\.?|\bft\.?|\bfeaturing\b|\bwith\b)\s*",
                     re.IGNORECASE)  # fmt: skip


def plain(text: str) -> str:
    """A name as compared: no accents, case or punctuation ("Céline Dion" is "celine dion",
    "Lovin’ You" "lovin you"); "&" is "and"."""
    decomposed = unicodedata.normalize("NFKD", text.casefold().replace("&", " and "))
    bare = "".join(c for c in decomposed if not unicodedata.combining(c))
    return " ".join(re.sub(r"[\W_]+", " ", bare).split())


def title_key(song: str) -> str:
    """A song's title as compared, without what says which take it is ("(Live)", "-
    Remastered 2011", "feat. ...") or a bracket ("Gimme! Gimme! Gimme! (A Man After
    Midnight)" is "gimme gimme gimme")."""
    return plain(TAKE.sub("", song)) or plain(song)


def artist_keys(artist: str) -> list[str]:
    """Each singer or band named in `artist`, as compared, without a leading "the"."""
    names = (plain(a) for a in ARTISTS.split(artist))
    return [re.sub(r"^the ", "", n) for n in names if len(n) >= 2]


def song_title(reel: Reel) -> str:
    """The song's title alone: the rating names a reel's song "Title - Artist"."""
    tail = f" - {reel.artist}"
    return reel.song.removesuffix(tail) if reel.artist and reel.song.endswith(tail) else reel.song


class Posted:
    """The songs an account posted or has waiting: (song, artist) pairs, and its posts'
    captions, which name them in any of the ways the account wrote them ("🎵 Song — Artist -
    1984", "Artist - Song", a story). A song is posted before when its title is one of
    theirs with an artist in common (or one side doesn't say), or when a caption names its
    title and its artist, or its title of three words or more on its first line."""

    def __init__(self, pairs: list[tuple[str, str]], captions: list[str]):
        self.pairs = [(title_key(s), artist_keys(a), s, a) for s, a in pairs if title_key(s)]
        self.captions = [
            (f" {plain(c)} ", f" {plain(c.strip().split(chr(10), 1)[0])} ", c)
            for c in captions
            if c.strip()
        ]

    def has(self, song: str, artist: str) -> bool:
        title, artists = title_key(song), artist_keys(artist)
        if not title:
            return False
        for known, theirs, _, _ in self.pairs:
            if known != title:
                continue
            if not theirs or not artists:
                return True
            if any(a in b or b in a for a in artists for b in theirs):
                return True
        named = f" {title} "
        # A title this long names the song by itself; a shorter one may be another song's
        # artist ("Daft Punk" on "🎵 Get Lucky — Daft Punk - 2013").
        long_title = len(title.split()) >= 3
        # A word of the artist's name long enough to be theirs ("jackson" of "M. Jackson").
        names = [f" {a} " for a in artists] + [
            f" {w} " for a in artists for w in a.split() if len(w) >= 4 and w not in COMMON
        ]
        for text, first, _ in self.captions:
            if named not in text:
                continue
            if any(n in text for n in names):
                return True
            if long_title and named in first:
                return True
        return False

    def add(self, song: str, artist: str) -> None:
        if title_key(song):
            self.pairs.append((title_key(song), artist_keys(artist), song, artist))

    def names(self, limit: int) -> list[str]:
        """The songs, each once, as the model is told them: the pairs, then the captions'
        first lines that name a song and its artist ("Toto - Africa", "🎵 Africa — Toto -
        1982"; a story's first sentence says too little for its length)."""
        found: dict[str, str] = {}
        for _, _, song, artist in self.pairs:
            found.setdefault(title_key(song), f"{song} - {artist}" if artist else song)
        for _, _, caption in self.captions:
            line = " ".join(
                re.sub(r"[🎵🎶🎤_]|\ben\b", " ", caption.strip().split("\n", 1)[0]).split()
            )
            if len(line) <= NAMED_LINE and SEPARATED.search(line):
                found.setdefault(plain(re.sub(r"\s*[-–—]\s*\d{4}\s*$", "", line)), line)
        return [n for n in found.values() if n][:limit]


def plan(llm, description: str, context: Context | None = None) -> Planned:
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
    brief, queries, music = "", [], None
    messages = [{"role": "user", "content": prompt}]
    for _ in range(2):
        try:
            reply = parse(Searches, llm.chat_json(messages, Searches.model_json_schema()))
        except LLMError as exc:
            log.warning("couldn't plan searches: %s", exc)
            continue
        brief = brief or reply.brief.strip()
        music = reply.music if music is None else music
        queries += reply.queries
        if len(_tidy(queries)) >= MIN_QUERIES:
            break
    return Planned(brief, (_tidy(queries) or _tidy(fallback))[:MAX_QUERIES], music)


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
            f"#{item.rank + 1} {kind_of(item.code, item.url).removeprefix('Instagram ')}"
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
    """Web results for a query (see websearch): empty when nothing was found, raises when
    the engines failed."""
    from agentic_editor import websearch

    return websearch.search(query, count)


def _youtube_songs(query: str, count: int) -> list[dict]:
    """YouTube's own search results for a query, as yt-dlp lists them without opening each
    one: link, title, channel and its badge, length, views."""
    from yt_dlp import YoutubeDL

    options = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
    options = {**options, "socket_timeout": 15, "logger": _Quiet()}
    with signed_in(options) as options, YoutubeDL(options) as ydl:
        return ydl.extract_info(f"ytsearch{count}:{query}", download=False).get("entries") or []


def song_query(query: str) -> str:
    """A search written for reels, as YouTube's search is asked it for whole songs."""
    return " ".join(NOT_FOR_YOUTUBE.sub(" ", query).split()) or query


def search(
    queries: list[str],
    find=_engines,
    pause: float | None = None,
    progress: Progress | None = None,
    sites: tuple[str, ...] = ("instagram",),
    songs=_youtube_songs,
) -> tuple[list[Reel], list[str]]:
    """Reels the searches find on each of `sites` (see SITES), each once, taken from the
    searches in turn (each one's first, then each one's second...), so the first searches
    don't fill all that is looked up (MAX_REELS) with their artist; and the searches that
    failed (an engine turned it away, or none answered). Whole songs ("songs") are
    searched on YouTube itself (`songs`); those that can't be used, as what the search
    said shows, come marked (Reel.unfit) to be counted."""
    from agentic_editor import websearch

    pause = PAUSE if pause is None else pause
    found: dict[str, Reel] = {}
    failed = []
    asked = [(query, site) for query in queries for site in sites]

    def ask(asking: tuple[str, str]) -> list[dict]:
        query, site = asking
        if site == SONGS:
            return songs(song_query(query), SONG_RESULTS)
        return find(f"{SITES[site]} {query}", PER_QUERY)

    # A few searches at once (each pausing after its own): kept in the order asked.
    answers = websearch.each(asked, ask, pause)
    lists: list[list[Reel]] = []  # each search's reels, in its order
    for n, ((query, site), hits) in enumerate(answers):
        if progress:
            where = {"shorts": " (YouTube Shorts)", "x": " (X)", SONGS: " (YouTube songs)"}
            progress(f"searching: {query}{where.get(site, '')}", 0.05 + 0.35 * (n + 1) / len(asked))
        if isinstance(hits, Exception):  # the engines are third parties: one failing is one less
            log.warning("search %r on %s failed: %s", query, site, hits)
            failed.append(query)
            continue
        reels: list[Reel] = []
        if site == SONGS:
            for entry in hits:
                # A Short is the Shorts search's: kept out of the way of its code.
                if "/shorts/" in (entry.get("url") or ""):
                    continue
                if reel := _song(entry, query):
                    reels.append(reel)
        for hit in hits if site != SONGS else []:
            link = hit.get("href", "")
            code = code_of(link)
            if code and code.startswith(YOUTUBE) and "/shorts/" not in link:
                continue  # a video, not a Short
            if code:
                reels.append(
                    Reel(
                        code=code,
                        url=reel_url(code),
                        query=query,
                        title=hit.get("title", "")[:200],
                        snippet=hit.get("body", "")[:500],
                    )
                )
        lists.append(reels)
    for turn in range(max((len(reels) for reels in lists), default=0)):
        for reels in lists:
            if turn < len(reels) and reels[turn].code not in found:
                found[reels[turn].code] = reels[turn]
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
    with signed_in(options) as options, YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False)


def look_up(reels: list[Reel], info=_info, workers: int = LOOKUPS) -> None:
    """Fill in each reel's account, caption, likes, comments and date. One Instagram won't
    show without signing in keeps what the search engine said about it."""

    def one(reel: Reel) -> None:
        try:
            meta = info(reel.url)
        except Exception as exc:
            log.info("couldn't look up %s: %s", reel.url, str(exc)[:200])
            # An X post yt-dlp can't read has no video to edit ("No video could be found").
            reel.no_video = reel.on_x
            return
        if reel.on_x and meta.get("entries"):
            # A post with a few videos: the first is the one edited (see download.fetch).
            first = next(iter(meta["entries"]), None) or {}
            meta = {**meta, **{k: v for k, v in first.items() if v is not None}}
        if reel.youtube:
            kind, length = meta.get("media_type"), meta.get("duration") or 0
            if reel.full:
                title = meta.get("title") or reel.title
                reel.unfit = not_a_song(
                    reel.url, title, meta.get("duration"), kind, meta.get("live_status")
                )
                verified = meta.get("channel_is_verified")
                # The channel again, as the video's page says (the search's word stands
                # when the page doesn't say); a source page's is the owner's choice.
                if not reel.unfit and reel.channel_kind != "source" and verified is not None:
                    found = channel_kind(meta.get("channel") or reel.account, verified, title)
                    if found is None:
                        reel.channel_kind, reel.unfit = "", OFF_CHANNEL
                    elif not reel.channel_kind:  # never less than the search knew
                        reel.channel_kind = found
            else:
                # A Short is vertical and at most MAX_SHORT long; YouTube says which it is.
                reel.long = kind not in (None, "short") or length > MAX_SHORT
            reel.account = meta.get("channel") or meta.get("uploader") or ""
            reel.handle = (meta.get("uploader_id") or "").removeprefix("@").lower()
            title, about = meta.get("title") or "", meta.get("description") or ""
            reel.caption = "\n".join(t for t in (title, about) if t)[:1000]
            reel.views = _count(meta.get("view_count"))
        elif reel.on_x:
            reel.account = meta.get("uploader") or ""
            reel.handle = (meta.get("uploader_id") or "").removeprefix("@").lower()
            reel.caption = (meta.get("description") or meta.get("title") or "")[:1000]
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
    """A YouTube channel's latest Shorts (or videos), newest first, as the tab of `url`
    lists them."""
    from yt_dlp import YoutubeDL

    options = {"quiet": True, "no_warnings": True, "extract_flat": "in_playlist"}
    options = {**options, "playlistend": count, "logger": _Quiet()}
    with signed_in(options) as options, YoutubeDL(options) as ydl:
        return ydl.extract_info(url, download=False).get("entries") or []


def from_sources(
    sources: list[str],
    find=_engines,
    pause: float | None = None,
    channel=_channel_shorts,
    each: int = PER_SOURCE,
    songs: bool = False,
) -> list[Reel]:
    """The latest `each` reels of each source page: a YouTube channel's Shorts tab lists
    them (and with `songs` its Videos tab its whole songs); an Instagram page can't be
    listed without signing in, so it is searched for, and what the search finds is kept
    only when it is the page's own (see run)."""
    found: dict[str, Reel] = {}
    for source in sources[:MAX_SOURCES]:
        kind, name = source.split(":", 1)
        if kind == "youtube":
            try:
                entries = channel(f"https://www.youtube.com/{name}/shorts", each)
            except Exception as exc:
                log.warning("couldn't list %s's Shorts: %s", name, exc)
                continue
            for entry in entries:
                link = entry.get("url") or ""
                code = code_of(link)
                if code and "/shorts/" in link and code not in found:  # never a video
                    title = entry.get("title") or ""
                    reel = Reel(code, reel_url(code), f"from {name}", title=title)
                    reel.source = source
                    found[code] = reel
            if songs:
                try:
                    videos = channel(f"https://www.youtube.com/{name}/videos", each)
                except Exception as exc:
                    log.warning("couldn't list %s's videos: %s", name, exc)
                    videos = []
                for entry in videos:
                    reel = _song(entry, f"from {name}", source)
                    if reel and not reel.unfit and reel.code not in found:
                        reel.source = source
                        found[reel.code] = reel
        else:
            reels, _ = search([f'"{name}"'], find, pause)
            for reel in reels[: each * 2]:  # half of what a search finds is others'
                reel.query, reel.page, reel.source = f"from @{name}", name, source
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
    matches and the preset it suits best. A batch it can't rate is tried again in halves;
    what still can't be rated keeps None (see run: left out, unless nothing was rated)."""
    context = context or Context()
    wish = description.strip()
    if context.handle:
        account = f"reels like those of @{context.handle}, which posts: " + (
            context.summary or "(not known: go by the presets)"
        )
        wish = f"{wish}\nThat is, {account}" if wish else account
    if taste := _taste(context, rating=True):
        wish = f"{wish}\n\n{taste}"
    groups = [reels[start : start + batch] for start in range(0, len(reels), batch)]
    while groups:
        group = groups.pop(0)
        lines = "\n".join(
            f"{n}. {r.kind}"
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
            log.warning("couldn't rate %d reels (%s...): %s", len(group), group[0].code, exc)
            if len(group) > 1:  # a long answer broke off: half as many at a time
                half = len(group) // 2
                groups[:0] = [group[:half], group[half:]]
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
                reel.usable = rating.usable
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
    """A name as compared with another: "Faithfully - Journey (feat. Steve Perry)" and
    "faithfully – journey" are one song, "Journey (feat. Steve Perry)" one band with
    "Journey"."""
    text = re.sub(r"\s*[(\[][^)\]]*[)\]]", "", text)
    text = re.sub(r"\s+(?:feat\.?|ft\.?|featuring)\s+.*$", "", text, flags=re.IGNORECASE)
    return " ".join(re.sub(r"[\W_]+", " ", text.casefold()).split())


def trends(items: list, now: datetime | None = None) -> tuple[list, list[SongTrend]]:
    """The trend radar, from reels earlier researches gave (the database's items, newest
    research first): those posted in the last TREND_DAYS, fastest spreading first, each
    once; and the songs more than one account posted reels of then, most accounts first."""
    now = now or datetime.now(UTC)

    def fresh(item) -> bool:
        posted = item.posted_at
        if posted is None or item.per_day is None:
            return False
        posted = posted if posted.tzinfo else posted.replace(tzinfo=UTC)
        return (now - posted).days < TREND_DAYS

    once: dict[str, object] = {}
    for item in items:
        if fresh(item):
            once.setdefault(item.code, item)
    reels = sorted(once.values(), key=lambda i: i.per_day, reverse=True)
    songs: dict[str, dict] = {}
    for item in reels:
        if not (key := _same(item.song)):
            continue
        song = songs.setdefault(key, {"song": item.song, "who": set(), "reels": 0, "per_day": 0.0})
        song["who"].add(_same(item.handle or item.account) or item.code)
        song["reels"] += 1
        song["per_day"] += item.per_day
    going = [
        SongTrend(s["song"], s["reels"], len(s["who"]), round(s["per_day"], 1))
        for s in songs.values()
        if len(s["who"]) > 1
    ]
    going.sort(key=lambda t: (t.accounts, t.per_day), reverse=True)
    return reels[:TRENDING], going[:TRENDING]


def instagram_few(reels: list[Reel], limit: int) -> list[Reel]:
    """The first `limit` reels to look up, Instagram's at most INSTAGRAM_LOOKED_UP of them
    (the rest YouTube's, in the order found), so a tenth of what is shown can be
    Instagram's and the rest has YouTube's to choose from."""
    room = max(2, round(limit * INSTAGRAM_LOOKED_UP))
    kept, taken = [], 0
    for reel in reels:
        if reel.on_instagram:
            if taken >= room:
                continue
            taken += 1
        kept.append(reel)
    return kept[:limit]


def pick(
    ranked: list[Reel],
    count: int = SHOWN,
    per_account: int = PER_ACCOUNT,
    per_song: int = PER_SONG,
    per_artist: int | None = PER_ARTIST,
    asked: str = "",
    sourced: int | None = None,
    instagram: int | None = None,
) -> tuple[list[Reel], dict[str, int], float | None]:
    """The best `count` of the ranked reels, at most `per_account` from one account (by its
    Instagram name, its display name when that isn't known), `per_song` of one song and
    `per_artist` of one singer or band, so one page, one hit or one artist doesn't fill
    the research, unless the person `asked` for that artist by name ("Adele's songs": all
    may be Adele's, still no more than `per_song` of one song); how many were passed over
    for each, and the best score left. A reel itself is never given twice (see run).

    With `sourced`, that many places are the source pages' and the rest the searches':
    a side with too few leaves its places to the other. The pages were chosen by the
    owner, so they share their places between them, whatever `per_account` (and fill the
    searches' empty ones with no cap), and a singer's own channel isn't held to
    `per_artist`.

    Whole songs come SONGS_PER_CHANNEL from one channel (a label's posts many singers'
    songs), and all of them when the singer was asked for: the channel is often theirs;
    and SONGS_PER_ARTIST of one singer.

    With `instagram`, at most that many Instagram reels, wherever they come from: places
    they would have taken stay with YouTube's, or empty (the owner wants few)."""
    named = f" {_same(asked)} "
    picked: list[Reel] = []
    passed = {"same account": 0, "same song": 0, "same artist": 0, "Instagram's share": 0}
    accounts: dict[str, int] = {}
    songs: dict[str, int] = {}
    artists: dict[str, int] = {}
    pages = len({r.source for r in ranked if r.source}) or 1
    page_cap = max(per_account, -(-(sourced or 0) // pages))
    places = {True: sourced, False: None if sourced is None else count - sourced}
    taken = {True: 0, False: 0}
    waiting: list[Reel] = []  # its side's places were full: they may fill the other's
    skipped: set[int] = set()

    def blocked(reel: Reel, filling: bool = False) -> str | None:
        given = sum(r.on_instagram for r in picked)
        if reel.on_instagram and instagram is not None and given >= instagram:
            return "Instagram's share"
        who, song, artist = _same(reel.handle or reel.account), _same(reel.song), _same(reel.artist)
        wanted = artist and f" {artist} " in named
        cap = per_account
        if reel.source:  # a page filling the searches' empty places has no cap of its own
            cap = count if filling else page_cap
        elif reel.full:
            cap = count if wanted else max(per_account, SONGS_PER_CHANNEL)
        if who and accounts.get(who, 0) >= cap:
            return "same account"
        if song and songs.get(song, 0) >= per_song:
            return "same song"
        free = wanted or (reel.source and sourced is not None)
        most = max(per_artist, SONGS_PER_ARTIST) if reel.full and per_artist else per_artist
        if most is not None and artist and not free and artists.get(artist, 0) >= most:
            return "same artist"
        return None

    def take(reel: Reel) -> None:
        picked.append(reel)
        taken[bool(reel.source)] += 1
        for seen, key in (
            (accounts, _same(reel.handle or reel.account)),
            (songs, _same(reel.song)),
            (artists, _same(reel.artist)),
        ):
            seen[key] = seen.get(key, 0) + 1

    for reel in ranked:
        if len(picked) == count:
            break
        side = bool(reel.source)
        if places[side] is not None and taken[side] >= places[side]:
            waiting.append(reel)
            continue
        if why := blocked(reel):
            passed[why] += 1
            skipped.add(id(reel))
            continue
        take(reel)
    for reel in waiting:
        if len(picked) == count:
            break
        if why := blocked(reel, filling=True):
            passed[why] += 1
            skipped.add(id(reel))
            continue
        take(reel)
    # Places still empty take, best first, more of a singer already given (up to twice the
    # cap): a research of few artists gives more of them rather than leaving its places
    # empty (owner: "ek baari mein jada results"). One account and one song stay capped.
    if per_artist is not None and len(picked) < count:
        for reel in ranked:
            if len(picked) == count:
                break
            if id(reel) not in skipped:
                continue
            if blocked(reel) not in (None, "same artist"):
                continue  # its account or its song is full
            most = 2 * (max(per_artist, SONGS_PER_ARTIST) if reel.full else per_artist)
            if artists.get(_same(reel.artist), 0) >= most:
                continue
            take(reel)
            skipped.discard(id(reel))
            passed["same artist"] -= 1
    chosen = {id(r) for r in picked} | skipped
    left = [r.score for r in ranked if id(r) not in chosen]
    cutoff = max(left, default=None) if len(picked) == count else None
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
    stats = [
        {
            "code": r.code,
            "likes": r.likes,
            "comments": r.comments,
            "posted": r.posted.isoformat() if r.posted else None,
            "caption": _short(r.caption or r.title, 120),
        }
        for r in own
    ]
    return Learned(summary.strip(), [r.code for r in own], best, stats)


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
    songs=_youtube_songs,
) -> Result:
    """Search, look up, rate, rank and pick the best few. `planner()` opens the planning
    model, which holds the GPU, so it is opened only to plan and to rate, never while
    waiting on the web. Reels in `used` (already edited), in `seen` (shown by an earlier
    research), the account's own and those that don't fit are left out, so each research
    brings new ones. With whole songs more are looked up and given (SONGS_REELS,
    SONGS_SHOWN), and only those the model says are a song's own video to edit."""
    context = context or Context()
    progress("planning the searches", 0.02)
    with planner() as llm:
        brief, queries, music = plan(llm, description, context)
    if music is False:  # whole songs only for music: none to find for military, sports...
        sites = tuple(s for s in sites if s != SONGS)
    many = SONGS in sites
    limit, shown = (SONGS_REELS, SONGS_SHOWN) if many else (MAX_REELS, SHOWN)
    reels, failed = search(queries, find, pause, progress, sites, songs)
    if not reels and failed:
        raise RuntimeError("the search engines didn't answer; try again in a few minutes")
    if context.sources:
        progress("looking at your source pages", 0.42)
        # The pages first, their share of what is looked up, so the searches still count.
        share = round(limit * SOURCE_SHARE)
        each = max(PER_SOURCE, -(-share // len(context.sources[:MAX_SOURCES])))
        picked = from_sources(context.sources, find, pause, channel, each, many)[:share]
        taken = {r.code for r in picked}
        reels = picked + [r for r in reels if r.code not in taken]
    if many:
        progress("listing the singers' own channels", 0.42)
        known = {r.code for r in reels}
        own = [r for r in own_channels_songs(reels, channel) if r.code not in known]
        # In turn with the searches' (see search), so both get looked up.
        mixed = [r for pair in zip_longest(reels, own) for r in pair if r is not None]
        reels = list({r.code: r for r in mixed}.values())
    # Whole songs the search already shows can't be used aren't looked up.
    unfit = Counter(r.unfit for r in reels if r.unfit)
    reels = [r for r in reels if not r.unfit]
    left_out = {
        "edited": sum(r.code in used for r in reels),
        "shown before": sum(r.code in seen and r.code not in used for r in reels),
        "the account's own": sum(r.code in context.own - used - seen for r in reels),
    }
    reels = [r for r in reels if r.code not in used | seen | context.own]
    reels = instagram_few(reels, limit)
    progress(f"looking up {len(reels)} reels", 0.45)
    look_up(reels, info)
    reels = [r for r in reels if not r.page or r.handle == r.page]  # another's, found by name
    # A Short YouTube wouldn't show (gone, private, age-locked) has nothing but what the
    # search engine said: no channel, no caption to judge, nothing to download.
    left_out["couldn't be looked up"] = sum(r.short and not r.looked_up for r in reels)
    reels = [r for r in reels if not (r.short and not r.looked_up)]
    if context.handle:
        left_out["the account's own"] += sum(r.handle == context.handle for r in reels)
        reels = [r for r in reels if r.handle != context.handle]
    left_out["hidden account"] = sum(r.handle in context.hidden for r in reels)
    reels = [r for r in reels if r.handle not in context.hidden]
    left_out["not a Short"] = sum(r.long for r in reels)
    left_out["no video"] = sum(r.no_video for r in reels)
    unfit.update(r.unfit for r in reels if r.unfit)
    reels = [r for r in reels if not r.long and not r.no_video and not r.unfit]
    left_out["low quality"] = sum(r.quality == "low" for r in reels)
    reels = [r for r in reels if r.quality != "low"]
    wants_ai = bool(ASKS_FOR_AI.search(f"{description} {brief}"))
    for r in reels:
        r.unfit = not_wanted(r, bool(music) or many, wants_ai)
    unfit.update(r.unfit for r in reels if r.unfit)
    reels = [r for r in reels if not r.unfit]
    progress(f"rating {len(reels)} reels", 0.7)
    if reels:
        with planner() as llm:
            # The brief says it plainly; the request keeps what the brief may have lost.
            wanted = description.strip()
            if brief:
                wanted = f"{brief} (their words: {wanted})" if wanted else brief
            rate(llm, wanted, reels, context=context)
    judge_songs(reels)
    unfit.update(r.unfit for r in reels if r.unfit)
    reels = [r for r in reels if not r.unfit]
    left_out.update(unfit)
    if context.avoid or context.posted:  # an account that never posts a song twice
        posted = Posted(context.avoid, context.posted)
        again = {id(r) for r in reels if posted.has(song_title(r), r.artist)}
        left_out["posted before"] = len(again)
        reels = [r for r in reels if id(r) not in again]
    if context.strikes:
        listed = Posted(context.strikes, [])
        blocked = {id(r) for r in reels if listed.has(song_title(r), r.artist)}
        left_out["on the strike list"] = len(blocked)
        reels = [r for r in reels if id(r) not in blocked]
    # A reel the model couldn't rate isn't shown (the owner: no junk), unless it rated none:
    # then they all are, as half relevant, rather than nothing.
    rated = any(r.relevance is not None for r in reels)
    left_out["couldn't be rated"] = sum(r.relevance is None for r in reels) if rated else 0
    least = ASKED_FIT if description.strip() else MIN_FIT
    fitting = [
        r for r in reels
        if (r.relevance is None and not rated) or (r.relevance or 0) >= least - 1e-9
    ]  # fmt: skip
    left_out["didn't fit"] = len(reels) - len(fitting) - left_out["couldn't be rated"]
    suggest(fitting, context)
    spread(fitting)
    # Only the person's own words (and answers) free an artist: the model's brief may name
    # examples of a genre they didn't ask for.
    sourced = round(shown * SOURCE_SHARE) if context.sources else None
    picked, passed, cutoff = pick(rank(fitting), shown, asked=description, sourced=sourced,
                                  instagram=max(1, int(shown * INSTAGRAM_SHOWN)))  # fmt: skip
    picked, over = instagram_share(picked, INSTAGRAM_SHOWN)
    passed["Instagram's share"] = passed.get("Instagram's share", 0) + over
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


def run_pdf(
    entries: list[dict],
    planner: Callable,
    used: set[str],
    seen: set[str],
    progress: Progress,
    find=_engines,
    info=_info,
    pause: float | None = None,
    context: Context | None = None,
    sites: tuple[str, ...] = ("instagram",),
    kind: str = "songs",
    songs=_youtube_songs,
) -> list[tuple[int, Reel]]:
    """For each item a PDF lists (see pdf_entries), the best few reels of it, PER_ENTRY at
    most, as (its entry's number, reel): searched for by its name and who or what it is
    by, looked up, rated and ranked as in run, never one edited, shown before, or given
    for another item. `kind` says what the list is of ("songs", "films", "military
    aircraft"...): songs are searched for as performances, anything else as itself. An
    item nothing fitting turned up for is searched for once more, with other words: the
    engines give a different few each time."""
    of_songs = is_songs(kind)
    context = context or Context()
    given: list[tuple[int, Reel]] = []
    taken = used | seen | context.own
    answered = False
    for n, entry in enumerate(entries):
        song, singer = entry["song"], entry["singer"]
        who = " - ".join(x for x in (song, singer) if x)
        progress(f"searching for {who} ({n + 1} of {len(entries)})", 0.05 + 0.9 * n / len(entries))
        if of_songs:
            rounds = [[f"{singer} {song}", f"{song} {singer} live"],
                      [f"{song} {singer} reel", f"{singer} {song} performance"]]  # fmt: skip
        else:
            rounds = [[f"{song} {singer}", f"{song} {singer} video"],
                      [f"{song} reel", f"{song} {singer} clip"]]  # fmt: skip
        picked: list[Reel] = []
        looked: set[str] = set()  # by the first round: not again in the second
        for queries in rounds:
            queries = _tidy(queries)
            reels, failed = search(queries, find, pause, sites=sites, songs=songs)
            answered = answered or len(failed) < len(queries) * len(sites)
            reels = [r for r in reels if r.code not in taken | looked and not r.unfit]
            reels = reels[:PDF_REELS]
            looked |= {r.code for r in reels}
            look_up(reels, info)
            reels = [
                r
                for r in reels
                if not r.long
                and not r.no_video
                and not r.unfit
                and r.quality != "low"
                and r.handle not in context.hidden
                and not (context.handle and r.handle == context.handle)
            ]
            if reels:
                with planner() as llm:
                    wish = (
                        f"reels of the song '{song}' by {singer}, {singer} singing it"
                        if of_songs
                        else f"reels showing {song}"
                        + (f" ({singer})" if singer else "")
                        + f": one of a list of {kind}, the reel about this one itself"
                    )
                    rate(llm, wish, reels, context=context)
            judge_songs(reels)
            fitting = [
                r for r in reels if not r.unfit and (r.relevance is None or r.relevance >= MIN_FIT)
            ]
            suggest(fitting, context)
            if entry.get("preset"):
                # The preset the person chose for the song's section (see songlist.py).
                for r in fitting:
                    r.preset, r.preset_why = entry["preset"], "the preset chosen for its section"
            spread(fitting)
            # All of one song: only the accounts are kept apart.
            picked, _, _ = pick(rank(fitting), count=PER_ENTRY, per_song=PER_ENTRY, per_artist=None)
            if picked:
                break
        taken |= {r.code for r in picked}
        given += [(n, r) for r in picked]
    if not answered and entries:
        raise RuntimeError("the search engines didn't answer; try again in a few minutes")
    return given


class _Pick(BaseModel):
    sections: list[str]
    first: int = Field(ge=1)
    last: int = Field(ge=0)  # 0: to the end
    year_from: int = Field(ge=0)  # 0: any
    year_to: int = Field(ge=0)
    judge: str  # what to check song by song that the choices above can't say; '' if nothing
    swap: bool
    line: Literal["top", "bottom", "none"]
    presets: dict[str, str]
    understood: str  # one line: what it will do


PICK_PROMPT = (
    "A video editor gives a PDF that lists {kind}, and says in their own words (any "
    "language) which of them to find reels of and how. Turn what they say into choices.\n"
    "Sections of the list, with their numbers:\n{sections}\n"
    "An item of it looks like: {sample}\n"
    "Their presets (name: what it is called):\n{presets}\n\n"
    "Their words: {instruction}\n\n"
    "Give: sections (the section names to research, exactly as listed; all of them if they "
    "don't say); first and last (the numbers within each section; first 1 and last 0 when "
    "they don't say; 'the top 30 are done' means first 31); year_from and year_to (0 when "
    "they don't limit the years); judge (anything else they want songs kept or left out "
    "by, which only knowing each item can tell, such as 'only female singers' or 'leave out "
    "Armando Manzanero', written as one English sentence; '' when the choices above say "
    "it all); swap (true only if they say the name comes after the one it is by in each "
    "line, as in 'Singer - Song'); "
    "line (where the words written for each item go: top for above the video, bottom for "
    "below, none for nowhere; top if they don't say); presets (for each section they give "
    "a preset for, the preset's name exactly as listed; a section they name by its decade "
    "or genre is that section); and understood: one short sentence of what you will do, "
    "in their language and their script (Hinglish written in Latin letters stays in Latin "
    "letters)."
)
JUDGE_PROMPT = (
    "These items are from a list of {kind} (id | section | number | name | by | year). "
    "Keep only "
    "the ones that match: {judge}\nGive the ids to keep.\n\n{songs}"
)
JUDGE_BATCH = 80  # songs a judging call looks at


class _Kept(BaseModel):
    keep: list[int]


def pdf_choose(
    llm, instruction: str, listing: list[dict], presets: dict[str, str], kind: str = "songs"
) -> dict:
    """What a person's words about a PDF's songs ask for, as the songs to research
    (`keep`, positions in `listing`) and how: sections, numbers and years are read into
    choices and applied in code; anything else ("only female singers") is judged song by
    song. Section and preset names the model gets wrong are dropped, never guessed at."""
    found = songlist.sections_of(listing)
    first = listing[0]
    sample = f"{first['number']}. {first['song']} ({first['year']}) - {first['singer']}"
    reply = parse(
        _Pick,
        llm.chat_json(
            [
                {
                    "role": "user",
                    "content": PICK_PROMPT.format(
                        sections="\n".join(
                            f"- {s['name']} ({s['first']}-{s['last']})" for s in found
                        ),
                        sample=sample,
                        presets="\n".join(f"- {n}: {label}" for n, label in presets.items())
                        or "- (none)",
                        instruction=instruction.strip(),
                        kind=kind,
                    ),
                }
            ],
            _Pick.model_json_schema(),
            temperature=0.0,
        ),
    )
    names = [s["name"] for s in found]
    wanted = [s for s in reply.sections if s in names] or names
    keep = songlist.pick(
        listing, wanted, reply.first, reply.last or None, reply.year_from or None,
        reply.year_to or None,
    )  # fmt: skip
    if reply.judge.strip():
        keep = judged(llm, reply.judge.strip(), listing, keep, kind)
    return {
        "keep": keep,
        "swap": reply.swap,
        "line": reply.line,
        "presets": {s: p for s, p in reply.presets.items() if s in names and p in presets},
        "understood": reply.understood.strip(),
    }


def judged(llm, judge: str, listing: list[dict], keep: list[int], kind: str = "songs") -> list[int]:
    """The songs at `keep` that match `judge`, asked about JUDGE_BATCH at a time."""
    out: list[int] = []
    for start in range(0, len(keep), JUDGE_BATCH):
        batch = keep[start : start + JUDGE_BATCH]
        songs = "\n".join(
            f"{i} | {listing[i]['section']} | {listing[i]['number']} | {listing[i]['song']} | "
            f"{listing[i]['singer']} | {listing[i]['year'] or '-'}"
            for i in batch
        )
        reply = parse(
            _Kept,
            llm.chat_json(
                [
                    {
                        "role": "user",
                        "content": JUDGE_PROMPT.format(judge=judge, songs=songs, kind=kind),
                    }
                ],
                _Kept.model_json_schema(),
                temperature=0.0,
            ),
        )
        allowed = set(batch)
        out += [i for i in dict.fromkeys(reply.keep) if i in allowed]
    return sorted(out)


LIST_WINDOW = 5_000  # characters of a PDF the model reads at a time
LIST_OVERLAP = 400  # read again at the start of the next window: an entry may be cut


class _ListedEntry(BaseModel):
    section: str = Field(max_length=120)
    number: int = Field(ge=0)
    song: str = Field(max_length=160)
    singer: str = Field(max_length=120)
    year: str = Field(max_length=4)
    words: str = Field(max_length=800)
    top: str = Field(max_length=400)
    bottom: str = Field(max_length=400)


class _ListedPart(BaseModel):
    entries: list[_ListedEntry] = Field(max_length=80)


def is_songs(kind: str) -> bool:
    return bool(re.search(r"song|music|canci|canzon|chanson|lied|gaan", kind, re.IGNORECASE))


def guess_kind(text: str) -> str:
    """What a PDF lists, as far as its words say: "songs" or, not knowing, "items"; the
    person names it when it's something else."""
    words = r"\b(songs?|singers?|canci[oó]n(es)?|canzon[ei]|chansons?|lieder|gaan[ae]|album)\b"
    return "songs" if re.search(words, text[:20000], re.IGNORECASE) else "items"


READ_LIST = (
    "This is part of a PDF that lists things to find videos of, laid out any way: a list, a "
    "table, prose. They may be songs, films, people, places, events, products, anything. "
    "For each item in it give: section (a heading over several of the items, such as a "
    "decade or genre, as written; '' if the list has none: the PDF's own title or "
    "introduction is no section), number (its number in the list, 0 if it has none), song "
    "(the item's name: the song's title, the film's, the person's...), singer (only the "
    "name of who or what it is by or from: the singer, director, team, maker, without "
    "words like 'by' or 'directed by'; '' if none), "
    "year ('' if not given), words (anything written for it, such as a note or the words "
    "for a video, copied exactly, with \\n between lines; '' if none), top and bottom (only "
    "words the PDF labels as above or below the video, else ''). Copy everything exactly "
    "as written, in its language. Leave out headings, introductions and anything that "
    "isn't an item. An item cut off at the very start or end of this part: leave it out if "
    "its name is cut.\n\nPart {part} of the PDF:\n{text}"
)


def _windows(text: str) -> list[str]:
    lines = [line for line in text.replace(songlist.PAGE_BREAK, "").splitlines() if line.strip()]
    windows, current, size = [], [], 0
    for line in lines:
        if current and size + len(line) > LIST_WINDOW:
            windows.append("\n".join(current))
            kept, kept_size = [], 0
            for prev in reversed(current):  # the window's end, read again in the next
                if kept_size + len(prev) > LIST_OVERLAP:
                    break
                kept.insert(0, prev)
                kept_size += len(prev)
            current, size = kept, kept_size
        current.append(line)
        size += len(line)
    if current:
        windows.append("\n".join(current))
    return windows


BY = re.compile(
    r"^(?:by|from|de|di|von|par|directed by|written by|performed by|sung by|made by)\s+",
    re.IGNORECASE,
)
ALL = "All"  # the section of a list that has none


def pdf_listing(llm, text: str, progress: Progress | None = None) -> list[dict]:
    """Every item a PDF lists, laid out any way, read by the model a window at a time,
    as songlist.Listed dicts in the PDF's order: for PDFs songlist.read can't read."""
    out: dict[tuple[str, str, str], dict] = {}
    given: set[tuple[str, str, str]] = set()  # numbered by the PDF itself
    windows = _windows(text)
    for n, window in enumerate(windows):
        if progress:
            progress(f"reading the PDF ({n + 1} of {len(windows)})", 0.9 * n / len(windows))
        reply = parse(
            _ListedPart,
            llm.chat_json(
                [{"role": "user", "content": READ_LIST.format(part=n + 1, text=window)}],
                _ListedPart.model_json_schema(),
                temperature=0.0,
            ),
        )
        for e in reply.entries:
            name, by = e.song.strip(), BY.sub("", e.singer.strip())
            if not (name or by):
                continue
            key = (e.section.strip().casefold(), name.casefold(), by.casefold())
            words = [w.strip() for w in e.words.replace("\\n", "\n").split("\n") if w.strip()]
            pairs = (("top", e.top), ("bottom", e.bottom))
            labelled = {k: v.strip().replace("\\n", "\n") for k, v in pairs if v.strip()}
            if key in out:
                # Read twice where windows overlap: the fuller reading stays.
                if len(" ".join(words)) > len(" ".join(out[key]["lines"])):
                    out[key]["lines"], out[key]["labelled"] = (
                        words,
                        labelled or out[key]["labelled"],
                    )
                continue
            year = e.year.strip()
            out[key] = {
                "number": e.number,
                "section": e.section.strip() or ALL,
                "song": name,
                "singer": by,
                "year": year if year.isdigit() and len(year) == 4 else "",
                "lines": words,
                "labelled": labelled,
            }
            if e.number:
                given.add(key)
    keys = list(out)
    listing = list(out.values())
    # A section of one item beside another section is a heading the model took for one
    # (the PDF's title over its first item): it joins its neighbour's.
    counts = Counter(e["section"] for e in listing)
    for i, e in enumerate(listing):
        if counts[e["section"]] == 1 and len(counts) > 1:
            near = [listing[j]["section"] for j in (i + 1, i - 1) if 0 <= j < len(listing)]
            e["section"] = next((s for s in near if counts[s] > 1), e["section"])
    # The PDF's own numbers stand when a section's run upwards; else (none, or numbers the
    # model made up) its items are numbered in order.
    for section in dict.fromkeys(e["section"] for e in listing):
        members = [(k, e) for k, e in zip(keys, listing, strict=True) if e["section"] == section]
        numbers = [e["number"] for _, e in members]
        own = all(k in given for k, _ in members) and all(a < b for a, b in pairwise(numbers))
        if not own:
            for n, (_, e) in enumerate(members, 1):
                e["number"] = n
    return listing
