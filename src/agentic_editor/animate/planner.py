"""The writer: one line in, a Plan out; then changes asked in plain words.

The planner model (gemma4 on the Mac) writes the whole reel as JSON held to a schema:
narration and a visual from the catalogue for every scene, the title, mood, voice kind,
the caption and the closing line. Facts, news and true stories are written from sources
found first (Wikipedia, a web search), so numbers and dates come from somewhere. A draft
that fails the checks (plan.problems) is sent back once with what to fix.
"""

import json
import logging
from dataclasses import dataclass
from pathlib import Path

from agentic_editor import caption, websearch
from agentic_editor.animate import mapwriter, voice
from agentic_editor.animate.plan import (
    ACTIONS,
    CATALOGUE,
    FEELINGS,
    KINDS,
    LOOKS,
    MAP_WORDS,
    MOODS,
    MOTIONS,
    SETTINGS,
    STYLES,
    WORDS,
    CastMember,
    Plan,
    Scene,
    Source,
    narration_words,
    problems,
    tidy,
)
from agentic_editor.llm import LLMError

log = logging.getLogger(__name__)

SOURCE_CHARS = 3500  # of each source, in the prompt
# Tokens the planner holds (prompt, sources, a draft and its fixes): Ollama's default cuts
# the answer to a fix off mid-reel.
CONTEXT = 12288
REPLY = 4096
SOURCES = 4


class PlannerError(RuntimeError):
    pass


@dataclass
class Brief:
    idea: str
    kind: str = "auto"  # auto or plan.KINDS
    style: str = "auto"  # auto or plan.STYLES
    voice: str = "auto"  # auto, a voice group or a voice id (voice.py)
    length: str = "auto"  # plan.LENGTHS


@dataclass
class Found:
    title: str
    url: str
    text: str


# ---- sources ---------------------------------------------------------------------------


def gather(
    idea: str, kind: str, cache: Path, search=websearch.search, topic: str = "", query: str = ""
) -> list[Found]:
    """What the reel's facts are taken from: Wikipedia's article on its subject (`topic`,
    else the idea) and the top web results. Lessons need none, nor a made-up story (one
    whose idea names no real subject)."""
    if kind == "lesson" or (kind == "story" and not topic):
        return []
    found: list[Found] = []
    try:
        sources = caption.gather_topic(topic or idea, "en", cache, english_first=True)
        found += [Found(a.title, a.url, a.text[:SOURCE_CHARS]) for a in sources.articles[:1]]
    except Exception as exc:  # no article: the web alone
        log.info("no Wikipedia article for %r: %s", topic or idea, exc)
    query = query or idea
    query = f"{query} news" if kind == "news" else query
    try:
        hits = search(query, 8)
    except Exception as exc:
        log.info("web search for %r failed: %s", query, exc)
        hits = []
    for hit in hits:
        if len(found) >= SOURCES:
            break
        body = " ".join(str(hit.get("body", "")).split())
        href = str(hit.get("href", ""))
        if any(_same_page(href, f.url) for f in found):
            continue  # the Wikipedia article again, found by the search too
        if len(body) > 60:
            found.append(
                Found(str(hit.get("title", ""))[:120], str(hit.get("href", "")), body[:800])
            )
    return found


SUBJECT_SCHEMA = {
    "type": "object",
    "properties": {"subject": {"type": "string"}, "search": {"type": "string"}},
    "required": ["subject", "search"],
}


def subject(llm, idea: str) -> tuple[str, str]:
    """What a reel idea is about, as Wikipedia would title it, and a short web search for
    it: an idea is a sentence ("the lost city that vanished into the desert"), which finds
    no article as it is."""
    reply = llm.chat_json(
        [{"role": "user", "content": (
            f"A short video will be made about: {idea}\n"
            "subject: the title of the English Wikipedia article it is about (e.g. 'Ubar'), "
            "or '' if it is not about one real thing. search: 2-6 words to search the web for "
            "facts about it."
        )}],
        SUBJECT_SCHEMA,
        temperature=0.1,
    )  # fmt: skip
    return str(reply.get("subject", "")).strip()[:120], str(reply.get("search", "")).strip()[
        :120
    ] or idea


def roomy(llm) -> None:
    """Give the planner client room for a whole reel and its fixes."""
    if hasattr(llm, "context"):
        llm.context = max(llm.context or 0, CONTEXT)
    if hasattr(llm, "max_reply"):
        llm.max_reply = max(llm.max_reply or 0, REPLY)


def _same_page(a: str, b: str) -> bool:
    """Two links to one page (http or https, a trailing slash or a #part aside)."""

    def key(url: str) -> str:
        return (
            url.split("#")[0].removeprefix("https://").removeprefix("http://").rstrip("/").lower()
        )

    return bool(a) and key(a) == key(b)


# ---- the schema the model answers to ---------------------------------------------------

_ITEM = {
    "type": "object",
    "properties": {
        "emoji": {"type": "string"},
        "text": {"type": "string"},
        "year": {"type": "string"},
        "value": {"type": "string"},
        "motion": {"type": "string", "enum": list(MOTIONS)},
    },
    "required": ["emoji", "text", "year", "value", "motion"],
}
_TEXT_FIELDS = (
    "text", "highlight", "kicker", "value", "prefix", "suffix", "detail", "by", "answer", "emoji",
    "art",
)  # fmt: skip
_ACTOR = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "action": {"type": "string", "enum": list(ACTIONS)},
        "mood": {"type": "string", "enum": list(FEELINGS)},
        "prop": {"type": "string"},
        "says": {"type": "string"},
    },
    "required": ["name", "action", "mood", "prop", "says"],
}
_VISUAL = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(CATALOGUE)},
        **{f: {"type": "string"} for f in _TEXT_FIELDS},
        "items": {"type": "array", "items": _ITEM, "maxItems": 5},
        "setting": {"type": "string", "enum": ["", *SETTINGS]},
        "actors": {"type": "array", "items": _ACTOR, "maxItems": 2},
        "crowd": {"type": "integer", "minimum": 0, "maximum": 8},
    },
    "required": ["kind", *_TEXT_FIELDS, "items", "setting", "actors", "crowd"],
}
_CAST = {
    "type": "object",
    "properties": {"name": {"type": "string"}, "look": {"type": "string", "enum": list(LOOKS)}},
    "required": ["name", "look"],
}
_SCENE = {
    "type": "object",
    "properties": {"narration": {"type": "string"}, "visual": _VISUAL},
    "required": ["narration", "visual"],
}
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "kind": {"type": "string", "enum": list(KINDS)},
        "mood": {"type": "string", "enum": list(MOODS)},
        "voice_kind": {"type": "string", "enum": ["narrator", "calm", "energetic"]},
        "cast": {"type": "array", "items": _CAST, "maxItems": 4},
        "scenes": {"type": "array", "items": _SCENE, "minItems": 3, "maxItems": 14},
        "cta": {"type": "string"},
        "caption": {"type": "string"},
    },
    "required": ["title", "kind", "mood", "voice_kind", "cast", "scenes", "cta", "caption"],
}
SCENE_SCHEMA = _SCENE


# ---- prompts ---------------------------------------------------------------------------

GUIDE = """You write short animated reels for Instagram: a voice tells it, each scene shows one
animated visual. People decide in two seconds whether to keep watching.

How to write:
- Scene 1 is the hook: a "title" visual with a curiosity-gap headline (max 7 words), or in
  a story its most striking moment as a "scene", and a narration that makes people need
  the answer. Never "In this video" or "Did you know".
- Every scene: 1-2 short spoken sentences (6-28 words), concrete and vivid, simple words.
  The visual shows the key idea of that narration; its words should appear in it.
- Vary the visuals; never the same kind twice in a row. Use stat for a number that matters,
  timeline for dates, place for where, reveal for the answer to a mystery, quote for a line
  someone said, icons for people or objects doing something, compare for contrasts, list for
  steps or reasons, dots for one punchy word or number.
- Facts, numbers, dates and names only from the sources given. Unsure: leave it out.
- End on a payoff: a twist, the answer, or the lesson in one line. The system adds the
  follow request after your last scene; never write "follow" in the scenes.
- cta: one line that starts with "Follow for more" and names the page's kind of content
  (max 7 words), e.g. "Follow for more lost history."
- voice_kind: narrator for history, mysteries, facts and stories; calm for life lessons;
  energetic for news and upbeat, fun facts.
- caption: 2-3 sentences that make people watch, then 5-8 relevant hashtags.
- title: the reel's subject in at most 6 words (shown small at the top).
- emoji fields: exactly one emoji character. Unused fields: empty strings or [].
- art: one sentence for the painting behind the scene: the place, the time of day, the
  light and mood, a few objects (for a character scene: the setting only, empty of
  people). Never a real person's name or face, never any writing.

The shape of a good reel (another subject than yours; never copy its words or facts):
{"title": "The Tower That Grows", "kind": "facts", "mood": "curious",
 "voice_kind": "energetic", "scenes": [
  {"narration": "Every summer, the Eiffel Tower quietly gets taller.",
   "visual": {"kind": "title", "text": "The tower that grows", "highlight": "grows"}},
  {"narration": "On the hottest days, its iron stretches and it can rise about 15 centimetres.",
   "visual": {"kind": "stat", "value": "15", "suffix": "cm", "text": "taller on hot days"}},
  {"narration": "Built in 1889 for a world fair, it was meant to stand for just 20 years.",
   "visual": {"kind": "timeline", "items": [{"year": "1889", "text": "Built for a world fair"},
   {"year": "1909", "text": "Saved from being torn down"}]}},
  {"narration": "So why didn't Paris tear it down? Radio. Its height made it the perfect antenna.",
   "visual": {"kind": "reveal", "text": "Why was it saved?", "answer": "Radio",
   "emoji": "📡"}}],
 "cta": "Follow for more strange facts."}

Stories and lessons are acted by characters:
- cast: 1-3 characters with a name and a look; the same names act all through.
- Most scenes are "scene" visuals: a setting and 1-2 actors from the cast, each with an
  action that shows what happens (walk_in when someone arrives, think while deciding,
  celebrate when it works, sad or cry when it hurts, shocked at the twist, give to hand
  something over, read for a phone or a book) and a mood. A prop is one emoji they hold.
  "says" is a short line in a bubble, only when someone speaks. A scene's text titles it
  (who or where this part is about), detail is a short red note pointing at the first
  actor ("40 days", "Skin peeling off"), emoji a big object beside them, crowd the people
  around (a mob, a class, a jury).
- True stories from history are told the same way: the real people as the cast.
- A title can open it; a quote or reveal can close it with the lesson or the twist.
- Narration is told by a storyteller in present tense: "Maya opens the box."
  Example scene: {"narration": "Leo finally opens the letter, and his hands start to shake.",
  "visual": {"kind": "scene", "setting": "room", "actors": [{"name": "Leo",
  "action": "read", "mood": "surprised", "prop": "✉️", "says": ""}]}}

By kind:
- facts: surprising, specific, numbers where they help. cast: [].
- lesson: a small story about one or two people, a quiet turn, the lesson in the last scene.
- story: beginning, tension, twist; present tense; 5-9 scenes.
- news: what happened, why it matters, what happens next; say when. cast: [].
"""


def catalogue_text() -> str:
    return "\n".join(
        f"- {kind}: {use}. Fields: {fields}" for kind, (use, fields) in CATALOGUE.items()
    )


def sources_text(found: list[Found]) -> str:
    if not found:
        return "No sources: write from general knowledge, no precise numbers or dates."
    return "\n\n".join(f"[{i + 1}] {f.title}\n{f.text}" for i, f in enumerate(found))


def draft_prompt(brief: Brief, found: list[Found]) -> list[dict]:
    low, high = WORDS.get(brief.length, WORDS["auto"])
    kind = "choose the kind that fits" if brief.kind == "auto" else f"kind: {brief.kind}"
    user = (
        f"Reel idea: {brief.idea}\n{kind}\n"
        f"Total narration across all scenes: {low}-{high} words.\n\n"
        f"Visual catalogue:\n{catalogue_text()}\n\nSources:\n{sources_text(found)}"
    )
    return [{"role": "system", "content": GUIDE}, {"role": "user", "content": user}]


def _plan_json(plan: Plan) -> str:
    keep = {"title", "kind", "mood", "cast", "scenes", "cta", "caption"}
    return json.dumps(plan.model_dump(include=keep), ensure_ascii=False)


def change_prompt(plan: Plan, request: str, length: str) -> list[dict]:
    low, high = WORDS.get(length, WORDS["auto"])
    user = (
        f"Here is a reel as JSON:\n{_plan_json(plan)}\n\nChange it as asked: {request}\n"
        f"Keep everything not asked about as it is. Total narration {low}-{high} words. "
        f"Answer with the whole reel.\n\nVisual catalogue:\n{catalogue_text()}"
    )
    return [{"role": "system", "content": GUIDE}, {"role": "user", "content": user}]


def scene_prompt(plan: Plan, index: int, request: str) -> list[dict]:
    ask = request.strip() or "make it better: a stronger line and a different visual"
    user = (
        f"Here is a reel as JSON:\n{_plan_json(plan)}\n\nWrite scene {index + 1} again ({ask}). "
        f"Keep its facts and its place in the story; it must not repeat the visual kind of the "
        f"scenes next to it. Answer with that one scene.\n\nVisual catalogue:\n{catalogue_text()}"
    )
    return [{"role": "system", "content": GUIDE}, {"role": "user", "content": user}]


# ---- writing ---------------------------------------------------------------------------


def _voice_for(choice: str, voice_kind: str, kind: str, mood: str, default: str = "") -> str:
    if choice not in ("", "auto"):
        return voice.resolve(choice, kind, mood)
    if default:
        return default
    if voice_kind == "narrator":
        return "bm_fable" if kind == "story" else "bm_george"
    if voice_kind == "energetic":
        return "am_puck" if kind == "news" else "am_michael"
    if voice_kind == "calm":
        return "af_heart"
    return voice.recommend(kind, mood)


def _style_for(choice: str, kind: str, acted: bool = False) -> str:
    """The style asked for, else by the content: characters for stories and lessons told
    with them, Neon Glow for facts and news."""
    if choice in STYLES:
        return choice
    if kind in ("lesson", "story"):
        return "story" if acted else "paper"
    return "glow"


def _from_reply(reply: dict, brief: Brief, found: list[Found]) -> Plan:
    kind = reply.get("kind") if brief.kind == "auto" else brief.kind
    kind = kind if kind in KINDS else "facts"
    mood = reply.get("mood") if reply.get("mood") in MOODS else "curious"
    scenes = [Scene.model_validate(s) for s in reply.get("scenes", []) if isinstance(s, dict)]
    acted = any(s.visual.kind == "scene" for s in scenes)
    plan = Plan(
        title=reply.get("title", ""),
        kind=kind,
        style=_style_for(brief.style, kind, acted),
        voice=_voice_for(brief.voice, reply.get("voice_kind", ""), kind, mood),
        mood=mood,
        scenes=scenes,
        cast=[CastMember.model_validate(c) for c in reply.get("cast", []) if isinstance(c, dict)],
        cta=reply.get("cta", ""),
        caption=reply.get("caption", ""),
        sources=[Source(title=f.title, url=f.url) for f in found if f.url],
    )
    return tidy(plan)


def _write(
    llm, messages: list[dict], brief: Brief, found: list[Found], temperature: float
) -> tuple[Plan, list[str]]:
    reply = llm.chat_json(messages, SCHEMA, temperature=temperature)
    plan = _from_reply(reply, brief, found)
    wrong = problems(plan, brief.length)
    if not wrong:
        return plan, []
    log.info("draft needs fixing: %s", "; ".join(wrong))
    fix = [
        *messages,
        {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)},
        {
            "role": "user",
            "content": "Fix these and answer with the whole reel again:\n- " + "\n- ".join(wrong),
        },
    ]
    try:
        again = _from_reply(llm.chat_json(fix, SCHEMA, temperature=0.4), brief, found)
    except LLMError as exc:  # a fix that breaks off: the first draft, with what is off in it
        log.warning("fixing the draft failed: %s", exc)
        again = plan
    left = problems(again, brief.length)
    better = again if len(left) <= len(wrong) else plan
    if len(better.scenes) < 3:
        raise PlannerError("the writer didn't manage a reel: " + "; ".join(left or wrong))
    return better, left if better is again else wrong


def draft(llm, brief: Brief, found: list[Found], known=None, checks=None) -> tuple[Plan, list[str]]:
    """A first Plan for `brief`, and what is still off about it (shown to the person).
    `known(name)` says whether a country is on the map, `checks` the strict check's verdicts
    kept from earlier reels (World map reels: mapwriter.Checks)."""
    if brief.style == mapwriter.STYLE:
        sources = [Source(title=f.title, url=f.url) for f in found if f.url]
        voice = _voice_for(brief.voice, "", "facts", mapwriter.MOOD, mapwriter.VOICE)
        return mapwriter.draft(
            llm, brief.idea, brief.length, sources_text(found), sources, voice, known, checks
        )
    return _write(llm, draft_prompt(brief, found), brief, found, temperature=0.75)


def change(
    llm, plan: Plan, request: str, brief: Brief, known=None, checks=None
) -> tuple[Plan, list[str]]:
    """`plan` changed as asked in plain words. The style, voice and sources stay unless
    the request is about them (the person changes those with the studio's own controls)."""
    if plan.style == mapwriter.STYLE:
        return mapwriter.change(llm, plan, brief.idea, request, brief.length, known, checks)
    keep = Brief(brief.idea, plan.kind, plan.style, plan.voice, brief.length)
    found = [Found(s.title, s.url, "") for s in plan.sources]
    changed, left = _write(
        llm, change_prompt(plan, request, brief.length), keep, found, temperature=0.5
    )
    return changed.model_copy(
        update={"style": plan.style, "voice": plan.voice, "sources": plan.sources}
    ), left


def redo_scene(
    llm, plan: Plan, index: int, request: str = "", idea: str = "", checks=None, known=None
) -> Plan:
    """`plan` with scene `index` written again."""
    if not 0 <= index < len(plan.scenes):
        raise PlannerError(f"there is no scene {index + 1}")
    if plan.style == mapwriter.STYLE and plan.scenes[index].visual.kind == "country":
        return mapwriter.redo_country(llm, plan, index, request, idea, checks, known)
    reply = llm.chat_json(scene_prompt(plan, index, request), SCENE_SCHEMA, temperature=0.8)
    scenes = list(plan.scenes)
    scenes[index] = Scene.model_validate(reply)
    return tidy(plan.model_copy(update={"scenes": scenes}))


def words_note(plan: Plan, length: str) -> str:
    words = MAP_WORDS if plan.style == mapwriter.STYLE else WORDS
    low, high = words.get(length, words["auto"])
    return f"{narration_words(plan)} words of narration (aim {low}-{high})"


# ---- autopilot: new topics and a fact check -------------------------------------------

TOPICS_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 8}
    },
    "required": ["topics"],
}
CHECK_SCHEMA = {
    "type": "object",
    "properties": {"unsupported": {"type": "array", "items": {"type": "string"}, "maxItems": 8}},
    "required": ["unsupported"],
}


def next_topic(llm, niche: str, used: list[str]) -> str:
    """A topic for the page's next reel, in its niche and not one it has made."""
    done = "\n".join(f"- {u}" for u in used[-40:]) or "(none yet)"
    reply = llm.chat_json(
        [{"role": "user", "content": (
            f"An Instagram page posts short animated reels about: {niche}.\n"
            f"Reels it has made already:\n{done}\n\n"
            "Suggest 6 new reel topics in that niche that people would stop scrolling for: "
            "each a specific, real story, fact or mystery in under 12 words, none like the "
            "ones made already."
        )}],
        TOPICS_SCHEMA,
        temperature=0.9,
    )  # fmt: skip
    made = {u.strip().lower() for u in used}
    for topic in reply.get("topics", []):
        topic = " ".join(str(topic).split())[:200]
        if topic and topic.lower() not in made:
            return topic
    raise PlannerError(f"no new topic in {niche!r}")


def fact_check(llm, plan: Plan, found: list[Found]) -> list[str]:
    """Claims of the narration (names, numbers, dates, events) the sources don't support.
    Stories and lessons are told, not reported: nothing to check. Facts without sources:
    everything is in doubt."""
    if plan.kind in ("story", "lesson"):
        return []
    claims = mapwriter.asides(plan) if plan.style == mapwriter.STYLE else None
    if claims == []:  # a World map tour that only names things: nothing to check
        return []
    if not any(f.text for f in found):
        if claims:
            return [f"no source to check: {c}" for c in claims]
        return ["no sources were found to check the facts against"]
    narration = "\n".join(f"{i + 1}. {s.narration}" for i, s in enumerate(plan.scenes))
    if claims:  # its short facts are what can be wrong
        narration = "\n".join(f"{i + 1}. {c}" for i, c in enumerate(claims))
    reply = llm.chat_json(
        [{"role": "user", "content": (
            f"Sources:\n{sources_text(found)}\n\nA reel's narration:\n{narration}\n\n"
            "List every claim in the narration (a name, number, date or event) that the "
            "sources do not support, quoting it briefly. Empty if all are supported."
        )}],
        CHECK_SCHEMA,
        temperature=0.0,
    )  # fmt: skip
    return [" ".join(str(c).split())[:200] for c in reply.get("unsupported", []) if str(c).strip()]
