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
STYLES = ("glow", "paper", "story", "kinetic", "stick")
MOODS = ("mysterious", "uplifting", "emotional", "tense", "curious")
LENGTHS = ("auto", "short", "medium", "long")
# Narration words a reel of each length runs to (Kokoro speaks about 2.6 words a second).
WORDS = {"short": (50, 85), "medium": (90, 150), "long": (155, 230), "auto": (70, 130)}

Kind = Literal["facts", "lesson", "story", "news"]
Style = Literal["glow", "paper", "story", "kinetic", "stick"]
Mood = Literal["mysterious", "uplifting", "emotional", "tense", "curious"]
VisualKind = Literal[
    "title", "stat", "icons", "compare", "list", "timeline", "place", "quote", "reveal", "dots",
    "scene",
]  # fmt: skip
# Character scenes (player.js character rig): where, who looks like what, what they do.
SETTINGS = ("room", "street", "park", "office", "night", "school", "shop", "stage", "plain")
ACTIONS = (
    "idle", "talk", "walk_in", "walk_out", "run_in", "wave", "point", "think", "celebrate",
    "sad", "shocked", "angry", "shrug", "sit", "read", "give", "cry",
)  # fmt: skip
FEELINGS = ("neutral", "happy", "sad", "angry", "surprised", "scared", "thinking")
LOOKS = ("man", "woman", "boy", "girl", "old man", "old woman")
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

MOTIONS = ("pop", "float", "walk_left", "walk_right", "shake", "spin", "grow", "fall", "pulse")
DEFAULT_EMOJI = {"icons": "✨", "compare": "❓", "place": "📍", "reveal": "💡", "list": ""}
MAX_SCENES = 16
CTA = "Follow for more."


class Loose(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Item(Loose):
    emoji: str = ""
    text: str = ""
    year: str = ""
    value: str = ""
    motion: str = "pop"


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


class Scene(Loose):
    narration: str
    visual: Visual = Field(default_factory=Visual)
    hold: float = Field(default=0.35, ge=0, le=3)


class Source(Loose):
    title: str = ""
    url: str = ""


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


# ---- mending ---------------------------------------------------------------------------

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
        cap = {"list": 5, "timeline": 5, "icons": 3, "compare": 2}.get(v.kind, 5)
        items = [
            Item(
                emoji=_emoji(i.emoji, DEFAULT_EMOJI.get(v.kind, "")),
                text=_one_line(i.text, 70),
                year=_one_line(i.year, 16),
                value=_one_line(i.value, 16),
                motion=i.motion if i.motion in MOTIONS else "pop",
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
        text = _one_line(v.text, 200 if v.kind == "quote" else 90)
        highlight = v.highlight.strip()
        if highlight and highlight.lower() not in text.lower():
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
            }
        )
        narration = " ".join(scene.narration.split())
        # A scene that only asks people to follow: the system's own ending does that.
        if _ASKS_TO_FOLLOW.search(narration) and len(narration.split()) <= 12:
            continue
        if narration:
            scenes.append(scene.model_copy(update={"narration": narration, "visual": visual}))
    return plan.model_copy(
        update={
            "title": _one_line(plan.title, 70),
            "scenes": scenes,
            "cast": _cast(plan.cast, scenes),
            "cta": _one_line(plan.cta or CTA, 60),
            "caption": plan.caption.strip()[:2200],
        }
    )


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
    found: list[str] = []
    if not plan.title.strip():
        found.append("the reel has no title")
    if len(plan.scenes) < 3:
        found.append(f"a reel needs at least 3 scenes, this has {len(plan.scenes)}")
    low, high = WORDS.get(length, WORDS["auto"])
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
        lows = {"compare": 2, "list": 2, "timeline": 2, "icons": 1}
        if v.kind in lows and len(v.items) < lows[v.kind]:
            found.append(f"{where}: needs at least {lows[v.kind]} items")
    return found
