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
from contextlib import contextmanager

from agentic_editor.animate.plan import (
    MAP_WORDS,
    MAX_COUNTRY_ITEMS,
    Item,
    Plan,
    Scene,
    Source,
    Visual,
    narration_words,
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


def parse(scene: Scene) -> tuple[str, list[str], list[str]]:
    """A country sentence as said: how it names the country ("The United States"), the
    short fact told after each thing ("" for none) and how each thing was said ("the
    telephone"; its name when it can't be told apart), in the order of the scene's items."""
    v = scene.visual
    names = [set(_words(i.text)) - FILLER for i in v.items]
    country = set(_words(v.text)) - FILLER
    head, has, _ = scene.narration.partition(" has ")
    opening = head.strip() if has and country and country <= set(_words(head)) else v.text
    facts = ["" for _ in v.items]
    said = [i.text for i in v.items]
    last = -1
    for part in re.split(r"[,;:]", scene.narration):
        part = re.sub(r"^\s*and\s+", "", part).strip().rstrip(".!")
        part = part.split(" has ", 1)[1].strip() if " has " in part else part
        words = set(_words(part)) - FILLER
        if not words:
            continue
        hits = [k for k, n in enumerate(names) if n and n <= words]
        named = hits[0] if hits else -1
        rest = words - country - set().union(*names) if names else words - country
        if named >= 0 and len(rest) <= 2:
            last = named
            if len(hits) == 1 and not rest:  # "the telephone": its own words, articles too
                said[named] = re.sub(r"^and\s+", "", part)
        elif len(rest) >= 3 and last >= 0 and not facts[last]:
            facts[last] = part
    return opening, facts, said


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
        _, facts, _ = parse(scene)
        for item, fact in zip(scene.visual.items, facts, strict=True):
            if fact:
                out.append(f"{scene.visual.text}, {item.text}: {fact}")
    return out


def sentence(opening: str, items: list[Item], facts: list[str], said=None) -> str:
    """'<Country> has A, B, and C.' with each kept fact after its thing, each thing as it
    was said (`said`) or by its name."""
    names = said or [i.text for i in items]
    parts = [f"{n}, {f}," if f else n for n, f in zip(names, facts, strict=True)]
    if len(parts) == 1:
        said = parts[0].rstrip(",")
    elif len(parts) == 2 and not facts[0]:
        said = f"{parts[0]} and {parts[1].rstrip(',')}"
    else:
        said = ", ".join(p.rstrip(",") for p in parts[:-1]) + f", and {parts[-1].rstrip(',')}"
    return f"{opening} has {said}."


def counted(opening: str, value: str, unit: str, items: list[Item], said=None) -> str:
    """A counted country's sentence: '<Country> has 5,580 nuclear bombs, including A and B.'"""
    names = said or [i.text for i in items]
    head = f"{opening} has {value} {unit}".rstrip()
    if not names:
        return head + "."
    listed = names[0] if len(names) == 1 else ", ".join(names[:-1]) + f" and {names[-1]}"
    return f"{head}, including {listed}."


def tighten(plan: Plan, length: str, unit: str = "") -> Plan:
    """A tour said the reference reels' way, whatever the writer wrote: each country's
    sentence made again from its cards ("<Country> has A, B, and C."), keeping at most one
    short fact a country and FACTS_PER_REEL in all (the first ones), then, while it runs
    past its length, fewer facts, at most ITEMS_WHEN_LONG things a country, fewer
    countries (never under MIN_COUNTRIES)."""
    from agentic_editor.animate.plan import MIN_COUNTRIES

    _, high = MAP_WORDS.get(length, MAP_WORDS["auto"])
    found = {
        n: parse(s)
        for n, s in enumerate(plan.scenes)
        if s.visual.kind == "country" and (s.visual.items or (unit and s.visual.value))
    }
    kept: dict[int, list[str]] = {}
    budget = FACTS_PER_REEL
    for n, (_, facts, _) in found.items():
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
                opening, _, said = found[n]
                items = scene.visual.items[:cap] if cap else scene.visual.items
                facts = scenes_kept[n][: len(items)]
                visual = scene.visual.model_copy(update={"items": items})
                if unit and re.search(r"\d", visual.value):
                    told = counted(opening, visual.value, unit, items, said[: len(items)])
                else:
                    told = sentence(opening, items, facts, said[: len(items)])
                scene = scene.model_copy(update={"narration": told, "visual": visual})
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


# Emoji that show nothing in particular (a dot, a square): a card shows its country's
# other emoji instead.
PLAIN_EMOJI = set("⚫⚪⬛⬜🔴🔵🟠🟡🟢🟣🟤🟥🟦🟧🟨🟩🟪🟫▪▫◾◽◼◻🔘❓❔")


def _emoji_of(items: list[dict]) -> list[str]:
    said = [str(i.get("emoji", "")).strip() for i in items]
    plain = [e.rstrip("\ufe0f") in PLAIN_EMOJI or not e for e in said]
    good = [e for e, p in zip(said, plain, strict=True) if not p]
    return [good[0] if p and good else ("" if p else e) for e, p in zip(said, plain, strict=True)]


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
        given = [i for i in c.get("items", []) if isinstance(i, dict)]
        items = [
            Item(text=str(i.get("name", "")), emoji=e, art=str(i.get("picture", "")))
            for i, e in zip(given, _emoji_of(given), strict=True)
        ]
        number = str(c.get("number", "")).strip()
        scenes.append(
            Scene(
                narration=str(c.get("narration", "")),
                visual=Visual(
                    kind="country", text=str(c.get("country", "")), items=items,
                    value=number if re.fullmatch(r"\d[\d,.]*", number) else "",
                ),
            )
        )  # fmt: skip
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
            **({"number": s.visual.value} if re.search(r"\d", s.visual.value) else {}),
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
    countries = [s for s in plan.scenes if s.visual.kind == "country"]
    if len(countries) > high + 3:
        found.append(f"{len(countries)} countries is too many for this length; keep {low}-{high}")
    if any("narration is" in f and "needs" in f for f in found):
        # Too short: what to add, said plainly (a small model doesn't work it out).
        few = [s.visual.text for s in countries if len(s.visual.items) < 3][:6]
        found.append(
            "name more famous things: 3-5 for each big country"
            + (f" (e.g. {', '.join(few)})" if few else "")
            + f", and tour {low}-{high} countries"
        )
    return found


def room(llm) -> None:
    """Give the writer room for a whole tour and its fixes."""
    if hasattr(llm, "context"):
        llm.context = max(llm.context or 0, CONTEXT)
    if hasattr(llm, "max_reply"):
        llm.max_reply = max(llm.max_reply or 0, REPLY)


def _ask(
    llm, messages, sources, voice, length, known, temperature, schema=None
) -> tuple[Plan, list[str], dict]:
    """The writer's tour, sent back once with what is off; and the answer it came from."""
    schema = schema or SCHEMA
    room(llm)
    reply = llm.chat_json(messages, schema, temperature=temperature)
    plan = tighten(to_plan(reply, sources, voice), length)
    wrong = _wrong(plan, length, known)
    if not wrong:
        return plan, [], reply
    log.info("World map draft needs fixing: %s", "; ".join(wrong))
    fix = [
        *messages,
        {"role": "assistant", "content": json.dumps(reply, ensure_ascii=False)},
        {
            "role": "user",
            "content": "Fix these and answer with the whole reel again:\n- " + "\n- ".join(wrong),
        },
    ]
    second = reply
    try:
        second = llm.chat_json(fix, schema, temperature=0.4)
        again = tighten(to_plan(second, sources, voice), length)
    except LLMError as exc:
        log.warning("fixing the World map draft failed: %s", exc)
        again = plan
    left = _wrong(again, length, known)
    better = again if len(left) <= len(wrong) else plan
    if sum(1 for s in better.scenes if s.visual.kind == "country") < 2:
        from agentic_editor.animate.planner import PlannerError

        raise PlannerError("the writer didn't manage a World map reel: " + "; ".join(left or wrong))
    if better is again:
        return better, left, second
    return better, wrong, reply


def _brief_text(idea: str, length: str) -> str:
    low, high = MAP_WORDS.get(length, MAP_WORDS["auto"])
    few, many = COUNTRIES.get(length, COUNTRIES["auto"])
    return (
        f"Topic: {idea}\n{few}-{many} countries. Total narration (the hook and every "
        f"country together): {low}-{high} words."
    )


# The kind of thing a topic is about: "inventions" in "Most famous inventions from every
# country".
KIND_OF = re.compile(
    r"(?:most\s+(?:famous|popular|iconic|haunted)|popular|famous|biggest|best|greatest)\s+"
    r"(.+?)\s+(?:from|in|of|around)\b",
    re.I,
)
HOW_MANY = re.compile(r"\bhow\s+many\s+(.+?)\s+(?:every|each|does|do|in|per)\b", re.I)
TOP_N = re.compile(r"\btop\s*\d+\b|\branked\b|\branking\b|\bfrom\s+worst\s+to\s+best\b", re.I)


def kind_of(idea: str) -> str:
    found = KIND_OF.search(idea) or HOW_MANY.search(idea)
    if found:
        return found.group(1).strip()
    rest = re.sub(r"^\s*top\s*\d+\s+", "", idea.strip(), flags=re.I)
    return rest.split(" from ")[0].split(" in ")[0].strip() or idea.strip()


# ---- the look and the badges, by the topic -------------------------------------------------

HORROR = re.compile(
    r"haunt|scar(y|iest)|creep|ghost|horror|cursed|spook|terrif|eerie|monster|demon|"
    r"abandoned|mysterious death|serial killer|nightmare",
    re.I,
)
FOOD = re.compile(
    r"food|dish|dessert|sweet|candy|candies|snack|breakfast|soup|bread|cheese|street eat|"
    r"cuisine|meal|pastr|cake|cookie|biscuit|noodle|dumpling|sandwich|burger|pizza|curr(y|ies)|"
    r"\bdrinks?\b|beverage|cocktail|juice|smoothie|tea\b|coffee|fruit|spice",
    re.I,
)


def look_of(idea: str) -> str:
    """The reference page's look for a topic: gold horror for haunted and scary ones, neon
    labels for food and drink, white picture cards for the rest."""
    if HORROR.search(idea):
        return "horror"
    if FOOD.search(kind_of(idea)) or FOOD.search(idea):
        return "neon"
    return "cards"


def ranking(idea: str) -> str:
    """How a topic numbers its countries: "count" for "How many X every country has" (a
    badge with each country's number), "rank" for "Top 10 ..." (a badge with its place),
    "" for none."""
    if HOW_MANY.search(idea) or re.search(r"\bnumber of\b", idea, re.I):
        return "count"
    if TOP_N.search(idea):
        return "rank"
    return ""


RANK_ICON = "🏆"
COUNT_ICON = "🔢"
RANK_GUIDE = """
This topic is a ranking: list the countries from the one ranked lowest to number one
(number one comes last). The tour order then follows the ranking, not the map."""
COUNT_GUIDE = """
This topic counts something in each country: give each country "number", how many it has,
as digits with commas (e.g. "5,580"), only a number you are sure of from the sources or
common knowledge (leave a country out when you aren't). Order the countries from the
smallest number to the biggest. Its narration says the number: "<country> has <number>
<what is counted>, including <thing> and <thing>." Name 0-2 things (e.g. a famous model),
and give the reel one "badge_emoji" for what is counted."""


def badges(plan: Plan, idea: str, reply: dict | None = None) -> Plan:
    """The countries numbered as the topic asks (`ranking`): each country scene's badge
    (Visual.value, its icon Visual.emoji) from its place in the tour or the writer's
    number; none for other topics (and none kept from an earlier topic)."""
    how = ranking(idea)
    numbers: dict[str, str] = {}
    icon = COUNT_ICON
    if how == "count" and reply:
        icon = _emoji_of([{"emoji": reply.get("badge_emoji", "")}])[0] or COUNT_ICON
        for c in reply.get("countries", []):
            if isinstance(c, dict) and re.fullmatch(r"\d[\d,.]*", str(c.get("number", "")).strip()):
                numbers[_plain_name(str(c.get("country", "")))] = str(c["number"]).strip()
    countries = [n for n, s in enumerate(plan.scenes) if s.visual.kind == "country"]
    scenes = list(plan.scenes)
    for place, n in enumerate(countries):
        v = scenes[n].visual
        if how == "rank":
            value, emoji = f"#{len(countries) - place}", RANK_ICON
        elif how == "count":
            given = numbers.get(_plain_name(v.text), v.value if re.search(r"\d", v.value) else "")
            value, emoji = (given, icon) if given else ("", v.emoji)
        else:
            value, emoji = "", v.emoji
        scenes[n] = scenes[n].model_copy(
            update={"visual": v.model_copy(update={"value": value, "emoji": emoji})}
        )
    return plan.model_copy(update={"scenes": scenes})


def _plain_name(text: str) -> str:
    return " ".join(w for w in _words(text) if w not in FILLER)


# ---- the strict check: each thing on its own -------------------------------------------------

# What a thing can be. The check asks for one; the topic's kind allows some (KIND_CATEGORIES).
# The list is in the question too: a model that only has the reply's grammar starts its own
# word and gets the nearest label ("animated" became "animal", "franchise" "fruit, ...").
CATEGORIES = (
    "food or dish", "dessert or sweet", "drink", "fruit, plant or spice", "animal",
    "place, building or landmark", "natural wonder", "festival, holiday or event", "person",
    "invention, technology or machine", "vehicle", "brand or company", "sports team or club",
    "film, TV show, cartoon or anime", "book, comic or story", "painting or artwork", "video game",
    "music, song, dance or instrument", "sport or game", "clothing",
    "myth or legendary creature", "other",
)  # fmt: skip
CATEGORY_LIST = "; ".join(CATEGORIES)
# The categories a topic's things may be in, by words of its kind: the first that matches.
# (Brands before what they make: "sportswear brands" are brands, not sports.)
KIND_CATEGORIES = (
    (r"brand|compan|airline|chain|maker|label|manufacturer|platform|apps?\b",
     {"brand or company"}),
    (r"club|team", {"sports team or club"}),
    (r"myth|legend|creature|monster|folklore|cryptid", {"myth or legendary creature"}),
    (r"scientist|painter|artist|writer|author|poet|warrior|footballer|player|athlete|singer|"
     r"actor|actress|people|person|leader|\bkings?\b|\bqueens?\b|inventor|celebrit|\bstars?\b|"
     r"youtuber|chef|explorer|hero|villain|composer|director|billionaire|creator",
     {"person"}),
    (r"invent|discover|technolog|gadget|machine",
     {"invention, technology or machine", "vehicle"}),
    (r"dessert|sweet|cand(y|ies)|chocolate|cake|pastr|cookie|biscuit",
     {"dessert or sweet", "food or dish"}),
    (r"\bdrinks?\b|beverage|soda|juice|\btea\b|coffee|cocktail|smoothie", {"drink"}),
    (r"food|dish|cuisine|breakfast|soup|bread|cheese|snack|meal|noodle|dumpling|sandwich|"
     r"burger|curr(y|ies)|sauce|pizza",
     {"food or dish", "dessert or sweet"}),
    (r"fruit|spice|herb|flower|plant|vegetable|\btrees?\b", {"fruit, plant or spice"}),
    (r"festival|holiday|celebration|carnival|tradition|ritual|parade",
     {"festival, holiday or event"}),
    (r"castle|palace|fort\b|fortress|cathedral|temple|church|mosque|bridge|tower|skyscraper",
     {"place, building or landmark"}),
    (r"mountain|river|lake|island|beach|waterfall|volcano|desert|forest|cave|natural",
     {"natural wonder", "place, building or landmark"}),
    (r"haunt|castle|palace|landmark|building|bridge|temple|church|cathedral|tower|monument|"
     r"place|ruin|cit(y|ies)|wonder|stadium|museum|prison|hotel|street|square",
     {"place, building or landmark", "natural wonder"}),
    (r"video ?game", {"video game"}),
    (r"cartoon|anime|animat|tv show|series|movie|film|\bshows?\b|sitcom|soap",
     {"film, TV show, cartoon or anime", "book, comic or story"}),
    (r"book|novel|stor(y|ies)|fairy tale|poem|comic|manga", {"book, comic or story"}),
    (r"painting|artwork|sculpture|statue|\bart\b", {"painting or artwork"}),
    (r"board game|card game|\bgames?\b|toy",
     {"sport or game", "video game", "invention, technology or machine"}),
    (r"\bcars?\b|vehicle|trains?\b|motorbike|motorcycle|plane|aircraft|ship|boat",
     {"vehicle", "invention, technology or machine"}),
    (r"animal|bird|dog|cats?\b|breed|fish|insect", {"animal"}),
    (r"dance|instrument|music|song|anthem", {"music, song, dance or instrument"}),
    (r"sport|martial art", {"sport or game"}),
    (r"cloth|dress|costume|\bhats?\b|shoe|sneaker|fashion|wear", {"clothing", "brand or company"}),
)  # fmt: skip
YES_NO = ["yes", "no", "unsure"]
CHECK_SCHEMA = {
    "type": "object",
    "properties": {
        "what": {"type": "string"},
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "is_kind": {"type": "string", "enum": YES_NO},
        "from_country": {"type": "string", "enum": YES_NO},
        "famous": {"type": "string", "enum": YES_NO},
    },
    "required": ["what", "category", "is_kind", "from_country", "famous"],
}
KIND_SCHEMA = {
    "type": "object",
    "properties": {
        "plain": {"type": "string"},
        "answer": {"type": "string", "enum": YES_NO},
        "origin": {"type": "string"},
    },
    "required": ["plain", "answer", "origin"],
}
ALLOWED_SCHEMA = {
    "type": "object",
    "properties": {
        "categories": {
            "type": "array",
            "items": {"type": "string", "enum": list(CATEGORIES)},
            "minItems": 1,
            "maxItems": 4,
        }
    },
    "required": ["categories"],
}
REFILL_SCHEMA = {
    "type": "object",
    "properties": {"items": {"type": "array", "items": _ITEM, "minItems": 1, "maxItems": 6}},
    "required": ["items"],
}
MORE_SCHEMA = {
    "type": "object",
    "properties": {
        "countries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "country": {"type": "string"},
                    "items": {"type": "array", "items": _ITEM, "minItems": 1, "maxItems": 4},
                },
                "required": ["country", "items"],
            },
            "minItems": 1,
            "maxItems": 8,
        }
    },
    "required": ["countries"],
}
DUP_SCHEMA = {
    "type": "object",
    "properties": {
        "same": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"first": {"type": "integer"}, "second": {"type": "integer"}},
                "required": ["first", "second"],
            },
        }
    },
    "required": ["same"],
}
ICONIC_SCHEMA = {
    "type": "object",
    "properties": {
        "examples": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"country": {"type": "string"}, **_ITEM["properties"]},
                "required": ["country", *_ITEM["required"]],
            },
            "minItems": 3,
            "maxItems": 5,
        }
    },
    "required": ["examples"],
}
CHECK_TOKENS = 200  # a check's answer is short: a stuck model can't run for minutes
MIN_KEPT = 2  # a country left with fewer things than this is filled up again
REFILL_TO = 3  # ... to at most this many
VERDICTS_KEPT = 5000
VERDICT_VERSION = 2  # verdicts from an earlier check (other questions) are asked again


def allowed_for(kind: str) -> set[str] | None:
    """The categories a topic's things may be in, by its kind's words; None when no rule
    knows the kind (the model is asked then)."""
    for pattern, categories in KIND_CATEGORIES:
        if re.search(pattern, kind, re.I):
            return set(categories)
    return None


def _norm(text: str) -> str:
    import unicodedata

    flat = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return " ".join(w for w in _words(flat) if w not in {"the", "a", "an"})


class Checks:
    """Verdicts of the strict check by (kind, country, thing), kept in a JSON file across
    reels: a thing judged once isn't asked about again, a known good one stays good."""

    def __init__(self, path=None):
        import threading

        self.path = path
        self.lock = threading.Lock()
        self.found: dict[str, dict] = {}
        if path is not None and path.is_file():
            try:
                self.found = json.loads(path.read_text(encoding="utf-8"))
            except ValueError:
                self.found = {}

    @staticmethod
    def key(kind: str, country: str, thing: str) -> str:
        return f"{_norm(kind)}|{_norm(country)}|{_norm(thing)}"

    def get(self, key: str) -> dict | None:
        found = self.found.get(key)
        return found if found and found.get("v") == VERDICT_VERSION else None

    def put(self, key: str, verdict: dict) -> None:
        with self.lock:
            self.found.pop(key, None)
            self.found[key] = verdict
            while len(self.found) > VERDICTS_KEPT:
                self.found.pop(next(iter(self.found)))

    def save(self) -> None:
        if self.path is None:
            return
        with self.lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            part = self.path.with_suffix(".part")
            part.write_text(json.dumps(self.found, ensure_ascii=False), encoding="utf-8")
            part.replace(self.path)


def _allowed(llm, idea: str, kind: str) -> set[str] | None:
    known = allowed_for(kind)
    if known is not None:
        return known
    try:
        with short_answers(llm, 120):
            reply = llm.chat_json(
                [{"role": "user", "content": (
                    f"A reel's topic: {idea}. Each thing it names must be one of the {kind}.\n"
                    "Which of these categories can such a thing be in? Pick only the ones that "
                    f"fit the topic exactly (1-3), written as shown: {CATEGORY_LIST}."
                )}],
                ALLOWED_SCHEMA,
                temperature=0.0,
            )  # fmt: skip
    except LLMError as exc:
        log.warning("asking what %s can be failed: %s", kind, exc)
        return None
    picked = {c for c in reply.get("categories", []) if c in CATEGORIES and c != "other"}
    return picked or None


@contextmanager
def short_answers(llm, most: int = CHECK_TOKENS):
    """The model's answers capped at `most` tokens while inside (a check, not a tour)."""
    if not hasattr(llm, "max_reply"):
        yield
        return
    before = llm.max_reply
    llm.max_reply = most
    try:
        yield
    finally:
        llm.max_reply = before


def judge(
    llm, idea: str, kind: str, country: str, thing: str, allowed, checks=None, known=None
) -> dict:
    """The strict check of one thing on its own: what it is, its category, whether it is
    one of the topic's kind, from the country and famous, then the kind and where it
    comes from asked again apart (`known`: the map, to match the country). `keep` only
    when every answer is a clear yes (and the category is one the topic allows)."""
    key = Checks.key(kind, country, thing)
    cached = checks.get(key) if checks else None
    if cached is not None:
        return cached
    with short_answers(llm):
        reply = llm.chat_json(
            [{"role": "user", "content": (
                "Check one thing a World map reel names.\n"
                f"Topic: {idea}. Each thing must be one of the {kind}.\n"
                f"Country: {country}. Thing: {thing}.\n"
                "Answer about the thing itself, strictly; when you aren't sure, answer unsure.\n"
                "what: what it is, in at most 8 words.\n"
                f"category: the one it belongs to, written exactly as one of: {CATEGORY_LIST}.\n"
                f"is_kind: is it truly one of the {kind} (not merely linked to them)?\n"
                f"from_country: does it come from {country}: made, invented, founded, created, "
                f"built or first done there (a place: is it in {country})?\n"
                "famous: would a typical viewer anywhere in the world recognise it (yes only "
                f"for one of the best known {kind} there are, known well outside {country})?"
            )}],
            CHECK_SCHEMA,
            temperature=0.0,
        )  # fmt: skip
    category = str(reply.get("category", "other"))
    answers = [str(reply.get(k, "unsure")).lower() for k in ("is_kind", "from_country", "famous")]
    fits = allowed is None or category in allowed
    what = " ".join(str(reply.get("what", "")).split())[:80]
    keep = fits and all(a == "yes" for a in answers)
    plain = origin = ""
    if keep:
        # A second, separate look: the kind alone (asked with the rest, a small model passed
        # near kinds: a palace for castles, a football final for festivals), and where it
        # comes from as an open question, matched to the card's country by the map (asked
        # "is it from Canada?" the model said yes to American cartoons).
        plain, again, origin = _kind_again(llm, idea, kind, country, thing, what)
        home = known is None or same_country(origin, country, known)
        answers += [again, "yes" if home else "no"]
        keep = again == "yes" and home
    verdict = {
        "keep": keep, "what": what, "category": category, "answers": answers,
        "v": VERDICT_VERSION,
    }  # fmt: skip
    if plain:
        verdict["plain"], verdict["origin"] = plain, origin
    if checks is not None:
        checks.put(key, verdict)
    return verdict


def _kind_again(llm, idea, kind, country, thing, what) -> tuple[str, str, str]:
    """What `thing` plainly is (one or two words), whether that is one of the topic's
    `kind` (yes, no or unsure) and the country it comes from, as the model knows it."""
    with short_answers(llm, 80):
        reply = llm.chat_json(
            [{"role": "user", "content": (
                f"A reel about {idea} must show only {kind}. On the {country} card: {thing} "
                f"({what}).\n"
                f"plain: what {thing} is, in one or two plain words (like river, violin, "
                "teapot).\n"
                f"answer: is that one of the {kind}? No when it is another kind of thing that "
                f"is only linked with {kind}; unsure when you aren't sure.\n"
                f"origin: the country {thing} comes from: where it was made, invented, founded, "
                "created or first done (a place: where it is), as the country's usual English "
                "name; unknown when you don't know."
            )}],
            KIND_SCHEMA,
            temperature=0.0,
        )  # fmt: skip
    plain = " ".join(str(reply.get("plain", "")).split())[:40]
    origin = " ".join(str(reply.get("origin", "")).split())[:60]
    return plain, str(reply.get("answer", "unsure")).lower(), origin


def _sorted_out(llm, idea, kind, country, items, allowed, checks, known=None):
    """`items` split into the ones that pass the strict check and the rest. One the model
    couldn't be asked about isn't kept (a wrong card is the worst outcome); when it
    couldn't be asked about any, the model isn't answering: LLMError."""
    kept, gone, failed, answered = [], [], 0, 0
    for item in items:
        if not item.text.strip():
            continue
        try:
            verdict = judge(llm, idea, kind, country, item.text, allowed, checks, known)
            answered += 1
        except LLMError as exc:
            log.warning("checking %s (%s) failed: %s", item.text, country, exc)
            verdict, failed = {"keep": False}, failed + 1
        (kept if verdict["keep"] else gone).append(item)
    if failed and not answered:
        raise LLMError(f"the check didn't answer for {country}")
    return kept, gone


# Words that say what kind of thing a name is, not which one: "Rio Carnival" and "Carnival
# of Venice" are different things however many such words they share.
GENERIC = {
    "festival", "festivals", "day", "night", "fest", "celebration", "carnival", "parade",
    "castle", "palace", "house", "hotel", "fort", "fortress", "tower", "national", "park",
    "world", "great", "grand", "old", "new", "royal", "saint", "st", "of", "and", "de", "di",
    "del", "la", "le", "el", "der", "des", "du", "il", "lo", "das", "die",
}  # fmt: skip


def _one(word: str) -> str:
    """A word without its plural ending: "churros" is "churro", "pastries" "pastry"."""
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("ches", "shes", "sses", "xes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith(("ss", "us", "is")):
        return word[:-1]
    return word


def _folded(text: str) -> str:
    """A name as compared: _norm, each word without its plural ending."""
    return " ".join(_one(w) for w in _norm(text).split())


def same_by_name(a: str, b: str) -> bool:
    """Whether two names are the same thing as written: the same words (plurals aside), or
    the words of one inside the other's (leaving out GENERIC words): "Mardi Gras" and "New
    Orleans Mardi Gras"."""
    x, y = set(_folded(a).split()), set(_folded(b).split())
    if x == y:
        return True
    x, y = x - GENERIC, y - GENERIC
    return bool(x) and bool(y) and (x <= y or y <= x)


def distinct(llm, idea: str, kind: str, country: str, items: list[Item]) -> tuple[list, list]:
    """`items` without the ones that name a thing already there: the same words or one
    inside the other (same_by_name), else by the model: another name, another language
    ("the Venice Carnival" and "Carnevale di Venezia"). The first stays."""
    kept, gone = [], []
    for item in items:
        (gone if any(same_by_name(item.text, k.text) for k in kept) else kept).append(item)
    if len(kept) < 2:
        return kept, gone
    lines = "\n".join(f"{n}. {i.text}" for n, i in enumerate(kept, 1))
    try:
        with short_answers(llm):
            reply = llm.chat_json(
                [{"role": "user", "content": (
                    f"The cards for {country} in a reel about {idea}:\n{lines}\n\n"
                    f"Do any two of them name the same one of the {kind}: under another name, in "
                    "another language, a nickname, or one is part of the other? List only "
                    "clear pairs by number (first, second); none: an empty list."
                )}],
                DUP_SCHEMA,
                temperature=0.0,
            )  # fmt: skip
    except LLMError as exc:
        log.warning("asking which of %s's things are the same failed: %s", country, exc)
        return kept, gone
    second = set()
    for pair in reply.get("same", []):
        if not isinstance(pair, dict):
            continue
        a, b = pair.get("first"), pair.get("second")
        numbers = isinstance(a, int) and isinstance(b, int) and a != b
        if numbers and min(a, b) >= 1 and max(a, b) <= len(kept):
            second.add(max(a, b) - 1)
    return [i for n, i in enumerate(kept) if n not in second], [
        *gone,
        *(i for n, i in enumerate(kept) if n in second),
    ]


def iconic(llm, idea: str, kind: str) -> list[tuple[str, Item]]:
    """The topic's headline things a viewer would miss if a tour left them out
    (Oktoberfest for festivals), with their countries, as the model names them."""
    try:
        with short_answers(llm, 700):
            reply = llm.chat_json(
                [{"role": "user", "content": (
                    f"Topic: {idea}. Name the 5 most famous {kind} in the whole world: the ones "
                    "nearly everyone has heard of, that a viewer would be surprised to see left "
                    "out. Each with its country (the usual English name), an emoji and its picture "
                    "(at most 8 words, no logo, no writing)."
                )}],
                ICONIC_SCHEMA,
                temperature=0.0,
            )  # fmt: skip
    except LLMError as exc:
        log.warning("asking for the most famous %s failed: %s", kind, exc)
        return []
    found = []
    for e in reply.get("examples", []):
        if isinstance(e, dict) and str(e.get("country", "")).strip():
            items = _items([e])
            if items:
                found.append((" ".join(str(e["country"]).split()), items[0]))
    return found


def same_country(a: str, b: str, known=None) -> bool:
    """Whether two names are one country: by the map when it knows both ("England" and
    "United Kingdom" are GBR), else by their words."""
    code = getattr(known, "code", None)
    if code is not None:
        x, y = code(a), code(b)
        if x is not None and y is not None:
            return x == y
    return bool(_plain_name(a)) and _plain_name(a) == _plain_name(b)


def _tour_items(scenes: list) -> list[Item]:
    return [i for s in scenes if s.visual.kind == "country" for i in s.visual.items]


def _near(scenes: list, name: str, locate) -> int:
    """Where a country goes in the tour: right after the one nearest to it on the map (the
    tour goes round the world region by region); without a map, before the last one."""
    countries = [n for n, s in enumerate(scenes) if s.visual.kind == "country"]
    if not countries:
        return len(scenes)
    spot = locate(name) if locate else None
    if spot is None:
        return countries[-1]
    best, far = countries[-1] + 1, None
    for n in countries:
        there = locate(scenes[n].visual.country or scenes[n].visual.text)
        if there is None:
            continue
        d = ((spot[0] - there[0]) ** 2 + (spot[1] - there[1]) ** 2) ** 0.5
        if far is None or d < far:
            best, far = n + 1, d
    return best


def headliners(llm, idea, kind, plan: Plan, allowed, checks, known=None) -> tuple[Plan, list[str]]:
    """The topic's headline things put in when a tour left them out (each checked like
    the rest): first on their country's cards, or their country added near its
    neighbours."""
    scenes = list(plan.scenes)
    added = []
    locate = getattr(known, "locate", None)
    for country, item in iconic(llm, idea, kind):
        if known is not None and not known(country):
            continue
        if any(same_by_name(item.text, i.text) for i in _tour_items(scenes)):
            continue  # there already
        home = next(
            (n for n, s in enumerate(scenes) if s.visual.kind == "country"
             and same_country(s.visual.text, country, known)),
            None,
        )  # fmt: skip
        if home is not None:
            country = scenes[home].visual.text  # the tour's own name for it
        kept, _ = _sorted_out(llm, idea, kind, country, [item], allowed, checks, known)
        if not kept:
            continue
        if home is not None:
            v = scenes[home].visual
            items = [kept[0], *v.items][:MAX_COUNTRY_ITEMS]
            scenes[home] = scenes[home].model_copy(
                update={"visual": v.model_copy(update={"items": items})}
            )
        else:
            more = refill(
                llm, idea, kind, country, kept, _tour_items(scenes), MIN_KEPT - 1, allowed,
                checks, known,
            )  # fmt: skip
            items = [*kept, *more]
            scene = Scene(
                narration=sentence(country, items, ["" for _ in items]),
                visual=Visual(kind="country", text=country, items=items),
            )
            scenes.insert(_near(scenes, country, locate), scene)
        added.append(f"{country}: {item.text}")
    notes = ["put in as the topic's best known: " + ", ".join(added)] if added else []
    return plan.model_copy(update={"scenes": scenes}), notes


def _items(given: list) -> list[Item]:
    good = [i for i in given if isinstance(i, dict) and str(i.get("name", "")).strip()]
    return [
        Item(text=str(i.get("name", "")).strip(), emoji=e, art=str(i.get("picture", "")))
        for i, e in zip(good, _emoji_of(good), strict=True)
    ]


def refill(llm, idea, kind, country, have, avoid, need, allowed, checks, known=None) -> list[Item]:
    """Up to `need` more things for `country` that pass the strict check: the writer
    names a few more (not `have`, not `avoid`), each is checked on its own."""
    if need <= 0:
        return []
    named = ", ".join(i.text for i in [*have, *avoid]) or "none"
    try:
        with short_answers(llm, 700):
            reply = llm.chat_json(
                [{"role": "system", "content": GUIDE}, {"role": "user", "content": (
                    f"Topic: {idea}. Name {need + 2} more {kind} that truly come from {country} "
                    f"and are famous, the best known first, none of these: {named}. Only real "
                    f"{kind} from {country}; fewer is better than a wrong one. Each: name, emoji, "
                    "picture."
                )}],
                REFILL_SCHEMA,
                temperature=0.3,
            )  # fmt: skip
    except LLMError as exc:
        log.warning("filling %s up failed: %s", country, exc)
        return []
    taken = [*have, *avoid]
    fresh = [
        i for i in _items(reply.get("items", []))
        if not any(same_by_name(i.text, t.text) for t in taken)
    ]  # fmt: skip
    kept, _ = _sorted_out(llm, idea, kind, country, fresh, allowed, checks, known)
    return kept[:need]


def more_countries(llm, idea, kind, plan: Plan, need: int, allowed, checks, known=None):
    """Country scenes to add when too few are left: the writer names more, each thing is
    checked; a country with nothing left isn't added."""
    have = [s.visual.text for s in plan.scenes if s.visual.kind == "country"]
    try:
        with short_answers(llm, 1400):
            reply = llm.chat_json(
                [{"role": "system", "content": GUIDE}, {"role": "user", "content": (
                    f"Topic: {idea}. Name {need + 2} more countries, none of these: "
                    f"{', '.join(have)}; for each, 2-3 {kind} that truly come from it and are "
                    f"famous. Only real {kind}; fewer is better than a wrong one."
                )}],
                MORE_SCHEMA,
                temperature=0.3,
            )  # fmt: skip
    except LLMError as exc:
        log.warning("finding more countries failed: %s", exc)
        return []
    taken = list(have)
    named = _tour_items(plan.scenes)
    scenes = []
    for c in reply.get("countries", []):
        if len(scenes) >= need or not isinstance(c, dict):
            continue
        name = " ".join(str(c.get("country", "")).split())
        if not name or any(same_country(name, t, known) for t in taken):
            continue
        if known is not None and not known(name):
            continue
        fresh = [
            i for i in _items(c.get("items", []))
            if not any(same_by_name(i.text, t.text) for t in named)
        ]  # fmt: skip
        kept, _ = _sorted_out(llm, idea, kind, name, fresh, allowed, checks, known)
        if not kept:
            continue
        taken.append(name)
        named += kept
        scenes.append(
            Scene(
                narration=sentence(name, kept, ["" for _ in kept]),
                visual=Visual(kind="country", text=name, items=kept[:MAX_COUNTRY_ITEMS]),
            )
        )
    return scenes


def vet(llm, plan: Plan, idea: str, checks=None, known=None) -> tuple[Plan, list[str]]:
    """The tour with only things that pass the strict check (`judge`, each thing on its
    own: of the topic's kind, from its country, famous; anything unsure goes). A country
    left with too few is filled up again with checked things; one left with none goes;
    when too few countries are left, more are found and checked. Known verdicts
    (`checks`) are used and kept. Returns what was left out, for the person."""
    from agentic_editor.animate.plan import MIN_COUNTRIES

    kind = kind_of(idea)
    if not any(s.visual.kind == "country" and s.visual.items for s in plan.scenes):
        return plan, []
    allowed = _allowed(llm, idea, kind)
    counted = ranking(idea) == "count"
    scenes, left_out, gone_countries = [], [], []
    seen_whole: dict[str, list[str]] = {}  # each country's cards as the duplicates check saw them
    for scene in plan.scenes:
        v = scene.visual
        if v.kind != "country":
            scenes.append(scene)
            continue
        kept, gone = _sorted_out(llm, idea, kind, v.text, v.items, allowed, checks, known)
        kept, twice = distinct(llm, idea, kind, v.text, kept)
        seen_whole[v.text] = [i.text for i in kept]
        gone += twice
        if (len(kept) < MIN_KEPT and len(v.items) >= MIN_KEPT) or (not kept and v.items):
            want = min(REFILL_TO, max(len(v.items), MIN_KEPT)) - len(kept)
            elsewhere = [*_tour_items(plan.scenes), *_tour_items(scenes)]
            kept += refill(
                llm, idea, kind, v.text, kept, [*gone, *elsewhere], want, allowed, checks, known
            )
        left_out += [f"{v.text}: {g.text}" for g in gone]
        if not kept and not (counted and v.value):
            gone_countries.append(v.text)
            continue
        scenes.append(scene.model_copy(update={"visual": v.model_copy(update={"items": kept})}))
    scenes = _once_in_the_tour(scenes, left_out)
    out = plan.model_copy(update={"scenes": scenes})
    headline_notes = []
    if not ranking(idea):  # a ranking or a count has its own order and its own countries
        out, headline_notes = headliners(llm, idea, kind, out, allowed, checks, known)
    countries = sum(1 for s in out.scenes if s.visual.kind == "country")
    if countries < MIN_COUNTRIES:
        extra = more_countries(
            llm, idea, kind, out, MIN_COUNTRIES - countries, allowed, checks, known
        )
        out = out.model_copy(update={"scenes": [*out.scenes, *extra]})
    # Whatever was put in afterwards (refills, the best known things, more countries):
    # still each thing once on a country's cards, and once in the tour.
    out = out.model_copy(update={"scenes": _distinct_again(llm, idea, kind, out.scenes,
                                                           seen_whole, left_out)})  # fmt: skip
    out = out.model_copy(update={"scenes": _once_in_the_tour(list(out.scenes), left_out)})
    if checks is not None:
        checks.save()
    notes = []
    if left_out:
        notes.append("left out as not sure to fit the topic: " + ", ".join(left_out))
    if gone_countries:
        notes.append("countries left out (nothing sure to show): " + ", ".join(gone_countries))
    return out, [*notes, *headline_notes]


def _distinct_again(llm, idea, kind, scenes, seen_whole: dict, left_out: list[str]) -> list:
    """The duplicates check again for each country whose cards changed after it ran
    ("Día de los Muertos" refilled next to "Day of the Dead", the best known "Chinese New
    Year" put in next to "Lunar New Year")."""
    out = []
    for scene in scenes:
        v = scene.visual
        if v.kind != "country" or [i.text for i in v.items] == seen_whole.get(v.text):
            out.append(scene)
            continue
        kept, twice = distinct(llm, idea, kind, v.text, list(v.items))
        left_out += [f"{v.text}: {t.text} (the same as another card)" for t in twice]
        out.append(scene.model_copy(update={"visual": v.model_copy(update={"items": kept})}))
    return out


def _once_in_the_tour(scenes: list, left_out: list[str]) -> list:
    """Each thing once in the whole tour (the telephone for both the US and the UK): a
    later country loses its copy (and goes, with nothing left)."""
    seen: list[str] = []
    out = []
    for scene in scenes:
        v = scene.visual
        if v.kind != "country" or not v.items:
            out.append(scene)
            continue
        items = []
        for item in v.items:
            if any(_folded(item.text) == other for other in seen):
                left_out.append(f"{v.text}: {item.text} (named for another country already)")
                continue
            seen.append(_folded(item.text))
            items.append(item)
        if items or v.value:
            out.append(scene.model_copy(update={"visual": v.model_copy(update={"items": items})}))
    return out


def _brief_for(idea: str, length: str) -> str:
    extra = {"rank": RANK_GUIDE, "count": COUNT_GUIDE}.get(ranking(idea), "")
    return _brief_text(idea, length) + extra


def _schema_for(idea: str) -> dict:
    if ranking(idea) != "count":
        return SCHEMA
    country = {**_COUNTRY, "properties": {**_COUNTRY["properties"], "number": {"type": "string"}}}
    country["required"] = [*_COUNTRY["required"], "number"]
    country["properties"]["items"] = {**_COUNTRY["properties"]["items"], "minItems": 0}
    return {
        **SCHEMA,
        "properties": {
            **SCHEMA["properties"],
            "countries": {**SCHEMA["properties"]["countries"], "items": country},
            "badge_emoji": {"type": "string"},
        },
        "required": [*SCHEMA["required"], "badge_emoji"],
    }


def finish(plan: Plan, idea: str, length: str, reply: dict | None = None) -> Plan:
    """The tour said the reference way, in the topic's look, numbered as the topic asks."""
    numbered = badges(plan, idea, reply)
    return tighten(numbered, length, unit=kind_of(idea) if ranking(idea) == "count" else "")


def draft(
    llm, idea: str, length: str, sources_text: str, sources, voice: str, known=None, checks=None
):
    """A first World map Plan for `idea`, and what is still off about it: written, every
    thing checked on its own (vet, filled up where too little is left), and said the
    reference way again; in the topic's look (look_of), its countries numbered when it is
    a ranking or a count (badges)."""
    user = (
        f"{_brief_for(idea, length)}\n\nSources (use their facts where they help; most of "
        f"a reel like this is common knowledge):\n{sources_text}"
    )
    messages = [{"role": "system", "content": GUIDE}, {"role": "user", "content": user}]
    plan, _, reply = _ask(
        llm, messages, sources, voice, length, known, temperature=0.7, schema=_schema_for(idea)
    )
    plan = badges(plan, idea, reply)  # before the check: a count may keep a country bare
    plan, removed = _vetted(llm, plan, idea, checks, known)
    plan = finish(plan, idea, length, reply).model_copy(update={"look": look_of(idea)})
    return plan, [*removed, *_wrong(plan, length, known)]


def _vetted(llm, plan: Plan, idea: str, checks=None, known=None) -> tuple[Plan, list[str]]:
    try:
        return vet(llm, plan, idea, checks, known)
    except LLMError as exc:
        # Not checked: every thing stays, said plainly to the person.
        log.warning("checking the World map tour failed: %s", exc)
        return plan, ["the things on the cards couldn't be checked; look them over"]


def _vet_new(llm, plan: Plan, old: Plan, idea: str, checks, known) -> tuple[Plan, list[str]]:
    """`vet` for a changed tour: things it had before are known good (they were checked
    then), so they are put in the verdicts before the new ones are judged."""
    checks = checks if checks is not None else Checks(None)
    kind = kind_of(idea)
    for s in old.scenes:
        if s.visual.kind != "country":
            continue
        for i in s.visual.items:
            key = Checks.key(kind, s.visual.text, i.text)
            if checks.get(key) is None:
                checks.put(
                    key,
                    {"keep": True, "what": "", "category": "", "answers": [], "v": VERDICT_VERSION},
                )
    return _vetted(llm, plan, idea, checks, known)


def change(llm, plan: Plan, idea: str, request: str, length: str, known=None, checks=None):
    """`plan` changed as asked in plain words, in the same shape; pictures that still fit
    are kept, new things are checked like a draft's."""
    user = (
        f"{_brief_for(idea, length)}\n\nHere is the reel as JSON:\n"
        f"{json.dumps(to_reply(plan), ensure_ascii=False)}\n\nChange it as asked: {request}\n"
        "Keep everything not asked about as it is. Answer with the whole reel."
    )
    messages = [{"role": "system", "content": GUIDE}, {"role": "user", "content": user}]
    changed, left, reply = _ask(
        llm, messages, plan.sources, plan.voice, length, known, temperature=0.5,
        schema=_schema_for(idea),
    )  # fmt: skip
    changed = badges(changed, idea, reply) if ranking(idea) else changed
    changed, removed = _vet_new(llm, changed, plan, idea, checks, known)
    changed = finish(changed, idea, length, reply).model_copy(update={"look": plan.look})
    return _with_pictures(changed, plan), [*removed, *left]


def redo_country(
    llm, plan: Plan, index: int, request: str = "", idea: str = "", checks=None, known=None
) -> Plan:
    """`plan` with the country scene `index` written again (the same country unless the
    request says otherwise); its things checked like a draft's."""
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
    new = one[1].model_copy(update={"hold": scene.hold})
    topic = idea or plan.title
    if topic:
        kind = kind_of(topic)
        allowed = _allowed(llm, topic, kind)
        kept, gone = _sorted_out(
            llm, topic, kind, new.visual.text, new.visual.items, allowed, checks, known
        )
        if len(kept) < MIN_KEPT:
            kept += refill(
                llm, topic, kind, new.visual.text, kept, gone, MIN_KEPT - len(kept), allowed,
                checks, known,
            )  # fmt: skip
        if checks is not None:
            checks.save()
        if not kept:
            from agentic_editor.animate.planner import PlannerError

            raise PlannerError(
                f"nothing sure to show for {new.visual.text}; the country is as it was"
            )
        opening = parse(new)[0]
        new = new.model_copy(
            update={
                "narration": sentence(opening, kept, ["" for _ in kept]),
                "visual": new.visual.model_copy(
                    update={"items": kept, "value": scene.visual.value, "emoji": scene.visual.emoji}
                ),
            }
        )
    scenes = list(plan.scenes)
    scenes[index] = new
    return _with_pictures(tidy(plan.model_copy(update={"scenes": scenes})), plan)
