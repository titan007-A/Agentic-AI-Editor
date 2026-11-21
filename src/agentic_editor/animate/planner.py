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
from agentic_editor.animate import voice
from agentic_editor.animate.plan import (
    CATALOGUE,
    KINDS,
    MOODS,
    MOTIONS,
    WORDS,
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
    else the idea) and the top web results. Lessons need none."""
    if kind in ("lesson",):
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
)  # fmt: skip
_VISUAL = {
    "type": "object",
    "properties": {
        "kind": {"type": "string", "enum": list(CATALOGUE)},
        **{f: {"type": "string"} for f in _TEXT_FIELDS},
        "items": {"type": "array", "items": _ITEM, "maxItems": 5},
    },
    "required": ["kind", *_TEXT_FIELDS, "items"],
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
        "scenes": {"type": "array", "items": _SCENE, "minItems": 3, "maxItems": 14},
        "cta": {"type": "string"},
        "caption": {"type": "string"},
    },
    "required": ["title", "kind", "mood", "voice_kind", "scenes", "cta", "caption"],
}
SCENE_SCHEMA = _SCENE


# ---- prompts ---------------------------------------------------------------------------

GUIDE = """You write short animated reels for Instagram: a voice tells it, each scene shows one
animated visual. People decide in two seconds whether to keep watching.

How to write:
- Scene 1 is the hook: a "title" visual with a curiosity-gap headline (max 7 words) and a
  narration that makes people need the answer. Never "In this video" or "Did you know".
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

By kind:
- facts: surprising, specific, numbers where they help.
- lesson: a small story about one person (icons), a quiet turn, the lesson in the last scene.
- story: beginning, tension, twist; present tense; 5-9 scenes.
- news: what happened, why it matters, what happens next; say when.
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
    keep = {"title", "kind", "mood", "scenes", "cta", "caption"}
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


def _voice_for(choice: str, voice_kind: str, kind: str, mood: str) -> str:
    if choice not in ("", "auto"):
        return voice.resolve(choice, kind, mood)
    if voice_kind == "narrator":
        return "bm_fable" if kind == "story" else "bm_george"
    if voice_kind == "energetic":
        return "am_puck" if kind == "news" else "am_michael"
    if voice_kind == "calm":
        return "af_heart"
    return voice.recommend(kind, mood)


def _style_for(choice: str, kind: str) -> str:
    if choice in ("glow", "paper"):
        return choice
    return "paper" if kind in ("lesson", "story") else "glow"


def _from_reply(reply: dict, brief: Brief, found: list[Found]) -> Plan:
    kind = reply.get("kind") if brief.kind == "auto" else brief.kind
    kind = kind if kind in KINDS else "facts"
    mood = reply.get("mood") if reply.get("mood") in MOODS else "curious"
    plan = Plan(
        title=reply.get("title", ""),
        kind=kind,
        style=_style_for(brief.style, kind),
        voice=_voice_for(brief.voice, reply.get("voice_kind", ""), kind, mood),
        mood=mood,
        scenes=[Scene.model_validate(s) for s in reply.get("scenes", []) if isinstance(s, dict)],
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


def draft(llm, brief: Brief, found: list[Found]) -> tuple[Plan, list[str]]:
    """A first Plan for `brief`, and what is still off about it (shown to the person)."""
    return _write(llm, draft_prompt(brief, found), brief, found, temperature=0.75)


def change(llm, plan: Plan, request: str, brief: Brief) -> tuple[Plan, list[str]]:
    """`plan` changed as asked in plain words. The style, voice and sources stay unless
    the request is about them (the person changes those with the studio's own controls)."""
    keep = Brief(brief.idea, plan.kind, plan.style, plan.voice, brief.length)
    found = [Found(s.title, s.url, "") for s in plan.sources]
    changed, left = _write(
        llm, change_prompt(plan, request, brief.length), keep, found, temperature=0.5
    )
    return changed.model_copy(
        update={"style": plan.style, "voice": plan.voice, "sources": plan.sources}
    ), left


def redo_scene(llm, plan: Plan, index: int, request: str = "") -> Plan:
    """`plan` with scene `index` written again."""
    if not 0 <= index < len(plan.scenes):
        raise PlannerError(f"there is no scene {index + 1}")
    reply = llm.chat_json(scene_prompt(plan, index, request), SCENE_SCHEMA, temperature=0.8)
    scenes = list(plan.scenes)
    scenes[index] = Scene.model_validate(reply)
    return tidy(plan.model_copy(update={"scenes": scenes}))


def words_note(plan: Plan, length: str) -> str:
    low, high = WORDS.get(length, WORDS["auto"])
    return f"{narration_words(plan)} words of narration (aim {low}-{high})"
