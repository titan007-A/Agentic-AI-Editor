"""When everything happens: scenes, words, visual beats, subtitles and sound effects.

The single source of timing. Each scene lasts its own voice plus a hold; a visual's
items land on the words that name them when they are spoken (a stat on its digits, a
timeline row on its year), else spread over the narration. The player and the sound
both read the times worked out here, so picture, words and sound agree to the frame.
"""

import re
from dataclasses import dataclass, field

from agentic_editor.animate.plan import Plan, Scene
from agentic_editor.transcript import Word

LEAD = 0.18  # silence before each scene's voice
FIRST_LEAD = 0.12
TAIL = 0.7  # after the last scene
# The least a visual needs on screen to play out, voice or not.
MIN_SCENE = {
    "stat": 2.8, "reveal": 3.2, "dots": 3.0, "cta": 2.8, "timeline": 2.6, "compare": 2.8,
    "scene": 2.6, "country": 2.4,
}  # fmt: skip
# A country's cards are all seen once more as the camera pulls back before it flies on.
EXTRA_HOLD = {"country": 0.45}
CARD_GAP = 0.55  # the least time between two cards of a country (the camera moves between)
# How long a character takes to come on (player.js ENTRANCES); its steps are heard meanwhile.
ENTRANCES = {"walk_in": (1.6, 0.42), "run_in": (0.9, 0.24)}  # (seconds, a step every)
WALK_OUT = 1.6
NOTE_FIRST = 2.0  # how long a note shows before the lines it has no room beside
CHUNK_WORDS = 3
CHUNK_CHARS = 18
COUNT = 1.3  # seconds a stat counts up (player.js stat)


@dataclass
class Spoken:
    """One scene's voice: its file, length and words (times from its own start)."""

    path: str
    duration: float
    words: list[Word]


@dataclass
class SceneTime:
    kind: str
    start: float
    end: float
    voice_start: float
    voice_end: float
    words: list[Word]  # absolute times
    beats: list[float]
    # Two actors with a note and a line: no room for both over their heads, so the note shows
    # first and the lines (bubble, thought, their pop) wait until here.
    talk_from: float | None = None


@dataclass
class Caption:
    start: float
    end: float
    words: list[Word]


@dataclass
class Cue:
    t: float
    sound: str  # sound.SFX name
    gain_db: float = -20.0


@dataclass
class Timeline:
    scenes: list[SceneTime]
    duration: float
    captions: list[Caption] = field(default_factory=list)
    cues: list[Cue] = field(default_factory=list)


def plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _digits(text: str) -> str:
    return re.sub(r"\D", "", text)


def _find(words: list[Word], target: str, after: float = -1.0) -> float | None:
    """When the first significant word of `target` is spoken (at or after `after`)."""
    keys = [plain(w) for w in target.split() if len(plain(w)) > 2] or [
        plain(w) for w in target.split() if plain(w)
    ]
    if not keys:
        return None
    for key in keys[:2]:
        for w in words:
            if w.start >= after and plain(w.text) == key:
                return w.start
        for w in words:  # "city's" for "city"
            if w.start >= after and plain(w.text).startswith(key) and len(key) > 3:
                return w.start
    return None


def _find_number(words: list[Word], value: str) -> float | None:
    digits = _digits(value)
    if not digits:
        return None
    for w in words:
        if (
            digits
            and _digits(w.text)
            and (_digits(w.text) == digits or digits.startswith(_digits(w.text)))
        ):
            return w.start
    return None


def _spread(n: int, a: float, b: float) -> list[float]:
    if n <= 0:
        return []
    if n == 1:
        return [a]
    return [a + (b - a) * i / (n - 1) for i in range(n)]


def _ordered(times: list[float | None], fallback: list[float], gap: float = 0.25) -> list[float]:
    """Matched times where found, the fallback elsewhere; an unmatched item goes before the
    next matched one rather than pushing it late, and each comes at least `gap` after the last."""
    floor = (fallback[0] - 0.2) if fallback else 0.0
    out: list[float] = []
    for i, t in enumerate(times):
        if t is None:
            chosen = fallback[i]
            later = next((x for x in times[i + 1 :] if x is not None), None)
            if later is not None:
                chosen = max(min(chosen, later - gap), floor)
        else:
            chosen = t
        if out:
            chosen = max(chosen, out[-1] + gap)
        out.append(chosen)
    return out


def beats(
    scene: Scene, start: float, voice_start: float, voice_end: float, words: list[Word]
) -> list[float]:
    """The times player.js uses for this scene's items (one list per kind, see SCENES)."""
    v = scene.visual
    span = max(0.6, voice_end - voice_start)
    early = voice_start + 0.05
    kind = v.kind
    if kind == "title":
        head = [plain(w) for w in v.text.split()]
        times: list[float | None] = []
        cursor = -1.0
        for key in head:
            hit = next((w.start for w in words if w.start > cursor and plain(w.text) == key), None)
            if hit is not None:
                cursor = hit
            times.append(hit)
        fallback = [start + 0.12 + i * 0.14 for i in range(len(head))]
        # The hook is read, not waited for: every word is up within a beat of its turn.
        return [
            min(t, fallback[i] + 0.05) if t is not None else fallback[i]
            for i, t in enumerate(times)
        ]
    if kind == "stat":
        at = _find_number(words, v.value)
        at = at - 0.1 if at is not None else voice_start + 0.25 * span
        at = max(at, start + 0.2)
        return [at, at + COUNT * 0.55]
    if kind == "icons":
        n = max(1, len(v.items))
        items = [early + 0.45 * i for i in range(n)]
        return [*items, items[-1] + 0.4]
    if kind == "compare":
        left = _find(words, v.items[0].text) if v.items else None
        right = _find(words, v.items[1].text, (left or 0) + 0.1) if len(v.items) > 1 else None
        return _ordered([left, right], [early, voice_start + span * 0.5], gap=0.6)
    if kind == "list":
        n = len(v.items)
        found = [_find(words, i.text) for i in v.items]
        fallback = _spread(n, voice_start + span * 0.15, voice_start + span * 0.8)
        return [start + 0.05, *_ordered(found, fallback, gap=0.35)]
    if kind == "timeline":
        found = [_find_number(words, i.year) or _find(words, i.text) for i in v.items]
        fallback = _spread(len(v.items), early, voice_start + span * 0.8)
        return _ordered(found, fallback, gap=0.45)
    if kind == "place":
        name = _find(words, v.text)
        drop = max(start + 0.15, (name - 0.6) if name is not None else early)
        return [drop, max(name if name is not None else drop + 0.5, drop + 0.4)]
    if kind == "reveal":
        answer = _find(words, v.answer, voice_start + span * 0.25)
        answer = answer if answer is not None else voice_start + span * 0.6
        return [start + 0.1, max(answer, start + 0.9)]
    if kind == "dots":
        at = _find_number(words, v.value) or _find(words, v.value or v.text)
        at = max(start + 0.15, (at - 0.3) if at is not None else early)
        return [at, at + 1.0]
    if kind == "country":
        # The country lights up as it is named; each card pops as its name is said.
        named = _find(words, v.text)
        first = max(start + 0.05, (named - 0.05) if named is not None else early)
        found: list[float | None] = []
        cursor = first
        for item in v.items:
            at = _find(words, item.text, cursor)
            found.append(at)
            cursor = at + 0.05 if at is not None else cursor
        fallback = _spread(len(v.items), voice_start + span * 0.3, voice_start + span * 0.9)
        cards = _ordered(found, fallback, gap=CARD_GAP)
        return [first, *[max(t, first + 0.35) for t in cards]]
    if kind == "scene":
        # Each actor comes on in turn; the bubble opens as its line is said (or the voice starts).
        starts = [start + 0.1 + 0.25 * i for i in range(max(1, len(v.actors)))]
        line = next((a.says for a in v.actors if a.says), "")
        said = _find(words, line) if line else None
        bubble = max(voice_start + 0.2, said - 0.1) if said is not None else voice_start + 0.2
        return [*starts, bubble]
    return []  # quote: words spread over the narration by the player


def captions(scenes: list[SceneTime]) -> list[Caption]:
    """Subtitles: up to three words at a time, broken after punctuation, each shown from
    its first word until the next chunk (or a little after its last word in a pause)."""
    out: list[Caption] = []
    for scene in scenes:
        chunk: list[Word] = []
        chunks: list[list[Word]] = []
        for w in scene.words:
            chunk.append(w)
            chars = sum(len(x.text) for x in chunk)
            if len(chunk) >= CHUNK_WORDS or chars >= CHUNK_CHARS or re.search(r"[.!?,;:]$", w.text):
                chunks.append(chunk)
                chunk = []
        if chunk:
            chunks.append(chunk)
        for i, c in enumerate(chunks):
            start = max(scene.start, c[0].start - 0.05)
            last = c[-1].end + 0.25
            nxt = chunks[i + 1][0].start - 0.05 if i + 1 < len(chunks) else None
            end = min(nxt, last + 0.4) if nxt is not None else min(last, scene.end)
            out.append(Caption(start, max(end, start + 0.2), c))
    return out


STOP = {"a", "an", "and", "the", "of", "in", "on", "to", "by", "for", "or", "with", "its", "his"}


def _phrase_at(words: list[Word], i: int, phrases: list[list[str]]) -> int:
    """How many words from `i` make one of `phrases` (as plain words), the longest; 1 when
    none does."""
    best = 1
    for phrase in phrases:
        n = len(phrase)
        if n > best and [plain(w.text) for w in words[i : i + n]] == phrase:
            best = n
    return best


def word_captions(scenes: list[SceneTime], plan: Plan) -> list[Caption]:
    """Subtitles one word at a time, as World map reels show them: a name said in several
    words (a country, a thing on a card) stays together, and so does a number with the
    word it counts ("7 Oscars"). Each shows until the next one starts."""
    out: list[Caption] = []
    visuals = [s.visual for s in plan.scenes]
    for n, scene in enumerate(scenes):
        v = visuals[n] if n < len(visuals) else None
        names = (
            [v.text, *(i.text for i in v.items)] if v is not None and v.kind == "country" else []
        )
        phrases = [p for p in ([plain(w) for w in name.split() if plain(w)] for name in names) if p]
        chunks: list[list[Word]] = []
        i = 0
        words = scene.words
        while i < len(words):
            size = _phrase_at(words, i, phrases)
            if size == 1 and i + 1 < len(words) and _digits(words[i].text):
                after = plain(words[i + 1].text)
                if after.isalpha() and len(after) > 2 and after not in STOP:
                    size = 2
            chunks.append(words[i : i + size])
            i += size
        for k, chunk in enumerate(chunks):
            start = max(scene.start, chunk[0].start - 0.03)
            nxt = chunks[k + 1][0].start - 0.03 if k + 1 < len(chunks) else None
            end = nxt if nxt is not None else min(chunk[-1].end + 0.6, scene.end)
            out.append(Caption(start, max(end, start + 0.12), chunk))
    return out


def _scene_cues(s: SceneTime, visual) -> list[Cue]:
    out: list[Cue] = []
    for actor, at in zip(visual.actors, s.beats, strict=False):
        if actor.action in ENTRANCES:
            length, every = ENTRANCES[actor.action]
            t = at + 0.1
            while t < at + length:
                out.append(Cue(t, "step", -27.0))
                t += every
        elif actor.action == "walk_out":
            t = max(at, s.end - WALK_OUT)
            while t < s.end:
                out.append(Cue(t, "step", -29.0))
                t += 0.42
        elif actor.action == "celebrate":
            out.append(Cue(at + 0.3, "chime", -26.0))
        elif actor.action == "shocked":
            out.append(Cue(at + 0.15, "pop", -20.0))
    if any(a.says for a in visual.actors):
        out.append(Cue(max(s.beats[-1], s.talk_from or 0.0), "pop", -23.0))
    return out


def cues(plan: Plan, scenes: list[SceneTime]) -> list[Cue]:
    """Sound effects on the same beats the picture uses."""
    out: list[Cue] = [Cue(0.0, "boom", -20.0)]
    visuals = [sc.visual for sc in plan.scenes]
    for i, s in enumerate(scenes):
        visual = visuals[i] if i < len(visuals) else None
        if i:
            out.append(Cue(max(0.0, s.start - 0.14), "whoosh", -21.0))
        b = s.beats
        if s.kind == "stat" and b:
            t, step = b[0], 0.05
            while t < b[0] + COUNT:
                out.append(Cue(t, "tick", -27.0))
                step *= 1.18
                t += step
            out.append(Cue(b[0] + COUNT, "ding", -22.0))
        elif s.kind in ("icons", "list", "compare", "timeline", "country") and b:
            first = s.kind in ("list", "country")
            items = b[1:] if first else b[: len(b) - 1] if s.kind == "icons" else b
            out += [Cue(t, "pop", -22.0) for t in items]
        elif s.kind == "place" and b:
            out.append(Cue(b[0] + 0.45, "drop", -18.0))
        elif s.kind == "reveal" and len(b) > 1:
            out.append(Cue(max(s.start, b[1] - 1.3), "riser", -22.0))
            out.append(Cue(b[1], "boom", -19.0))
        elif s.kind == "dots" and b:
            out.append(Cue(b[0], "shimmer", -22.0))
        elif s.kind == "cta" and b:
            out.append(Cue(b[0], "chime", -18.0))
        elif s.kind == "scene" and b and visual is not None:
            out += _scene_cues(s, visual)
    return sorted(out, key=lambda c: c.t)


def build(plan: Plan, voices: list[Spoken]) -> Timeline:
    """`voices`: one per scene, then one for the call to action at the end."""
    scenes = [*plan.scenes, Scene(narration=plan.cta, visual={"kind": "title"})]
    times: list[SceneTime] = []
    at = 0.0
    for i, (scene, spoken) in enumerate(zip(scenes, voices, strict=True)):
        kind = "cta" if i == len(scenes) - 1 else scene.visual.kind
        voice_start = at + (FIRST_LEAD if i == 0 else LEAD)
        voice_end = voice_start + spoken.duration
        hold = scene.hold + EXTRA_HOLD.get(kind, 0.0)
        end = max(voice_end + hold, at + MIN_SCENE.get(kind, 1.6))
        words = [
            Word(text=w.text, start=voice_start + w.start, end=voice_start + w.end)
            for w in spoken.words
        ]
        if kind == "cta":  # the Follow button is tapped as the voice ends
            b = [max(at + 0.7, voice_end - 0.45)]
        else:
            b = beats(scene, at, voice_start, voice_end, words)
        talk_from = None
        v = scene.visual
        if (
            kind == "scene"
            and v.detail
            and len(v.actors) >= 2
            and any(a.says or a.action == "think" for a in v.actors[:2])
        ):
            talk_from = min(at + NOTE_FIRST, end - 1.2)
        times.append(SceneTime(kind, at, end, voice_start, voice_end, words, b, talk_from))
        at = end
    timeline = Timeline(times, at + TAIL)
    timeline.captions = word_captions(times, plan) if plan.style == "map" else captions(times)
    timeline.cues = cues(plan, times)
    return timeline


def player_data(plan: Plan, timeline: Timeline) -> dict:
    """What player.js load() takes."""
    visuals = [s.visual.model_dump() for s in plan.scenes] + [{"kind": "cta", "text": plan.cta}]
    cast = [c.model_dump() for c in plan.cast]
    word = lambda w: {"text": w.text, "start": round(w.start, 3), "end": round(w.end, 3)}  # noqa: E731
    return {
        "style": plan.style,
        "title": plan.title,
        "duration": round(timeline.duration, 3),
        "header": {
            "start": round(timeline.scenes[1].start, 3) if len(timeline.scenes) > 2 else 9999
        },
        "cast": cast,
        "scenes": [
            {
                "kind": s.kind,
                "start": round(s.start, 3),
                "end": round(s.end, 3),
                "voiceStart": round(s.voice_start, 3),
                "voiceEnd": round(s.voice_end, 3),
                "beats": [round(b, 3) for b in s.beats],
                "talkFrom": round(s.talk_from, 3) if s.talk_from is not None else None,
                "words": [word(w) for w in s.words],
                "visual": visual,
            }
            for s, visual in zip(timeline.scenes, visuals, strict=True)
        ],
        "captions": [
            {
                "start": round(c.start, 3),
                "end": round(c.end, 3),
                "words": [word(w) for w in c.words],
            }
            for c in timeline.captions
        ],
    }
