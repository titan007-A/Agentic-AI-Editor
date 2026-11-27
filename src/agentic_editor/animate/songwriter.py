"""The writer of Song story reels (style "song").

The reference page tells a story to a song's best-known part: each lyric line on screen
as it is sung, a few words big and in colour, over a painted moment of a story that shows
what the line means (masks on relatives' faces for a line about false faces, a family lit by
their phones for a line about a lonely world). The owner's pages post English and Spanish
songs: the story is set in the song's own world and the caption written in its language.
Here the planning model does two things with the part animate/songhook.py found:

1. The lines in the script the reel shows them in (Devanagari for Hindi and Urdu, however
   the lyrics were written), word for word so every word keeps its time, the words drawn
   big with their colours, where each line breaks into its two rows on screen, and now and
   then a word meaning a movement whose letters act it out (fall, rise, shake...).
2. The story: a few people who come back from line to line, a moment for each picture (a
   picture to paint and how the camera moves over it: one a line, two for a line on screen
   longer than LONG_PICTURE), a title and the post's caption. A reel joined from two parts
   of the song gets one story across both.
"""

import json
import logging
import re
import unicodedata

from agentic_editor.animate import songhook
from agentic_editor.animate.plan import (
    CAMERAS,
    MAX_SCENES,
    WORD_COLORS,
    WORD_EFFECTS,
    Person,
    Plan,
    Scene,
    Song,
    SongLine,
    Visual,
    scene_lines,
    sung_spans,
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
# A word's letters act out its movement in at most one line in this many, never in two lines
# running, and only in a word of this many letters or more (the reference does it twice in
# a 29 s reel).
EFFECT_EVERY = 4
EFFECT_LETTERS = 3
# A line on screen longer than this (s) gets a second picture partway (a scene each).
LONG_PICTURE = 3.0
# At most one moment in this many goes on with the picture before (the owner wants a new
# picture far more often than not).
KEEP_EVERY = 5


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
                                "effect": {"type": "string", "enum": ["none", *WORD_EFFECTS]},
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
white, the next in colour. White and amber are the reel's main pair: the coloured word is
amber unless its meaning is plainly one of the others. Colours by meaning:
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

EFFECT_GUIDE = """Now and then a key word means a movement, and its letters act it out on screen
while it is sung: effect fall (falling, dropping, sinking, crashing down: the letters tumble
down out of the line), rise (rising, flying, floating, lifting up: they float up), shake
(shaking, trembling, shivering: they tremble), scatter (breaking, shattering, falling apart,
scattering: they fly apart), spin (turning, spinning, whirling: they turn). Give a key word an
effect only when the word itself means that movement, at most one word in a line, in a few
lines of the part at most, never two lines running; every other key word has effect none.
Example: "We fall like rain on a paper town" -> fall white with effect fall, town amber;
"Mis manos tiemblan cuando llamas" -> manos amber, tiemblan white with effect shake."""


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
                f"- {how}\n- key: its key words by number, each with a colour (and an "
                "effect, mostly none).\n"
                "- split: each line is set on screen as two rows: the number of the word its "
                'second row starts with, where its phrase breaks ("Hold the light for me" -> 3, '
                'at "for"; "I walked the empty roads all night and still I could not find you" '
                f'-> 7, at "and": a line of more than {SPLIT_OVER} words is shown as two lines '
                "there); 0 for a line of one or two words."
                f"\n\n{KEY_GUIDE}\n\n{EFFECT_GUIDE}"
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
        given = [k for k in got.get("key", [])[:MOST_KEYS] if isinstance(k, dict)]
        keys = {int(k.get("word", -1)): k.get("color", "white") for k in given}
        effects = {int(k.get("word", -1)): str(k.get("effect") or "") for k in given}
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
            update = {
                "key": k in keys,
                "color": keys.get(k, "") if k in keys else "",
                "effect": effects.get(k, "") if k in keys else "",
            }
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
        at = _number(got.get("split"))
        parts = songhook.split_long(whole, at if at > 0 else None)
        if len(parts) == 1 and 0 < at < len(new_words):
            # Short enough to stay one line: the writer's break is where its rows break.
            parts = [whole.model_copy(update={"row": at})]
        lines += parts
    notes = [f"{kept} lyric lines are shown as the lyrics wrote them"] if kept else []
    return song.model_copy(update={"lines": moving(lines)}), notes


def _number(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


# The words (English and Spanish) that mean each movement: a word the writer marks keeps
# its effect only when it is one of them (the writer gave "hurts" a tremble and, two
# lines on, a scatter), under the effect it means if the writer named another.
MOVES = {
    "fall": r"fall(s|ing|en)?|fell|drop(s|ped|ping)?|sink(s|ing)?|sank|sunk|crash(es|ed|ing)?"
    r"|tumbl\w*|collaps\w*|plung\w*|descend\w*|ca(er|igo|es|e|emos|en|i|yo|yeron|yendo|ida"
    r"|ido|ia|ias)|hund\w*|derrumb\w*|desplom\w*",
    "rise": r"ris(e|es|ing|en)|rose|fl(y|ies|ew|ying|own)|float\w*|lift\w*|soar\w*|climb\w*"
    r"|ascend\w*|rais(e|es|ed|ing)|sub(e|o|es|en|ir|imos|iendo|io)|vol(ar|ando|amos)"
    r"|vuel(o|a|as|an)|elev\w*|flot\w*|alz\w*|levant\w*|ascien\w*|despeg\w*",
    "shake": r"shak(e|es|ing|en)|shook|trembl\w*|shiver\w*|quak\w*|quiver\w*|shudder\w*"
    r"|tiembl\w*|tembl\w*|estremec\w*|sacud\w*|vibr\w*",
    "scatter": r"scatter\w*|shatter\w*|break(s|ing)?|broke|broken|burst\w*|explod\w*"
    r"|crumbl\w*|splinter\w*|romp\w*|rot[oa]s?|quiebr\w*|quebr\w*|estall\w*|destroz\w*"
    r"|deshac\w*|pedazos?|anicos",
    "spin": r"spin(s|ning)?|spun|turn(s|ed|ing)?|twirl\w*|whirl\w*|rotat\w*|revolv\w*"
    r"|danc\w*|gir(a|as|an|o|ar|ando|amos|aba)|vuelta|bail(a|as|an|o|ar|ando|amos|emos)",
}
_MOVES = {effect: re.compile(rf"^({words})$") for effect, words in MOVES.items()}


def moves(word: str, effect: str) -> str:
    """The effect `word` acts out: `effect` when the word means that movement, else the
    movement it does mean, else none."""
    if effect not in WORD_EFFECTS:
        return ""
    bare = unicodedata.normalize("NFKD", word.lower())
    bare = re.sub(r"[^a-z]", "", "".join(c for c in bare if not unicodedata.combining(c)))
    if _MOVES[effect].match(bare):
        return effect
    return next((other for other, said in _MOVES.items() if said.match(bare)), "")


def moving(lines: list[SongLine]) -> list[SongLine]:
    """The lines with the letter effects the writer gave kept only as the reference uses
    them: on a key word of EFFECT_LETTERS letters or more that means a movement (MOVES),
    one word a line, never in two lines running, in one line of EFFECT_EVERY at most (the
    first ones given)."""
    most = max(1, len(lines) // EFFECT_EVERY)
    out, used, last = [], 0, -2
    for n, line in enumerate(lines):
        mover = next(
            (k for k, w in enumerate(line.words)
             if w.key and moves(w.text, w.effect)
             and len(re.findall(r"\w", w.text)) >= EFFECT_LETTERS),
            None,
        )  # fmt: skip
        if mover is not None and (used >= most or n - last < 2):
            mover = None
        if mover is not None:
            used, last = used + 1, n
        if mover is None and not any(w.effect for w in line.words):
            out.append(line)
            continue
        words = [w.model_copy(update={"effect": moves(w.text, w.effect) if k == mover else ""})
                 for k, w in enumerate(line.words)]  # fmt: skip
        out.append(line.model_copy(update={"words": words}))
    return out


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
- moments: exactly the moments listed, in order, n as the moment's number: one for each line,
  two for a line marked with a first and a second picture (two beats of the action: a new
  angle, a closer shot, what happens next). The moments follow one story with a beginning, a
  turn and an ending that lands on the last line, mostly in one or two places (one house, one
  street) so it feels like one film.
- art: what the picture shows, in plain English for an image painter: who is in it, by their
  names exactly as in people ("Dad lifts Mia onto his shoulders", never "the father lifts the
  girl": the painter draws each name as that person, the same in every picture), what they are
  doing, where, how close we are (wide, medium, close-up), and the light (a warm lamp, a candle,
  a cold phone glow, moonlight, a street light). One clear action. Never any writing, signs, or
  words in the picture.
- new_picture: true for nearly every moment: every moment is a new painted picture, so the reel
  moves on as the song does. False only now and then (at most one moment in five), when the
  very same shot must stay while the camera keeps moving. The first moment, and a line's second
  picture, are always true.
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


def _moments_text(song: Song) -> str:
    """The moments the writer gives, in order: which line each shows (and which of its two
    pictures)."""
    which, out = scene_lines(song), []
    for n, k in enumerate(which, 1):
        half = ""
        if song.lines[k].pictures >= 2:
            first = which.index(k) == n - 1
            half = " (first picture)" if first else " (second picture: a new one)"
        out.append(f"moment {n}: line {k + 1}{half}")
    return "\n".join(out)


def story_prompt(
    song: Song, request: str = "", plan: Plan | None = None, cast: list[Person] | None = None
) -> list[dict]:
    """What the writer is asked for a story; `cast`: the people of the song's other reels,
    kept so they look the same in every reel of it (their portraits are painted once)."""
    count = len(scene_lines(song))
    joined = ""
    if len(song.pieces) > 1:
        cut = next((k for k, line in enumerate(song.lines) if line.piece > 0), len(song.lines))
        joined = (
            f"\nThe reel joins two parts of the song: lines 1-{cut} are one part, lines "
            f"{cut + 1}-{len(song.lines)} another. Tell ONE story across both: the same people, "
            "the second part going on from the first."
        )
    ask = (
        f'Song: "{song.title}" by {song.artist or "unknown"} (language: '
        f"{song.language or 'unknown'}). The part the reel plays, line by line:\n"
        f"{_lines_text(song)}{joined}\n\nWrite the reel: {count} moments, a picture each, in "
        f"this order:\n{_moments_text(song)}"
    )
    if song.query and "\n" in song.query:
        ask += f"\nThe person asked for: {song.query}"
    known = [p for p in cast or [] if p.name and p.look]
    if known:
        ask += (
            "\n\nThe song's other reels have these people:\n"
            + "\n".join(f"- {p.name}: {p.look}" for p in known)
            + "\nTell this part's story with them: keep each one's name and look word for "
            "word (the painter draws them from it), leave out anyone this part doesn't need, "
            "and add someone new only if the story needs them."
        )
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
        "people": [{"name": p.name, "look": p.look} for p in song.people],
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


def _plan_from(reply: dict, song: Song, title: str, cast: list[Person] | None = None) -> Plan:
    """The Plan from the writer's story: a scene for each moment. A moment goes on with
    the picture before only where the writer said so, at most one in KEEP_EVERY, never as a
    line's second picture. Someone of `cast` (the song's other reels) keeps their look as it
    was, word for word, whatever the writer wrote, so their portrait is the same one."""
    by_n = {int(m.get("n", 0)): m for m in reply.get("moments", []) if isinstance(m, dict)}
    scenes: list[Scene] = []
    art, seed = "", 0
    which = scene_lines(song)
    kept, may_keep = 0, max(1, len(which) // KEEP_EVERY)
    for n, k in enumerate(which, 1):
        m = by_n.get(n, {})
        own = " ".join(str(m.get("art", "")).split())
        second = n > 1 and which[n - 2] == k
        keep = bool(art) and not m.get("new_picture", True) and not second and kept < may_keep
        if keep or not own:
            kept += int(keep)
        else:
            art = own
        camera = m.get("camera") if m.get("camera") in CAMERAS else "push_in"
        scenes.append(
            Scene(
                narration=song.lines[k].text,
                hold=0,
                visual=Visual(kind="moment", art=art, camera=camera, art_seed=seed),
            )
        )
    people = [
        Person(name=" ".join(str(p.get("name", "")).split())[:30],
               look=" ".join(str(p.get("look", "")).split())[:200])
        for p in reply.get("people", []) if isinstance(p, dict) and p.get("name")
    ]  # fmt: skip
    known = {p.name.casefold(): p for p in cast or [] if p.name and p.look}
    people = [
        known[p.name.casefold()].model_copy(update={"image": ""})
        if p.name.casefold() in known
        else p
        for p in people
    ]
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


def _ask(
    llm, messages: list[dict], song: Song, temperature: float, cast: list[Person] | None = None
) -> Plan:
    from agentic_editor.animate.plan import song_problems

    reply = llm.chat_json(messages, STORY_SCHEMA, temperature=temperature)
    plan = _plan_from(reply, song, song.title, cast)
    wrong = _missing(reply, song) + song_problems(plan) + _unnamed(plan)
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
    again = _plan_from(second, song, song.title, cast)
    still = _missing(second, song) + song_problems(again) + _unnamed(again)
    return again if len(still) <= len(wrong) else plan


# Words that say someone is in a picture.
SOMEONE = re.compile(
    r"\b(man|woman|boy|girl|men|women|boys|girls|father|mother|dad|mom|mum|son|daughter|child|"
    r"kid|teenager|teen|grandfather|grandmother|grandpa|grandma|husband|wife|he|she|his|her|him|"
    r"person)\b",
    re.I,
)


def _unnamed(plan: Plan) -> list[str]:
    """Moments with someone in them who isn't named from people (nor the father, the boy, of
    a story with one father and one boy): they would be painted unlike themselves."""
    song = plan.song
    named = [p.name for p in song.people if p.name and p.look] if song is not None else []
    if not named:
        return []
    seen, out = set(), []
    for n, scene in enumerate(plan.scenes, 1):
        art = scene.visual.art
        if art in seen:
            continue
        seen.add(art)
        if SOMEONE.search(art) and not people_in(plan, art):
            out.append(n)
    if not out:
        return []
    return [
        "moments " + ", ".join(str(n) for n in out) + ": name who is in each picture by their "
        "names from people (" + ", ".join(named) + "), never 'the father' or 'a boy'"
    ]


def _missing(reply: dict, song: Song) -> list[str]:
    """The writer must give every picture its moment (a missing one would show the last
    one's)."""
    given = {int(m.get("n", 0)) for m in reply.get("moments", []) if isinstance(m, dict)}
    wanted = set(range(1, len(scene_lines(song)) + 1))
    if given >= wanted:
        return []
    return [f"write one moment for each of the {len(wanted)} moments listed; these have none: "
            + ", ".join(str(n) for n in sorted(wanted - given))]  # fmt: skip


def pictured(song: Song) -> Song:
    """`song` with each line's pictures: two for a line on screen longer than LONG_PICTURE
    (the longest first, while the reel has scenes to spare), one for the rest."""
    lengths = [b - a for a, b in sung_spans(song)]
    room = max(0, MAX_SCENES - len(song.lines))
    long_ones = sorted((k for k, t in enumerate(lengths) if t > LONG_PICTURE),
                       key=lambda k: -lengths[k])[:room]  # fmt: skip
    return song.model_copy(
        update={
            "lines": [
                line.model_copy(update={"pictures": 2 if k in long_ones else 1})
                for k, line in enumerate(song.lines)
            ]
        }
    )


def draft(llm, song: Song) -> tuple[Plan, list[str]]:
    """The reel for a found song: its lines as shown, how many pictures each gets, then its
    story, with the people `song` already has (another part of a song a reel was made to)
    kept as they look."""
    cast = list(song.people)
    song, notes = shown(llm, song)
    song = pictured(song)
    plan = _ask(llm, story_prompt(song, cast=cast), song, temperature=0.8, cast=cast)
    if song.lyrics == "heard":
        notes.append("no lyrics were found online: the words are as heard, check them")
    return plan, notes


def change(llm, plan: Plan, request: str) -> tuple[Plan, list[str]]:
    """The story changed as asked in plain words; the song, its part and words stay."""
    song = plan.song
    if song is None:
        raise WriterError("this reel has no song")
    changed = _ask(llm, story_prompt(song, request, plan), song, temperature=0.5)
    # Someone who looks as before keeps the portrait asked for again, if it was.
    again = {(p.name.casefold(), p.look): p.art_seed for p in song.people}
    people = [
        p.model_copy(update={"art_seed": again.get((p.name.casefold(), p.look), 0)})
        for p in changed.song.people
    ]
    return changed.model_copy(
        update={"song": changed.song.model_copy(update={"people": people})}
    ), []


MOMENT_SCHEMA = STORY_SCHEMA["properties"]["moments"]["items"]


def redo_moment(llm, plan: Plan, index: int, request: str = "") -> Plan:
    """`plan` with one line's moment written again."""
    song = plan.song
    which = scene_lines(song) if song is not None else []
    if song is None or not 0 <= index < min(len(plan.scenes), len(which)):
        raise WriterError(f"there is no moment {index + 1}")
    line = song.lines[which[index]]
    second = index > 0 and which[index - 1] == which[index]
    messages = [
        *story_prompt(song),
        {"role": "assistant", "content": json.dumps(story_of(plan), ensure_ascii=False)},
        {
            "role": "user",
            "content": (
                f"Write moment {index + 1} again (line: {line.text}"
                + (", its second picture" if second else "")
                + ")"
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


def painted(plan: Plan, art: str, marks: dict[str, str] | None = None) -> str:
    """What a moment's picture is painted from: its art with each person in it (by name, or
    as the story's only father, the boy...) written out as they look, so a person looks the
    same in every picture. `marks`: words put after a person's first mention, by name (which
    reference picture is theirs)."""
    song = plan.song
    if song is None:
        return art
    marks = {k.casefold(): v for k, v in (marks or {}).items()}
    out, at, marked = [], 0, set()
    for start, end, k in _mentions(song, art):
        person = song.people[k]
        look = person.look.strip().rstrip(".")
        mark = marks.get(person.name.casefold(), "")
        if mark and k not in marked:
            marked.add(k)
            look = f"{look} ({mark})"
        out += [art[at:start], _as_seen(look, art[start:end])]
        at = end
    return "".join(out) + art[at:]


def people_in(plan: Plan, art: str) -> list[Person]:
    """The people of the story in a moment's art, in the order they first come."""
    song = plan.song
    if song is None:
        return []
    order = list(dict.fromkeys(k for _, _, k in _mentions(song, art)))
    return [song.people[k] for k in order]


# What a person may be called besides their name: their role, when their name is one ("Dad" is
# the father, his dad), and what their look says they are ("a little boy in overalls" is the
# boy, the little boy), when no one else in the story is.
ROLES = {
    "father": ("dad", "daddy", "father", "papa", "papi"),
    "mother": ("mom", "mum", "mommy", "mummy", "mother", "mama", "mami"),
    "grandfather": ("grandpa", "grandfather", "granddad", "grandad", "gramps", "abuelo"),
    "grandmother": ("grandma", "grandmother", "granny", "gran", "nana", "abuela"),
    "son": ("son",),
    "daughter": ("daughter",),
    "brother": ("brother",),
    "sister": ("sister",),
    "husband": ("husband",),
    "wife": ("wife",),
}
# "the little boy", "the old man": up to two words between, never "the other girl" (someone else).
BETWEEN = r"(?:\s+(?!other\b|second\b|another\b)[\w-]+){0,2}?"
KINDS = (
    "man", "woman", "boy", "girl", "teenager", "teen", "child", "kid", "baby", "toddler", "lady",
    "guy", "grandfather", "grandmother", "father", "mother",
)  # fmt: skip


def _aliases(song: Song) -> dict[int, list[re.Pattern]]:
    """Each person's ways to be named in a moment, by their place in song.people: their name,
    then (only where no one else shares it) their role and what their look says they are."""
    people = [(k, p) for k, p in enumerate(song.people) if p.name and p.look]
    roles: dict[int, set[str]] = {}
    kinds: dict[int, set[str]] = {}
    for k, p in people:
        words = set(re.findall(r"[a-z]+", p.name.lower()))
        roles[k] = {r for r, said in ROLES.items() if words & set(said)}
        kinds[k] = {w for w in KINDS if re.search(rf"\b{w}\b", p.look, re.I)}
    out: dict[int, list[re.Pattern]] = {}
    for k, p in people:
        found = [_name_pattern(p.name)]
        for role in roles[k]:
            if sum(role in roles[j] for j, _ in people) == 1:
                said = "|".join(ROLES[role])
                found.append(
                    re.compile(rf"\b(?:(?:the|his|her|their|our)\s+)?(?:{said})\b(?:'s)?", re.I)
                )
        for kind in kinds[k]:
            if sum(kind in kinds[j] for j, _ in people) == 1:
                found.append(re.compile(rf"\bthe{BETWEEN}\s+{kind}\b(?:'s)?", re.I))
        out[k] = found
    return out


def _mentions(song: Song, art: str) -> list[tuple[int, int, int]]:
    """Where the people are named in `art`: (start, end, their place in song.people), in
    order; a longer name wins where two overlap ("Ana" inside "Ana Maria" is Ana Maria), and
    a name wins over a role or a kind of person."""
    hits = []
    for k, patterns in _aliases(song).items():
        for rank, pattern in enumerate(patterns):
            hits += [(m.start(), m.end(), k, min(rank, 1)) for m in pattern.finditer(art)]
    taken: list[tuple[int, int, int]] = []
    for start, end, k, _ in sorted(hits, key=lambda h: (h[3], h[0] - h[1])):
        if not any(start < b and a < end for a, b, _ in taken):
            taken.append((start, end, k))
    return sorted(taken)


def _name_pattern(name: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(name)}\b(?:'s)?", re.I)


def _as_seen(look: str, said: str) -> str:
    return look + ("'s" if said.lower().endswith("'s") else "")
