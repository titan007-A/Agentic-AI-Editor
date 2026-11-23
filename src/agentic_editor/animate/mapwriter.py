"""The writer for World map reels: one topic, a tour of countries, a card for each thing.

The format (from the reference reels the owner chose): a short spoken hook that names the
topic ("The most famous desserts from every country."), then one sentence a country,
"<Country> has <thing>, <thing>, and <thing>.", the most famous first, now and then one
short surprising fact set off by commas, touring the world from the United States through
Europe and Asia to Australia and Latin America. The model answers a schema made for this
shape (far easier for a small model than the general catalogue); it becomes an ordinary
Plan: a title scene, then a "country" scene per country, each thing an item with its
emoji and what its picture shows.

Pictures never show a logo, writing or a character someone owns: for a brand, film or
character the card shows an object of its kind, so every reel is our own.
"""

import json
import logging
import random
import re

from agentic_editor.animate.plan import (
    MAP_WORDS,
    MAX_COUNTRY_ITEMS,
    Item,
    Plan,
    Scene,
    Source,
    Visual,
    problems,
    tidy,
)
from agentic_editor.llm import LLMError

log = logging.getLogger(__name__)

STYLE = "map"
MOOD = "upbeat"
VOICE = "am_michael"  # the reference reels: a clear, confident American man
CTA = "Follow for more countries."
# Countries a reel tours, by its length.
COUNTRIES = {"short": (6, 8), "medium": (8, 12), "long": (13, 18), "auto": (10, 14)}
# A tour of fifteen countries with a card each thing is a long answer: room for it (tokens).
CONTEXT = 16384
REPLY = 8192

GUIDE = """You write World map reels for Instagram. The camera flies over a satellite map of
the Earth from country to country; each country lights up as the voice names it, and a
picture card pops up for every thing it names. One topic, many countries.

Write it exactly in this shape:
- hook: one short spoken line that says the topic, e.g. "The most famous desserts from every
  country." (at most 10 words).
- headline: the topic on screen, at most 7 words, e.g. "Most famous desserts in every
  country"; highlight: its one key word, e.g. "desserts".
- countries: a world tour, each country once: start with the United States, then Canada or
  Mexico, the United Kingdom and Europe (France, Spain, Italy, Germany...), then Asia
  (Turkey, India, China, Japan, South Korea...), then Australia, and end in Latin America
  or Africa. Leave out a country where you know nothing sure.
- narration, one sentence a country: "<country> has <thing>, <thing>, and <thing>." Name 3-5
  things for a big country, 1-3 for a small one, the most famous first, by name only.
  Now and then (three or four times in the whole reel, never twice in one country) a thing
  gets a short, famous, surprising fact right after its name, set off by commas ("Tom and
  Jerry, who won seven Oscars,"): only a fact everyone can check, never a description of
  what the thing is, never made up. No other words: no "known for", no "also".
- Say the country exactly as "country" writes it, and every thing exactly as its card's
  "name". Use the usual English name of each thing (at most 4 words).
- items: one per thing named, in the order said: name; emoji (one emoji for it); picture
  (at most 8 words): what its card's picture shows, plainly (a dish on a plate, a castle on
  a hill, a red sneaker). For a brand, film, show, game, book or character the picture
  shows an object or scene of its kind, never a logo, never the character itself, never
  any writing.
- Only things really from that country and famous there. Family-friendly: nothing sexual,
  no drugs, no hate.
- title: the topic in at most 7 words, title case.
- caption: one line that makes people comment (e.g. "Which one is your favourite?"), then
  5-8 hashtags (#maps #countries #geography and the topic's own).

An example of one country (another topic than yours; never copy its words):
{"country": "Japan", "narration": "Japan has the Shinkansen, the world's first bullet train,
the Nintendo Switch, and the instant noodle.", "items": [{"name": "Shinkansen",
"emoji": "🚄", "picture": "a white bullet train speeding past, side view"}, {"name":
"Nintendo Switch", "emoji": "🎮", "picture": "a handheld video game console with two small
coloured controllers"}, {"name": "instant noodle", "emoji": "🍜", "picture": "a cup of
instant noodles with steam, chopsticks on top"}]}
"""

_ITEM = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "emoji": {"type": "string"},
        "picture": {"type": "string"},
    },
    "required": ["name", "emoji", "picture"],
}
_COUNTRY = {
    "type": "object",
    "properties": {
        "country": {"type": "string"},
        "narration": {"type": "string"},
        "items": {
            "type": "array", "items": _ITEM, "minItems": 1, "maxItems": MAX_COUNTRY_ITEMS,
        },
    },
    "required": ["country", "narration", "items"],
}  # fmt: skip
SCHEMA = {
    "type": "object",
    "properties": {
        "title": {"type": "string"},
        "hook": {"type": "string"},
        "headline": {"type": "string"},
        "highlight": {"type": "string"},
        "countries": {"type": "array", "items": _COUNTRY, "minItems": 4, "maxItems": 20},
        "caption": {"type": "string"},
    },
    "required": ["title", "hook", "headline", "highlight", "countries", "caption"],
}
COUNTRY_SCHEMA = _COUNTRY

# Topics in the reference page's kind, all family-friendly; the studio offers them as
# ideas and the autopilot takes them in turn before asking the model for more.
IDEAS = (
    "Most famous foods from every country",
    "Most famous desserts from every country",
    "Most famous street foods from every country",
    "Most famous breakfasts from every country",
    "Most famous drinks from every country (no alcohol)",
    "Most famous cartoons from every country",
    "Most famous video games from every country",
    "Most famous inventions from every country",
    "Most famous landmarks from every country",
    "Most famous castles from every country",
    "Most famous festivals from every country",
    "Most famous car brands from every country",
    "Most famous sportswear brands from every country",
    "Most famous phone brands from every country",
    "Most famous snacks from every country",
    "Most famous cheeses from every country",
    "Most famous breads from every country",
    "Most famous soups from every country",
    "Most famous national animals from every country",
    "Most famous dog breeds from every country",
    "Most famous musical instruments from every country",
    "Most famous dances from every country",
    "Most famous sports from every country",
    "Most famous football clubs from every country",
    "Most famous scientists from every country",
    "Most famous painters from every country",
    "Most famous paintings from every country",
    "Most famous books from every country",
    "Most famous movies from every country",
    "Most famous TV shows from every country",
    "Most famous myths and legendary creatures from every country",
    "Most haunted places from every country",
    "Most famous warriors from every country",
    "Most famous toys from every country",
    "Most famous board games from every country",
    "Most famous chocolate brands from every country",
    "Most famous soft drinks from every country",
    "Most famous fast food chains from every country",
    "Most famous airlines from every country",
    "Most famous trains from every country",
    "Most famous bridges from every country",
    "Most famous mountains from every country",
    "Most famous rivers from every country",
    "Most famous islands from every country",
    "Most famous traditional clothes from every country",
    "Most famous superstitions from every country",
    "Most famous holiday traditions from every country",
    "Most famous fruits from every country",
    "Most famous spices from every country",
    "Most famous tea and coffee drinks from every country",
)


def ideas(used: list[str], count: int = 6, seed: int | None = None) -> list[str]:
    """`count` topics from IDEAS not made already (by their words, any case), in a fresh
    order each time; the made ones again only once all are."""
    made = {" ".join(u.lower().split()) for u in used}
    fresh = [i for i in IDEAS if i.lower() not in made]
    pool = fresh or list(IDEAS)
    rng = random.Random(seed)
    rng.shuffle(pool)
    return pool[:count]


TOPICS_SCHEMA = {
    "type": "object",
    "properties": {
        "topics": {"type": "array", "items": {"type": "string"}, "minItems": 3, "maxItems": 8}
    },
    "required": ["topics"],
}


def next_topic(llm, niche: str, used: list[str]) -> str:
    """A World map reel's next topic: one of IDEAS not made yet, else a new one the model
    suggests in the same shape ("Most famous X from every country")."""
    made = {" ".join(u.lower().split()) for u in used}
    for idea in IDEAS:
        if idea.lower() not in made:
            return idea
    done = "\n".join(f"- {u}" for u in used[-60:])
    reply = llm.chat_json(
        [{"role": "user", "content": (
            "An Instagram page posts World map reels: a tour of countries, one thing or a few "
            "for each, e.g. 'Most famous desserts from every country'.\n"
            f"Its niche: {niche or 'countries of the world'}.\nReels it has made:\n{done}\n\n"
            "Suggest 6 new topics in exactly that shape ('Most famous X from every country' "
            "or 'Biggest X from every country'), family-friendly, each one people of every "
            "country would comment on, none like those made."
        )}],
        TOPICS_SCHEMA,
        temperature=0.9,
    )  # fmt: skip
    for topic in reply.get("topics", []):
        topic = " ".join(str(topic).split())[:200]
        if topic and topic.lower() not in made:
            return topic
    raise LLMError(f"no new World map topic in {niche!r}")


FILLER = {"has", "have", "the", "a", "an", "and", "of"}
# The reference reels give a short fact now and then, never one per thing: at most one a
# country, a few a reel, each short. The rest is said as names.
FACTS_PER_REEL = 4
FACT_WORDS = 12
ITEMS_WHEN_LONG = 4  # things a country keeps when a tour runs too long
# A fact worth its seconds, as the reference reels tell them ("who won seven Oscars",
# "created by a marine biologist", "the most viewed video on YouTube"), not a description
# of what the thing is ("a custard topped with caramel").
FACT_OPENS = re.compile(
    r"^(who|whose|which|where|that|yes|created by|founded by|invented by|started|born|"
    r"backed by|named after|made famous|first made|the world's|the first|the most|the oldest|"
    r"the only|the biggest|the largest)\b",
    re.I,
)
FACT_WORDS_ANYWHERE = re.compile(
    r"\d|\b(most|first|biggest|best[- ]selling|world's|only|oldest|largest|highest|ever|"
    r"record|banned|invented|founded)\b",
    re.I,
)


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def parse(scene: Scene) -> tuple[str, list[str]]:
    """A country sentence as said: how it names the country ("The United States") and the
    short fact told after each thing ("" for none), in the order of the scene's items."""
    v = scene.visual
    names = [set(_words(i.text)) - FILLER for i in v.items]
    country = set(_words(v.text)) - FILLER
    head, has, _ = scene.narration.partition(" has ")
    opening = head.strip() if has and country and country <= set(_words(head)) else v.text
    facts = ["" for _ in v.items]
    last = -1
    for part in re.split(r"[,;:]", scene.narration):
        part = re.sub(r"^\s*and\s+", "", part).strip().rstrip(".!")
        words = set(_words(part)) - FILLER
        if not words:
            continue
        named = next((k for k, n in enumerate(names) if n and n <= words), -1)
        rest = words - country - set().union(*names) if names else words - country
        if named >= 0 and len(rest) <= 2:
            last = named
        elif len(rest) >= 3 and last >= 0 and not facts[last]:
            facts[last] = part
    return opening, facts


def telling(fact: str) -> bool:
    """Whether a short fact is the kind the reels tell, not a description of the thing."""
    return bool(FACT_OPENS.search(fact) or FACT_WORDS_ANYWHERE.search(fact))


def asides(plan: Plan) -> list[str]:
    """The short facts a World map reel tells after a thing's name ("brownies, which were
    first baked in Chicago,"), each with its country and thing: what a fact check looks at
    (the names themselves are the topic, not claims)."""
    out: list[str] = []
    for scene in plan.scenes:
        if scene.visual.kind != "country":
            continue
        _, facts = parse(scene)
        for item, fact in zip(scene.visual.items, facts, strict=True):
            if fact:
                out.append(f"{scene.visual.text}, {item.text}: {fact}")
    return out


def sentence(opening: str, items: list[Item], facts: list[str]) -> str:
    """'<Country> has A, B, and C.' with each kept fact after its thing."""
    parts = [f"{i.text}, {f}," if f else i.text for i, f in zip(items, facts, strict=True)]
    if len(parts) == 1:
        said = parts[0].rstrip(",")
    elif len(parts) == 2 and not facts[0]:
        said = f"{parts[0]} and {parts[1].rstrip(',')}"
    else:
        said = ", ".join(p.rstrip(",") for p in parts[:-1]) + f", and {parts[-1].rstrip(',')}"
    return f"{opening} has {said}."


def tighten(plan: Plan, length: str) -> Plan:
    """A tour said the reference reels' way, whatever the writer wrote: each country's
    sentence made again from its cards ("<Country> has A, B, and C."), keeping at most one
    short fact a country and FACTS_PER_REEL in all (the first ones), then, while it runs
    past its length, fewer facts, at most ITEMS_WHEN_LONG things a country, fewer
    countries (never under MIN_COUNTRIES)."""
    from agentic_editor.animate.plan import MIN_COUNTRIES, narration_words

    _, high = MAP_WORDS.get(length, MAP_WORDS["auto"])
    found = {
        n: parse(s)
        for n, s in enumerate(plan.scenes)
        if s.visual.kind == "country" and s.visual.items
    }
    kept: dict[int, list[str]] = {}
    budget = FACTS_PER_REEL
    for n, (_, facts) in found.items():
        mine = ["" for _ in facts]
        for k, fact in enumerate(facts):
            if fact and budget and len(fact.split()) <= FACT_WORDS and telling(fact):
                mine[k] = fact
                budget -= 1
                break
        kept[n] = mine

    def build(scenes_kept: dict[int, list[str]], cap: int | None, drop: int) -> Plan:
        scenes = []
        countries = [n for n in found]
        gone = set(countries[len(countries) - drop :]) if drop else set()
        for n, scene in enumerate(plan.scenes):
            if n in gone:
                continue
            if n in found:
                opening = found[n][0]
                items = scene.visual.items[:cap] if cap else scene.visual.items
                facts = scenes_kept[n][: len(items)]
                visual = scene.visual.model_copy(update={"items": items})
                scene = scene.model_copy(
                    update={"narration": sentence(opening, items, facts), "visual": visual}
                )
            scenes.append(scene)
        return plan.model_copy(update={"scenes": scenes})

    out = build(kept, None, 0)
    if narration_words(out) <= high:
        return out
    for n in reversed(list(kept)):  # facts go first, from the end
        kept[n] = ["" for _ in kept[n]]
        out = build(kept, None, 0)
        if narration_words(out) <= high:
            return out
    out = build(kept, ITEMS_WHEN_LONG, 0)
    drop = 0
    while narration_words(out) > high and len(found) - drop > MIN_COUNTRIES:
        drop += 1
        out = build(kept, ITEMS_WHEN_LONG, drop)
    return out


# ---- between the model's shape and a Plan --------------------------------------------------


def to_plan(reply: dict, sources: list[Source], voice: str) -> Plan:
    """A Plan from the model's answer: the hook as a title scene, a country scene each."""
    hook = " ".join(str(reply.get("hook", "")).split())
    headline = " ".join(str(reply.get("headline", "")).split()) or str(reply.get("title", ""))
    scenes = [
        Scene(
            narration=hook,
            visual=Visual(kind="title", text=headline, highlight=str(reply.get("highlight", ""))),
        )
    ]
    for c in reply.get("countries", []):
        if not isinstance(c, dict):
            continue
        items = [
            Item(
                text=str(i.get("name", "")),
                emoji=str(i.get("emoji", "")),
                art=str(i.get("picture", "")),
            )
            for i in c.get("items", [])
            if isinstance(i, dict)
        ]
        scenes.append(
            Scene(
                narration=str(c.get("narration", "")),
                visual=Visual(kind="country", text=str(c.get("country", "")), items=items),
            )
        )
    return tidy(
        Plan(
            title=str(reply.get("title", "")),
            kind="facts",
            style=STYLE,
            voice=voice,
            mood=MOOD,
            scenes=[s for s in scenes if s.narration.strip()],
            cta=CTA,
            caption=str(reply.get("caption", "")),
            sources=sources,
        )
    )


def to_reply(plan: Plan) -> dict:
    """A Plan in the model's shape (to change it or write a country again)."""
    first = plan.scenes[0] if plan.scenes and plan.scenes[0].visual.kind == "title" else None
    countries = [
        {
            "country": s.visual.text,
            "narration": s.narration,
            "items": [{"name": i.text, "emoji": i.emoji, "picture": i.art} for i in s.visual.items],
        }
        for s in plan.scenes
        if s.visual.kind == "country"
    ]
    return {
        "title": plan.title,
        "hook": first.narration if first else "",
        "headline": first.visual.text if first else plan.title,
        "highlight": first.visual.highlight if first else "",
        "countries": countries,
        "caption": plan.caption,
    }


def _with_pictures(new: Plan, old: Plan | None) -> Plan:
    """Painted card pictures kept for the things that are still there with the same picture."""
    if old is None:
        return new
    kept = {
        (i.text.lower(), i.art): i.image
        for s in old.scenes
        for i in s.visual.items
        if s.visual.kind == "country" and i.image
    }
    scenes = []
    for s in new.scenes:
        if s.visual.kind == "country":
            items = [
                i.model_copy(update={"image": kept.get((i.text.lower(), i.art), "")})
                for i in s.visual.items
            ]
            s = s.model_copy(update={"visual": s.visual.model_copy(update={"items": items})})
        scenes.append(s)
    return new.model_copy(update={"scenes": scenes})


# ---- writing -----------------------------------------------------------------------------


def _wrong(plan: Plan, length: str, known) -> list[str]:
    found = problems(plan, length)
    if known is not None:
        for n, s in enumerate(plan.scenes, 1):
            if s.visual.kind == "country" and s.visual.text and not known(s.visual.text):
                found.append(
                    f"scene {n}: {s.visual.text!r} isn't a country on the map; write a real "
                    "country's usual English name"
                )
    low, high = COUNTRIES.get(length, COUNTRIES["auto"])
    count = sum(1 for s in plan.scenes if s.visual.kind == "country")
    if count > high + 3:
        found.append(f"{count} countries is too many for this length; keep {low}-{high}")
    return found


def room(llm) -> None:
    """Give the writer room for a whole tour and its fixes."""
    if hasattr(llm, "context"):
        llm.context = max(llm.context or 0, CONTEXT)
    if hasattr(llm, "max_reply"):
        llm.max_reply = max(llm.max_reply or 0, REPLY)


def _ask(llm, messages, sources, voice, length, known, temperature) -> tuple[Plan, list[str]]:
    room(llm)
    reply = llm.chat_json(messages, SCHEMA, temperature=temperature)
    plan = tighten(to_plan(reply, sources, voice), length)
    wrong = _wrong(plan, length, known)
    if not wrong:
        return plan, []
    log.info("World map draft needs fixing: %s", "; ".join(wrong))
    fix = [
        *messages,
        {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)},
        {
            "role": "user",
            "content": "Fix these and answer with the whole reel again:\n- " + "\n- ".join(wrong),
        },
    ]
    try:
        again = tighten(
            to_plan(llm.chat_json(fix, SCHEMA, temperature=0.4), sources, voice), length
        )
    except LLMError as exc:
        log.warning("fixing the World map draft failed: %s", exc)
        again = plan
    left = _wrong(again, length, known)
    better = again if len(left) <= len(wrong) else plan
    if sum(1 for s in better.scenes if s.visual.kind == "country") < 2:
        from agentic_editor.animate.planner import PlannerError

        raise PlannerError("the writer didn't manage a World map reel: " + "; ".join(left or wrong))
    return better, left if better is again else wrong


def _brief_text(idea: str, length: str) -> str:
    low, high = MAP_WORDS.get(length, MAP_WORDS["auto"])
    few, many = COUNTRIES.get(length, COUNTRIES["auto"])
    return (
        f"Topic: {idea}\n{few}-{many} countries. Total narration (the hook and every "
        f"country together): {low}-{high} words."
    )


def draft(llm, idea: str, length: str, sources_text: str, sources, voice: str, known=None):
    """A first World map Plan for `idea`, and what is still off about it."""
    user = (
        f"{_brief_text(idea, length)}\n\nSources (use their facts where they help; most of "
        f"a reel like this is common knowledge):\n{sources_text}"
    )
    messages = [{"role": "system", "content": GUIDE}, {"role": "user", "content": user}]
    return _ask(llm, messages, sources, voice, length, known, temperature=0.7)


def change(llm, plan: Plan, idea: str, request: str, length: str, known=None):
    """`plan` changed as asked in plain words, in the same shape; pictures that still fit
    are kept."""
    user = (
        f"{_brief_text(idea, length)}\n\nHere is the reel as JSON:\n"
        f"{json.dumps(to_reply(plan), ensure_ascii=False)}\n\nChange it as asked: {request}\n"
        "Keep everything not asked about as it is. Answer with the whole reel."
    )
    messages = [{"role": "system", "content": GUIDE}, {"role": "user", "content": user}]
    changed, left = _ask(llm, messages, plan.sources, plan.voice, length, known, temperature=0.5)
    return _with_pictures(changed, plan), left


def redo_country(llm, plan: Plan, index: int, request: str = "") -> Plan:
    """`plan` with the country scene `index` written again (the same country unless the
    request says otherwise)."""
    scene = plan.scenes[index]
    ask = request.strip() or "make it better: the things it is most famous for, a sharper fact"
    user = (
        f"Here is a World map reel as JSON:\n{json.dumps(to_reply(plan), ensure_ascii=False)}"
        f"\n\nWrite the country {scene.visual.text!r} again ({ask}). Same shape: one sentence "
        "'<country> has <thing>, <thing>, and <thing>.' Answer with that one country."
    )
    reply = llm.chat_json(
        [{"role": "system", "content": GUIDE}, {"role": "user", "content": user}],
        COUNTRY_SCHEMA,
        temperature=0.8,
    )
    one = to_plan({"hook": "x", "countries": [reply]}, [], plan.voice).scenes
    if len(one) < 2:
        from agentic_editor.animate.planner import PlannerError

        raise PlannerError("the writer gave no country")
    scenes = list(plan.scenes)
    scenes[index] = one[1].model_copy(update={"hold": scene.hold})
    return _with_pictures(tidy(plan.model_copy(update={"scenes": scenes})), plan)
