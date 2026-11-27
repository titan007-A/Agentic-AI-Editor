"""The writer of Song story reels (style "song").

The reference page tells a story to a song's best-known part: each lyric line on screen
as it is sung, a few words big and in colour, over a painted moment of a story that shows
what the line means (masks on relatives' faces for a line about false faces, a family lit by
their phones for a line about a lonely world). The owner's pages post English and Spanish
songs: the story is set in the song's own world and the caption written in its language.
Here the planning model does two things with the part animate/songhook.py found:

1. The lines in the script the reel shows them in (Devanagari for Hindi and Urdu, however
   the lyrics were written), word for word so every word keeps its time, and the words
   drawn big with their colours.
2. The story: a few people who come back from line to line, one moment a line (a picture
   to paint and how the camera moves over it), a title and the post's caption.
"""

import json
import logging
import re

from agentic_editor.animate import songhook
from agentic_editor.animate.plan import (
    CAMERAS,
    WORD_COLORS,
    Person,
    Plan,
    Scene,
    Song,
    Visual,
    tidy,
)
from agentic_editor.llm import LLMError

log = logging.getLogger(__name__)

STYLE = "song"
MOOD = "emotional"
# The scripts the reel shows a language's lyrics in, when the lyrics came another way.
SCRIPTS = {"hi": "Devanagari", "ur": "Devanagari"}
MOST_KEYS = 3
SPLIT_OVER = 7  # words: a longer line may be shown as two


class WriterError(RuntimeError):
    pass


# ---- the lines as shown ----------------------------------------------------------------

SCRIPT_SCHEMA = {
    "type": "object",
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "words": {"type": "array", "items": {"type": "string"}},
                    "key": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "word": {"type": "integer"},
                                "color": {"type": "string", "enum": list(WORD_COLORS)},
                            },
                            "required": ["word", "color"],
                        },
                        "maxItems": MOST_KEYS,
                    },
                    "split": {"type": "integer"},
                },
                "required": ["n", "words", "key", "split"],
            },
        }
    },
    "required": ["lines"],
}

KEY_GUIDE = """Key words are drawn big and in colour, as lyric reels do: in each line pick the 1-3
words that carry its meaning and feeling (nouns, strong verbs or adjectives: never small words
like the, a, I, you, my, el, la, que, mi, tu, ye, ke, hai). Most lines have two: the first
white, the next in colour. Colours by meaning:
- red: enemies, pain, betrayal, anger, poison, blood, death, worse
- amber: love, family, faces, warmth, loyalty, the line's main thing
- blue: loneliness, cold, phones, screens, night, tears, distance
- pink: romance, longing, a beloved
- green: hope, life, growing, nature
- white: other strong words
Example (English): "I keep your letters in a drawer" -> letters white, drawer amber;
"The city lights are cold tonight" -> lights white, cold blue.
Example (Spanish): "Tu voz se quedó en mi ventana" -> voz pink, ventana amber;
"La noche me encuentra sola" -> noche blue, sola blue.
Example (Hindi): "ये रातें अब ख़ाली लगती हैं" -> रातें white, ख़ाली blue;
"तेरी यादों का मेला लगा है" -> यादों amber, मेला white."""


def script_prompt(song: Song) -> list[dict]:
    script = SCRIPTS.get(song.language)
    numbered = "\n".join(
        f"{n}. " + " ".join(f"[{k}] {w}" for k, w in enumerate(line.said.split() or
                                                               line.text.split()))
        for n, line in enumerate(song.lines, 1)
    )  # fmt: skip
    if script:
        how = (
            f"words: the line's words written in {script}, one for one: the same number of "
            f"words in the same order (a word stays one word). Spell them as the song is "
            f"printed in {script}, with nuktas and chandrabindus (वफ़ा, ज़माने, जाँ, फ़ना). "
            f"A line already in {script} is copied as it is. Write words the way lyrics are "
            f"sung and printed: ye/yeh -> ये, wo/woh -> वो, ghamon -> ग़मों, apno -> अपनों, "
            f"jaan -> जाँ or जान as sung, parayon -> परायों."
        )
    else:
        how = "words: the line's words exactly as given (same number, same order)."
    return [
        {
            "role": "user",
            "content": (
                f'Song: "{song.title}" by {song.artist or "unknown"} (language: '
                f"{song.language or 'unknown'}). These are the lines of the part a reel plays, "
                f"each word numbered from 0:\n{numbered}\n\nFor every line (n as given):\n"
                f"- {how}\n- key: its key words by number, each with a colour.\n"
                f"- split: for a line of more than {SPLIT_OVER} words, the number of the word "
                'its second half starts with, where the meaning breaks ("I walked the empty roads '
                'all night and still I could not find you" -> 7, at "and"); 0 otherwise.'
                f"\n\n{KEY_GUIDE}"
            ),
        }
    ]


def shown(llm, song: Song) -> tuple[Song, list[str]]:
    """`song` with its lines in the script they are shown in and their key words marked,
    and what didn't work out (a line whose words didn't come back one for one stays as it
    was written)."""
    try:
        reply = llm.chat_json(script_prompt(song), SCRIPT_SCHEMA, temperature=0.2)
    except LLMError as exc:
        log.warning("song lines not written out: %s", exc)
        lines = [part for line in song.lines for part in songhook.split_long(_guess_keys(line))]
        note = "the lyrics are shown as found (the writer didn't answer)"
        return song.model_copy(update={"lines": lines}), [note]
    by_n = {int(x.get("n", 0)): x for x in reply.get("lines", []) if isinstance(x, dict)}
    lines, kept = [], 0
    for n, line in enumerate(song.lines, 1):
        got = by_n.get(n) or {}
        words = [" ".join(str(w).split()) for w in got.get("words", [])]
        same_count = len(words) == len(line.words) and all(words)
        keys = {
            int(k.get("word", -1)): k.get("color", "white")
            for k in got.get("key", [])[:MOST_KEYS]
            if isinstance(k, dict)
        }
        shown_words = words if same_count else [w.text for w in line.words]
        said_words = line.said.split() or [w.text for w in line.words]
        keys = {
            k: c
            for k, c in keys.items()
            if 0 <= k < len(shown_words)
            and not _small(shown_words[k])
            and not (k < len(said_words) and _small(said_words[k]))
        }
        new_words = []
        for k, w in enumerate(line.words):
            update = {"key": k in keys, "color": keys.get(k, "") if k in keys else ""}
            if same_count:
                update["text"] = words[k]
            new_words.append(w.model_copy(update=update))
        if not same_count:
            kept += 1
        text = " ".join(w.text for w in new_words)
        if not any(w.key for w in new_words):
            changed = _guess_keys(line.model_copy(update={"words": new_words}))
            new_words = changed.words
        whole = line.model_copy(update={"words": new_words, "text": text})
        at = int(got.get("split") or 0)
        lines += songhook.split_long(whole, at if at > 0 else None)
    notes = [f"{kept} lyric lines are shown as the lyrics wrote them"] if kept else []
    return song.model_copy(update={"lines": lines}), notes


SMALL = re.compile(
    r"^(ye|yeh|wo|woh|ke|ka|ki|ko|hai|hain|pe|par|se|me|mein|main|to|tu|tum|hum|the|a|an|i|jo|"
    r"ho|bhi|hi|thay|tha|thi|"
    r"you|my|me|and|of|in|on|is|it|ये|यह|वो|वह|के|का|की|को|है|हैं|पे|पर|से|में|मैं|तो|तू|तुम|हम|थे|"
    r"था|थी|ना|न|जो|हो|भी|ही)$",
    re.I,
)


def _small(word: str) -> bool:
    return bool(SMALL.match(re.sub(r"[^\w\u0900-\u097f]+", "", word)))


def _guess_keys(line):
    """Without the writer: the longest word that isn't a small one, in amber."""
    best = max(
        (k for k, w in enumerate(line.words) if not _small(w.text)),
        key=lambda k: len(line.words[k].text),
        default=None,
    )
    words = [
        w.model_copy(update={"key": k == best, "color": "amber" if k == best else ""})
        for k, w in enumerate(line.words)
    ]
    return line.model_copy(update={"words": words})


# ---- the story -------------------------------------------------------------------------

STORY_SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "story": {"type": "string"},
        "people": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"name": {"type": "string"}, "look": {"type": "string"}},
                "required": ["name", "look"],
            },
            "maxItems": 5,
        },
        "moments": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "new_picture": {"type": "boolean"},
                    "art": {"type": "string"},
                    "camera": {"type": "string", "enum": list(CAMERAS)},
                },
                "required": ["n", "new_picture", "art", "camera"],
            },
        },
        "caption": {"type": "string"},
    },
    "required": ["title", "story", "people", "moments", "caption"],
}

GUIDE = """You direct short animated Instagram reels set to a song. The reel plays one part of the
song; each lyric line is on screen while it is sung, over one painted moment of a story. The story
shows what the lines MEAN as a scene from everyday life that makes people feel it (family, friends,
love, betrayal, loneliness, growing old...), not the words drawn literally. A visual idea that
carries a line works best: friends holding smiling masks over dark faces for "they put a face on
their face"; a family on the sofa each lit blue by a phone while the grandfather sits alone for
"the world became lonely in its phones"; an empty chair at a set table under a lamp for "you are
gone"; a girl at a rainy bus stop watching the bus pull away for "you left me waiting".

How to write it:
- people: 2-5 people who come back from moment to moment, each with a fixed look a painter can
  draw the same way every time: age, build, clothes and their colours, hair (e.g. "an old
  grandfather with a white beard, grey cardigan and glasses"; "a young woman with long dark
  hair in a red jacket"). Fit them to the song's language and world:
  - an English song: everyday life as in the US or the UK (a family home, a kitchen, a diner, a
    car at night, a city street, a school corridor), people in jeans, hoodies, jackets, dresses;
  - a Spanish song: everyday life in Latin America or Spain (a family home with a patio, a
    plaza, a market, a beach town, a barrio street at night), people dressed as people there are;
  - a Hindi song: an Indian family or friends in everyday Indian clothes (kurtas, sarees,
    salwar), homes with chai, a diya, a sofa, a photo on the wall.
  No alcohol, no smoking, nothing gory.
- moments: exactly one for each line, in order, n as the line's number. The moments follow one
  story with a beginning, a turn and an ending that lands on the last line, mostly in one or two
  places (one house, one street) so it feels like one film.
- art: what the picture shows, in plain English for an image painter: who is in it (by name from
  people), what they are doing, where, how close we are (wide, medium, close-up), and the light
  (a warm lamp, a candle, a cold phone glow, moonlight, a street light). One clear action.
  Never any writing, signs, or words in the picture.
- new_picture: false when the line goes on with the same moment as the line before (the same
  picture stays, the camera keeps moving); the first moment is always true, and most are true.
- camera: push_in (slowly closer), pull_out (slowly back), pan_left, pan_right, rise (slowly
  up), close_up (a sudden move in, for the line that hits hardest), still.
- title: a short title for the reel (3-6 words).
- story: the story in 2-3 sentences.
- caption: the post's caption, as such pages write it: 2-4 short lines telling the feeling of the
  story in the song's own language (English for an English song, Spanish for a Spanish song,
  Hinglish in Latin letters for a Hindi song), one or two emoji; then a line like "Sound on 🎧 |
  Tag someone who..." in that language; then "(Song credit: <song>, <singer>)"; then 10-15
  hashtags (the song, the singer, the story's theme, #animation #animatedreel #storytelling
  #reels). Never claim the reel was made by the singer."""


def _lines_text(song: Song) -> str:
    return "\n".join(
        f"{n}. {line.text}" + (f"  ({line.said})" if line.said and line.said != line.text else "")
        for n, line in enumerate(song.lines, 1)
    )


def story_prompt(song: Song, request: str = "", plan: Plan | None = None) -> list[dict]:
    ask = (
        f'Song: "{song.title}" by {song.artist or "unknown"} (language: '
        f"{song.language or 'unknown'}). The part the reel plays, line by line:\n"
        f"{_lines_text(song)}\n\nWrite the reel: {len(song.lines)} moments, one per line."
    )
    if song.query and "\n" in song.query:
        ask += f"\nThe person asked for: {song.query}"
    messages = [{"role": "system", "content": GUIDE}, {"role": "user", "content": ask}]
    if plan is not None:
        messages += [
            {"role": "assistant", "content": json.dumps(story_of(plan), ensure_ascii=False)},
            {"role": "user", "content": f"Change it as asked, keep the rest: {request}"},
        ]
    return messages


def story_of(plan: Plan) -> dict:
    """A Plan as the story the writer gave (for a change asked in words)."""
    song = plan.song or Song()
    return {
        "title": plan.title,
        "story": song.story,
        "people": [p.model_dump() for p in song.people],
        "moments": [
            {
                "n": n,
                "new_picture": n == 1 or s.visual.art != plan.scenes[n - 2].visual.art,
                "art": s.visual.art,
                "camera": s.visual.camera,
            }
            for n, s in enumerate(plan.scenes, 1)
        ],
        "caption": plan.caption,
    }


def _plan_from(reply: dict, song: Song, title: str) -> Plan:
    by_n = {int(m.get("n", 0)): m for m in reply.get("moments", []) if isinstance(m, dict)}
    scenes: list[Scene] = []
    art, seed = "", 0
    for n, line in enumerate(song.lines, 1):
        m = by_n.get(n, {})
        fresh = bool(m.get("new_picture", True)) or not art
        if fresh:
            art = " ".join(str(m.get("art", "")).split()) or art
        camera = m.get("camera") if m.get("camera") in CAMERAS else "push_in"
        scenes.append(
            Scene(
                narration=line.text,
                hold=0,
                visual=Visual(kind="moment", art=art, camera=camera, art_seed=seed),
            )
        )
    people = [
        Person(name=" ".join(str(p.get("name", "")).split())[:30],
               look=" ".join(str(p.get("look", "")).split())[:200])
        for p in reply.get("people", []) if isinstance(p, dict) and p.get("name")
    ]  # fmt: skip
    song = song.model_copy(
        update={"people": people[:5], "story": " ".join(str(reply.get("story", "")).split())}
    )
    return tidy(
        Plan(
            title=str(reply.get("title", "")).strip() or title,
            kind="story",
            style=STYLE,
            voice="",
            mood=MOOD,
            scenes=scenes,
            cta="",
            caption=str(reply.get("caption", "")),
            song=song,
        )
    )


def _ask(llm, messages: list[dict], song: Song, temperature: float) -> Plan:
    from agentic_editor.animate.plan import song_problems

    reply = llm.chat_json(messages, STORY_SCHEMA, temperature=temperature)
    plan = _plan_from(reply, song, song.title)
    wrong = _missing(reply, song) + song_problems(plan)
    if not wrong:
        return plan
    fix = [
        *messages,
        {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)},
        {"role": "user", "content": "Fix these and answer whole again:\n- " + "\n- ".join(wrong)},
    ]
    try:
        second = llm.chat_json(fix, STORY_SCHEMA, temperature=0.4)
    except LLMError:
        return plan
    again = _plan_from(second, song, song.title)
    return again if len(_missing(second, song) + song_problems(again)) <= len(wrong) else plan


def _missing(reply: dict, song: Song) -> list[str]:
    """The writer must give every line its moment (a missing one would show the last one's)."""
    given = {int(m.get("n", 0)) for m in reply.get("moments", []) if isinstance(m, dict)}
    wanted = set(range(1, len(song.lines) + 1))
    if given >= wanted:
        return []
    return [f"write one moment for each of the {len(wanted)} lines; these have none: "
            + ", ".join(str(n) for n in sorted(wanted - given))]  # fmt: skip


def draft(llm, song: Song) -> tuple[Plan, list[str]]:
    """The reel for a found song: its lines as shown, then its story."""
    song, notes = shown(llm, song)
    plan = _ask(llm, story_prompt(song), song, temperature=0.8)
    if song.lyrics == "heard":
        notes.append("no lyrics were found online: the words are as heard, check them")
    return plan, notes


def change(llm, plan: Plan, request: str) -> tuple[Plan, list[str]]:
    """The story changed as asked in plain words; the song, its part and words stay."""
    song = plan.song
    if song is None:
        raise WriterError("this reel has no song")
    changed = _ask(llm, story_prompt(song, request, plan), song, temperature=0.5)
    return changed.model_copy(update={"song": changed.song}), []


MOMENT_SCHEMA = STORY_SCHEMA["properties"]["moments"]["items"]


def redo_moment(llm, plan: Plan, index: int, request: str = "") -> Plan:
    """`plan` with one line's moment written again."""
    song = plan.song
    if song is None or not 0 <= index < len(plan.scenes):
        raise WriterError(f"there is no line {index + 1}")
    messages = [
        *story_prompt(song),
        {"role": "assistant", "content": json.dumps(story_of(plan), ensure_ascii=False)},
        {
            "role": "user",
            "content": (
                f"Write moment {index + 1} again (line: {song.lines[index].text})"
                + (f", as asked: {request}" if request else "")
                + ". Answer with that one moment."
            ),
        },
    ]
    reply = llm.chat_json(messages, MOMENT_SCHEMA, temperature=0.8)
    scenes = list(plan.scenes)
    visual = scenes[index].visual.model_copy(
        update={
            "art": " ".join(str(reply.get("art", "")).split()) or scenes[index].visual.art,
            "camera": reply.get("camera") if reply.get("camera") in CAMERAS else "push_in",
            "image": "",
        }
    )
    scenes[index] = scenes[index].model_copy(update={"visual": visual})
    return tidy(plan.model_copy(update={"scenes": scenes}))


def painted(plan: Plan, art: str) -> str:
    """What a moment's picture is painted from: its art with each person named in it
    written out as they look, so a person looks the same in every picture."""
    song = plan.song
    if song is None:
        return art
    out = art
    for person in sorted(song.people, key=lambda p: -len(p.name)):
        if person.name and person.look:
            pattern = re.compile(rf"\b{re.escape(person.name)}\b(?:'s)?", re.I)
            out = pattern.sub(lambda m, p=person: _as_seen(p.look, m.group(0)), out)
    return out


def _as_seen(look: str, said: str) -> str:
    return look + ("'s" if said.lower().endswith("'s") else "")
