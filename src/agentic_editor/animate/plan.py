"""The Plan: what a reel says and shows, scene by scene (docs/animation-studio.md).

The planner writes it, a person edits it, and the voice, timeline, sound and player all
read it. Visuals share one flat set of fields (the catalogue below says which a kind
uses): a small model fills a flat schema far more reliably than a union of shapes.
`tidy` mends what can be mended (emoji that aren't, too many items, a highlight that isn't
in the headline); `problems` lists what a writer has to fix.
"""

import re
import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

KINDS = ("facts", "lesson", "story", "news")
STYLES = ("glow", "paper", "story", "kinetic", "stick", "map", "song", "singer")
MOODS = ("mysterious", "uplifting", "emotional", "tense", "curious", "upbeat")
LENGTHS = ("auto", "short", "medium", "long")
# Narration words a reel of each length runs to (Kokoro speaks about 2.6 words a second).
WORDS = {"short": (50, 85), "medium": (90, 150), "long": (155, 230), "auto": (70, 130)}
# A World map reel tours a dozen countries or more, so it runs longer when left to itself
# (the reference reels: 100-230 words, 55-100 s).
MAP_WORDS = {"short": (55, 90), "medium": (90, 150), "long": (150, 230), "auto": (110, 175)}

Kind = Literal["facts", "lesson", "story", "news"]
Style = Literal["glow", "paper", "story", "kinetic", "stick", "map", "song", "singer"]
Mood = Literal["mysterious", "uplifting", "emotional", "tense", "curious", "upbeat"]
# A World map reel's look, as the reference page has them: white picture cards (most
# topics), gold horror (haunted and scary topics: blood-red countries, photos in gold
# frames, gold names) and neon labels (food: the dish cut out on the map, its name glowing).
LOOKS = ("cards", "horror", "neon")
Look = Literal["cards", "horror", "neon"]
VisualKind = Literal[
    "title", "stat", "icons", "compare", "list", "timeline", "place", "quote", "reveal", "dots",
    "scene", "country", "moment", "verse",
]  # fmt: skip
# Character scenes (player.js character rig): where, who looks like what, what they do.
SETTINGS = ("room", "street", "park", "office", "night", "school", "shop", "stage", "plain")
ACTIONS = (
    "idle", "talk", "walk_in", "walk_out", "run_in", "wave", "point", "think", "celebrate",
    "sad", "shocked", "angry", "shrug", "sit", "read", "give", "cry",
)  # fmt: skip
FEELINGS = ("neutral", "happy", "sad", "angry", "surprised", "scared", "thinking")
LOOKS = ("man", "woman", "boy", "girl", "old man", "old woman")
# How a singer edit's line shows the singer (animate/singer.py, player/singer.js).
SHOTS = ("hero", "close", "far", "print", "mirror", "echo", "silhouette", "words", "painted")
MAX_ACTORS = 2
MAX_CAST = 4
# Too big to hold: a character with one of these in hand looks wrong.
BIG_PROPS = set("🪑🛋🛏🚪🏠🏡🏢🌳🌲🌴🚗🚌🚲✈🚀⛵🗻🏔🌋")
# Words in a name that say how someone looks, overruling a look that disagrees.
LOOK_HINTS = (
    (("grandpa", "grandfather", "old man", "uncle"), "old man"),
    (("grandma", "grandmother", "granny", "old woman", "auntie"), "old woman"),
    (("mr", "sir", "dad", "father", "king", "mister", "uncle"), "man"),
    (("mrs", "ms", "miss", "mom", "mum", "mother", "queen", "lady", "madam"), "woman"),
)
Motion = Literal[
    "pop", "float", "walk_left", "walk_right", "shake", "spin", "grow", "fall", "pulse"
]

# What each visual is for and the fields it uses; the planner's prompt is made from this.
CATALOGUE: dict[str, tuple[str, str]] = {
    "title": (
        "the hook or a big statement",
        "text (max 7 words), highlight (1-2 words of text), kicker (optional 1-3 word label)",
    ),
    "stat": (
        "one number that matters",
        "value (the number as written, e.g. 4,500 or 1.3), prefix (e.g. $), "
        "suffix (e.g. million, %, years), text (what it counts, max 8 words)",
    ),
    "icons": (
        "someone or something doing something (stories, lessons)",
        "items: 1-3 of {emoji, motion}, text (max 8 words)",
    ),
    "compare": (
        "A against B, before and after",
        "items: exactly 2 of {emoji, text (label, max 4 words), value (max 12 chars)}",
    ),
    "list": (
        "2-5 points, steps or reasons",
        "text (title, max 6 words), items: 2-5 of {emoji (optional), text (max 7 words)}",
    ),
    "timeline": ("dates and what happened", "items: 2-5 of {year, text (max 7 words)}"),
    "place": (
        "where it happened",
        "text (place name, max 4 words), detail (country, era; max 6 words), "
        "emoji (optional, default a pin)",
    ),
    "quote": ("a line someone said or wrote", "text (the quote, max 22 words), by (who)"),
    "reveal": (
        "the answer to a question or mystery",
        "text (the question, max 10 words), answer (max 5 words), emoji",
    ),
    "dots": (
        "one short word or number drawn by glowing dots",
        "value (max 8 characters), text (max 8 words)",
    ),
    "country": (
        "one country and the things it is known for (World map reels)",
        "text (the country as the narration names it), items: 1-5 of {text (the thing's "
        "name), emoji (one emoji for it), art (what its picture shows)}",
    ),
    "scene": (
        "a moment of the story acted by characters from the cast, in a place",
        "setting (one of " + ", ".join(SETTINGS) + "), actors: 1-2 of {name (from the "
        "cast), action (one of "
        + ", ".join(ACTIONS)
        + "), mood (one of "
        + ", ".join(FEELINGS)
        + "), prop (one emoji they hold or use, or ''), says (a line in a speech bubble, max "
        "6 words, or '')}, text (the scene's title, e.g. a person's name or 'Years later', max 4 "
        "words), detail (a red note with an arrow at the first actor, e.g. '40 days', max 4 "
        "words, or ''), emoji (one big object standing beside them, e.g. a barrel, or ''), crowd "
        "(0-8 more people standing around)",
    ),
}

# A Song story's moment (kind "moment", written only by animate/songwriter.py, so not in
# the catalogue the other writers choose from): a painted picture of the story with its
# people in it, and how the camera moves over it while its lyric line is sung.
CAMERAS = ("push_in", "pull_out", "pan_left", "pan_right", "rise", "close_up", "still")
# The colours a Song story's key words are drawn in (player.js SONG_COLORS), by what they mean.
WORD_COLORS = ("white", "amber", "red", "blue", "pink", "green")

MOTIONS = ("pop", "float", "walk_left", "walk_right", "shake", "spin", "grow", "fall", "pulse")
DEFAULT_EMOJI = {
    "icons": "✨", "compare": "❓", "place": "📍", "reveal": "💡", "list": "", "country": "🌍",
}  # fmt: skip
MAX_SCENES = 24
MAX_COUNTRY_ITEMS = 5
CTA = "Follow for more."


class Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Item(Loose):
    emoji: str = ""
    text: str = ""
    year: str = ""
    value: str = ""
    motion: str = "pop"
    # A World map card's picture: what it shows, as the writer put it, its file once
    # painted (animate/images.py), and a number to paint it again.
    art: str = ""
    image: str = ""
    art_seed: int = 0


class Actor(Loose):
    """One character in a scene: who (a name from the cast), what they do and feel, what
    they hold, and a short line in a bubble."""

    name: str = ""
    action: str = "idle"
    mood: str = "neutral"
    prop: str = ""
    says: str = ""


class CastMember(Loose):
    name: str = ""
    look: str = "man"


class Visual(Loose):
    kind: VisualKind = "title"
    text: str = ""
    highlight: str = ""
    kicker: str = ""
    value: str = ""
    prefix: str = ""
    suffix: str = ""
    detail: str = ""
    by: str = ""
    answer: str = ""
    emoji: str = ""
    items: list[Item] = Field(default_factory=list)
    setting: str = ""
    actors: list[Actor] = Field(default_factory=list)
    crowd: int = 0  # extra people standing around in a character scene
    # A country scene's country as the map knows it, when it differs from how the
    # narration names it ("the States"); empty: `text` is looked up.
    country: str = ""
    # The painted picture behind the scene (animate/images.py): what it shows, as the
    # writer put it; the picture's file once painted; a number to paint it again.
    art: str = ""
    image: str = ""
    art_seed: int = 0
    camera: str = ""  # a moment's camera move (CAMERAS)
    # A singer edit's line (kind "verse", animate/singer.py): how the singer is shown, and
    # whether its picture has the singer painted in (from their photos) rather than
    # standing in front of it cut out.
    shot: str = ""
    with_singer: bool = False
    # Where the singer's face is in that picture, [x, y, w, h] of it, 0..1 (words keep off).
    face: list[float] = Field(default_factory=list)


class Scene(Loose):
    narration: str
    visual: Visual = Field(default_factory=Visual)
    hold: float = Field(default=0.35, ge=0, le=3)


class Source(Loose):
    title: str = ""
    url: str = ""


class SongWord(Loose):
    """A sung word, its times in the song (seconds), and whether it is drawn big and in
    which colour (WORD_COLORS)."""

    text: str
    start: float
    end: float
    key: bool = False
    color: str = ""


class SongLine(Loose):
    """A lyric line as sung: its words in the script shown, and how it was written where
    the lyrics came from (romanised Hindi, say), for the writer."""

    text: str = ""
    said: str = ""
    start: float = 0.0
    end: float = 0.0
    words: list[SongWord] = Field(default_factory=list)


class HookChoice(Loose):
    """A part of the song that could be the reel's: where it starts and ends, how it opens."""

    start: float
    end: float
    first: str = ""
    score: float = 0.0


class Person(Loose):
    """Someone in a Song story: a name the writer uses and how they look in every picture."""

    name: str = ""
    look: str = ""


class Song(Loose):
    """The song a Song story reel is made to (animate/songhook.py): what was found for the
    name typed, the part of it the reel plays (`start`-`end` of the downloaded audio) and its
    lyric lines, one scene each."""

    query: str = ""
    title: str = ""
    artist: str = ""
    video: str = ""  # the YouTube video's id the sound came from
    url: str = ""
    audio: str = ""  # the downloaded song's file in the songs cache (songhook.audio_path)
    duration: float = 0.0
    start: float = 0.0
    end: float = 0.0
    language: str = ""
    lyrics: str = ""  # where the lines came from: "lrclib" or "heard" (transcribed)
    lines: list[SongLine] = Field(default_factory=list)
    choices: list[HookChoice] = Field(default_factory=list)
    # The story told over it (animate/songwriter.py): who is in it and what happens.
    people: list[Person] = Field(default_factory=list)
    story: str = ""


class SungWord(Loose):
    text: str = ""
    start: float = 0.0
    end: float = 0.0


class SungLine(Loose):
    """One sung line of a singer edit's hook: a scene, times from the hook's start."""

    text: str = ""
    start: float = 0.0
    end: float = 0.0
    words: list[SungWord] = Field(default_factory=list)


class SingerPhoto(Loose):
    image: str = ""  # the photo as given (a picture in images.folder, named by its content)
    cutout: str = ""  # the singer cut out of it, see-through around them; "" when none
    face: list[float] = Field(default_factory=list)  # [x, y, w, h] in the cutout, 0..1
    aspect: float = 0.0  # the cutout's width over its height


class SingerEdit(Loose):
    """What a singer edit is made from (animate/singer.py): the song's hook and the
    singer's photos. Set by the studio, never by a writer or an edit by hand."""

    query: str = ""  # the song as the owner named it
    title: str = ""
    artist: str = ""
    audio: str = ""  # the hook's sound, a WAV named by its content (singer.songs_dir)
    duration: float = 0.0
    lines: list[SungLine] = Field(default_factory=list)  # one per scene, in order
    photos: list[SingerPhoto] = Field(default_factory=list)
    look: str = ""  # how the singer looks, for pictures that paint them


class Plan(Loose):
    v: int = 1
    title: str = ""
    kind: Kind = "facts"
    style: Style = "glow"
    voice: str = ""
    mood: Mood = "curious"
    scenes: list[Scene] = Field(default_factory=list, max_length=MAX_SCENES)
    cast: list[CastMember] = Field(default_factory=list)
    cta: str = CTA
    caption: str = ""
    sources: list[Source] = Field(default_factory=list)
    look: Look = "cards"  # a World map reel's (LOOKS); the other styles ignore it
    song: Song | None = None  # a Song story reel's song, its part and lyric lines
    singer: SingerEdit | None = None  # a singer edit's song and photos (style "singer")


# ---- mending ---------------------------------------------------------------------------

_PICTURE = re.compile(r"[0-9a-f]{24}\.png")  # images.name_of

# A field's name and value a writer left in a text: "Most famous festivals in every country;
# highlight: festivals" (a hook card showed "...; HIGHLIGHT:").
_LEAK = re.compile(
    r"\s*[;,|/-]?\s*\b(highlight|kicker|headline|hook|caption|emoji|subtitle|narration)\s*:\s*(.*)$",
    re.I,
)


# Markdown emphasis a model writes around the word it means to stand out ("Most famous
# **festivals** in every country"): drawn as it is, the asterisks show on the card.
_EMPHASIS = re.compile(r"(\*\*|__)(.+?)\1|(?<![\w*])\*(?!\s)([^*]+?)(?<!\s)\*(?![\w*])")


def plain(text: str) -> str:
    """`text` without markdown emphasis marks around its words."""
    return _EMPHASIS.sub(lambda m: m.group(2) or m.group(3), text or "")


def no_leak(text: str) -> tuple[str, dict[str, str]]:
    """`text` without markdown emphasis or a field name and value left at its end, and what
    was found: the emphasised words count as the highlight."""
    text = text or ""
    leaked: dict[str, str] = {}
    if marked := _EMPHASIS.search(text):
        leaked["highlight"] = (marked.group(2) or marked.group(3)).strip()
        text = plain(text)
    found = _LEAK.search(text)
    if not found or not text[: found.start()].strip():
        return text, leaked
    value = found.group(2).strip().strip("\"'.;,")
    return text[: found.start()].strip(" ;,|/-"), {**leaked, found.group(1).lower(): value}


_ASKS_TO_FOLLOW = re.compile(r"\bfollow (for|us|me|the page)\b|\bsubscribe\b|\blike and\b", re.I)
_STALE_OPENERS = re.compile(r"\b(did you know|in this video|today we)\b", re.I)

_EMOJI_CATEGORIES = {"So", "Sk"}


def is_emoji(text: str) -> bool:
    """One emoji (a pictograph, maybe with joiners, skin tones or a variation selector)."""
    text = text.strip()
    if not text or len(text) > 12:
        return False
    marks = [c for c in text if unicodedata.category(c) in _EMOJI_CATEGORIES]
    others = [
        c
        for c in text
        if unicodedata.category(c) not in _EMOJI_CATEGORIES
        and c not in "‍️︎⃣"
        and not (0x1F3FB <= ord(c) <= 0x1F3FF)
        and not (0xE0020 <= ord(c) <= 0xE007F)
        and not (0x1F1E6 <= ord(c) <= 0x1F1FF)
    ]
    flags = [c for c in text if 0x1F1E6 <= ord(c) <= 0x1F1FF]
    return (bool(marks) or len(flags) == 2) and not others


def _one_line(text: str, limit: int) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _emoji(value: str, default: str) -> str:
    return value.strip() if is_emoji(value) else default


def tidy(plan: Plan) -> Plan:
    """The plan with what a writer gets slightly wrong put right, never changing meaning:
    whitespace, emoji that aren't emoji, too many items, motions outside the list, a
    highlight not in its headline, lengths past what the screen holds."""
    scenes = []
    for scene in plan.scenes[:MAX_SCENES]:
        v = scene.visual
        cap = {"list": 5, "timeline": 5, "icons": 3, "compare": 2}.get(v.kind, MAX_COUNTRY_ITEMS)
        items = [
            Item(
                emoji=_emoji(i.emoji, DEFAULT_EMOJI.get(v.kind, "")),
                text=_one_line(i.text, 70),
                year=_one_line(i.year, 16),
                value=_one_line(i.value, 16),
                motion=i.motion if i.motion in MOTIONS else "pop",
                art=_one_line(i.art, 240) if v.kind == "country" else "",
                image=i.image if v.kind == "country" and _PICTURE.fullmatch(i.image or "") else "",
                art_seed=max(0, min(10_000, int(i.art_seed or 0))) if v.kind == "country" else 0,
            )
            for i in v.items[:cap]
        ]
        actors = [
            Actor(
                name=_one_line(x.name, 24) or "Sam",
                action=x.action if x.action in ACTIONS else "idle",
                mood=x.mood if x.mood in FEELINGS else "neutral",
                prop="" if _emoji(x.prop, "").rstrip("\ufe0f") in BIG_PROPS else _emoji(x.prop, ""),
                says=_one_line(x.says, 48),
            )
            for x in v.actors[:MAX_ACTORS]
        ]
        text, leaked = no_leak(" ".join(v.text.split()))
        text = _one_line(text, 200 if v.kind == "quote" else 90)
        highlight = v.highlight.strip() or leaked.get("highlight", "")
        if v.kind == "verse":  # a sung line's key words, one by one, wherever they are in it
            words = text.lower().split()
            stands = all(w in words for w in highlight.lower().split())
        else:
            stands = highlight.lower() in text.lower()
        if not stands:
            highlight = ""
        visual = v.model_copy(
            update={
                "text": text,
                "highlight": highlight,
                "kicker": _one_line(v.kicker, 28),
                "value": _one_line(v.value, 16),
                "prefix": _one_line(v.prefix, 4),
                "suffix": _one_line(v.suffix, 16),
                "detail": _one_line(v.detail, 60),
                "by": _one_line(v.by, 50),
                "answer": _one_line(v.answer, 50),
                "emoji": _emoji(v.emoji, DEFAULT_EMOJI.get(v.kind, "")),
                "items": items,
                "setting": setting_of(v),
                "actors": actors,
                "crowd": max(0, min(8, int(v.crowd or 0))) if v.kind == "scene" else 0,
                "country": _one_line(v.country, 60) if v.kind == "country" else "",
                "art": _one_line(v.art, 300),
                "image": v.image if _PICTURE.fullmatch(v.image or "") else "",
                "art_seed": max(0, min(10_000, int(v.art_seed or 0))),
                "camera": camera_of(v),
                "shot": v.shot if v.kind == "verse" and v.shot in SHOTS else "",
                "face": [round(float(x), 4) for x in v.face]
                if v.kind == "verse" and len(v.face) == 4 and all(0 <= x <= 1 for x in v.face)
                else [],
                "with_singer": bool(
                    v.with_singer and v.kind == "verse" and _PICTURE.fullmatch(v.image or "")
                ),
            }
        )
        narration = " ".join(plain(scene.narration).split())
        # A scene that only asks people to follow: the system's own ending does that. A sung
        # line (a Song story's or a singer edit's) is the song's own, whatever it says.
        sung = plan.style == "song" or v.kind == "verse"
        if not sung and _ASKS_TO_FOLLOW.search(narration) and len(narration.split()) <= 12:
            continue
        if narration:
            scenes.append(scene.model_copy(update={"narration": narration, "visual": visual}))
    return plan.model_copy(
        update={
            "title": _one_line(no_leak(plan.title)[0], 70),
            "scenes": scenes,
            "cast": _cast(plan.cast, scenes),
            # A Song story ends with its song, never a spoken "follow" line.
            "cta": "" if plan.style == "song" else _one_line(plan.cta or CTA, 60),
            "caption": plan.caption.strip()[:2200],
            "song": _song(plan.song) if plan.song is not None else None,
        }
    )


def camera_of(v: Visual) -> str:
    if v.camera in CAMERAS:
        return v.camera
    return "push_in" if v.kind == "moment" else ""


def _song(song: Song) -> Song:
    """A song's lines with their words in order inside the part played, colours only from
    WORD_COLORS and only on key words."""
    lines = []
    for line in song.lines:
        words = [
            w.model_copy(
                update={
                    "text": " ".join(w.text.split()),
                    "end": max(w.end, w.start),
                    "color": (w.color if w.color in WORD_COLORS else "white") if w.key else "",
                }
            )
            for w in sorted(line.words, key=lambda w: w.start)
            if w.text.strip()
        ]
        text = " ".join(w.text for w in words) or " ".join(line.text.split())
        lines.append(line.model_copy(update={"words": words, "text": text}))
    return song.model_copy(update={"lines": lines, "query": _one_line(song.query, 200)})


def setting_of(v: Visual) -> str:
    if v.setting in SETTINGS:
        return v.setting
    return "room" if v.kind == "scene" else ""


def _hinted(name: str, look: str) -> str:
    """The look a name like "Grandpa Eli" or "Mrs Patel" says, when the writer's disagrees."""
    words = set(re.findall(r"[a-z]+", name.lower()))
    joined = " ".join(re.findall(r"[a-z]+", name.lower()))
    for hints, hinted in LOOK_HINTS:
        if any((h in joined) if " " in h else (h in words) for h in hints):
            return hinted
    return look


def _cast(cast: list[CastMember], scenes: list[Scene]) -> list[CastMember]:
    """Everyone who acts, once each, with a look: those the writer named first, then any
    actor it forgot to cast (a man unless the name says nothing more)."""
    out: list[CastMember] = []
    seen: set[str] = set()
    for member in cast:
        name = _one_line(member.name, 24)
        if name and name.lower() not in seen:
            look = member.look if member.look in LOOKS else "man"
            out.append(CastMember(name=name, look=_hinted(name, look)))
            seen.add(name.lower())
    for scene in scenes:
        for actor in scene.visual.actors:
            if actor.name.lower() not in seen:
                out.append(CastMember(name=actor.name, look=_hinted(actor.name, "man")))
                seen.add(actor.name.lower())
    return out[:MAX_CAST]


# ---- checking --------------------------------------------------------------------------

_DIGIT = re.compile(r"\d")


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _says(narration: str, text: str) -> bool:
    """Whether the narration says the main word of `text` (its longest word)."""
    words = [w for w in _plain(text).split() if len(w) > 2] or _plain(text).split()
    if not words:
        return True
    key = max(words, key=len)
    return key in _plain(narration).split() or any(
        w.startswith(key) for w in _plain(narration).split()
    )


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def narration_words(plan: Plan) -> int:
    return sum(len(s.narration.split()) for s in plan.scenes)


def problems(plan: Plan, length: str = "auto") -> list[str]:
    """What a writer must fix, as sentences it can act on; empty when the plan is fine."""
    if plan.style == "song":
        return song_problems(plan)
    found: list[str] = []
    if not plan.title.strip():
        found.append("the reel has no title")
    if len(plan.scenes) < 3:
        found.append(f"a reel needs at least 3 scenes, this has {len(plan.scenes)}")
    words = MAP_WORDS if plan.style == "map" else WORDS
    low, high = words.get(length, words["auto"])
    total = narration_words(plan)
    if total < low * 0.8:
        found.append(f"the narration is {total} words; this length needs {low}-{high}")
    elif total > high * 1.2:
        found.append(f"the narration is {total} words; this length allows {low}-{high}")
    if plan.scenes and plan.scenes[0].visual.kind not in ("title", "scene"):
        found.append("scene 1 must be the hook: a title, or a story's most striking moment")
    for n, scene in enumerate(plan.scenes, 1):
        v = scene.visual
        where = f"scene {n} ({v.kind})"
        if _ASKS_TO_FOLLOW.search(scene.narration):
            found.append(f"{where}: don't ask people to follow; the ending is added after")
        if _STALE_OPENERS.search(scene.narration):
            found.append(f"{where}: no 'Did you know' or 'In this video'")
        # What is shown is what is said.
        number = _digits(v.value)
        if v.kind == "stat" and number and number not in _digits(scene.narration):
            found.append(f"{where}: say the number {v.value} in the narration")
        if v.kind == "place" and v.text and not _says(scene.narration, v.text):
            found.append(f"{where}: name {v.text} in the narration")
        if v.kind == "reveal" and v.answer and not _says(scene.narration, v.answer):
            found.append(f"{where}: say the answer ({v.answer}) in the narration")
        if len(scene.narration.split()) > 45:
            found.append(f"{where}: the narration is over 45 words; split the scene")
        needs = {
            "title": v.text,
            "stat": v.value,
            "place": v.text,
            "quote": v.text,
            "reveal": v.answer,
            "dots": v.value or v.text,
        }
        if v.kind in needs and not needs[v.kind].strip():
            found.append(f"{where}: missing its main field")
        if v.kind == "stat" and v.value and not _DIGIT.search(v.value):
            found.append(f"{where}: value must be a number, not {v.value!r}")
        if v.kind == "title" and len(v.text.split()) > 10:
            found.append(f"{where}: the headline is over 10 words")
        if v.kind == "dots" and len((v.value or v.text).replace("\n", "")) > 10:
            found.append(f"{where}: dots draw at most 10 characters")
        if v.kind == "scene" and not v.actors:
            found.append(f"{where}: needs at least one actor from the cast")
        lows = {"compare": 2, "list": 2, "timeline": 2, "icons": 1, "country": 1}
        # A country with a number badge ("How many ... every country has") may show only it.
        counted = v.kind == "country" and bool(v.value)
        if v.kind in lows and len(v.items) < lows[v.kind] and not counted:
            found.append(f"{where}: needs at least {lows[v.kind]} items")
    if plan.style == "map":
        found += map_problems(plan)
    return found


MIN_COUNTRIES = 5


def map_problems(plan: Plan) -> list[str]:
    """What a World map reel's writer must fix: enough countries, each once, each naming
    its country and every thing on its cards."""
    found: list[str] = []
    countries = [(n, s) for n, s in enumerate(plan.scenes, 1) if s.visual.kind == "country"]
    if len(countries) < MIN_COUNTRIES:
        found.append(
            f"a World map reel needs at least {MIN_COUNTRIES} countries, this has {len(countries)}"
        )
    named: set[str] = set()
    for n, scene in countries:
        v = scene.visual
        where = f"scene {n} ({v.text or 'country'})"
        name = _plain(v.text)
        if not name:
            found.append(f"scene {n}: which country is it? text is empty")
            continue
        if name in named:
            found.append(f"{where}: {v.text} comes twice; each country once")
        named.add(name)
        if not _says(scene.narration, v.text):
            found.append(f"{where}: the narration must name {v.text}")
        for item in v.items:
            if not item.text.strip():
                found.append(f"{where}: a card has no name")
            elif not _says(scene.narration, item.text):
                found.append(f"{where}: say {item.text} in the narration")
    return found


def song_problems(plan: Plan) -> list[str]:
    """What a Song story's writer must fix: one moment for every lyric line, each with a
    picture to paint."""
    found: list[str] = []
    if not plan.title.strip():
        found.append("the reel has no title")
    lines = plan.song.lines if plan.song is not None else []
    if not lines:
        found.append("the song has no lyric lines")
    if len(plan.scenes) != len(lines):
        found.append(f"write one moment for each of the {len(lines)} lines, not {len(plan.scenes)}")
    for n, scene in enumerate(plan.scenes, 1):
        if scene.visual.kind != "moment":
            found.append(f"scene {n}: a Song story scene is a moment")
        elif not scene.visual.art.strip():
            found.append(f"scene {n}: say what its picture shows (art)")
    return found
