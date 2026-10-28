"""The post's caption, written by the local planning model from what Wikipedia and Wikidata
say about the song, and checked before anyone sees it.

Accuracy comes from the sources, not the model's memory: the model is told to use only the
articles and facts it is given, every name and number in what it writes must appear in them
(checked in code, not by a model), and the model is then asked which sentences the sources
don't back. A caption that still fails is kept with the reasons, for a person to look at;
a song Wikipedia has no article on gets no caption rather than an invented one.
"""

import json
import logging
import re
import unicodedata
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from agentic_editor.agent import LANGUAGES
from agentic_editor.llm import LLMError, parse

log = logging.getLogger(__name__)

USER_AGENT = "AgenticAIEditor/0.7 (https://github.com/titan007-A/Agentic-AI-Editor)"
# Tokens the writer holds: the articles, the facts, the instructions and its answer.
CONTEXT = 12288
ARTICLE_CHARS = 5000  # of each article, lead and the sections about the song's making
SECTIONS = re.compile(
    r"background|recording|writing|composition|production|release|reception|legacy|"
    r"history|origin|inspiration|music video|antecedentes|grabaci|composici|producci|"
    r"lanzamiento|recepci|legado|historia|origen|inspiraci|creaci|video musical",
    re.I,
)
# Wikidata properties worth stating as facts, and how to name them for the writer.
BY = ("performer", "composer", "lyricist")
PROPERTIES = {
    "P175": "performer",
    "P86": "composer",
    "P676": "lyricist",
    "P162": "producer",
    "P361": "part of (album)",
    "P264": "record label",
    "P136": "genre",
    "P495": "country of origin",
    "P577": "publication date",
}
STYLE = (
    "three paragraphs, 150 to 220 words in all. The first sets up the song, the artist and "
    "the year with a striking fact; the second goes into how it was written or recorded, a "
    "twist, a dispute or a technical detail; the third is about its legacy today and ends "
    "on a reflective note. Two to four fitting emojis spread through the text, not bunched "
    "at the end. Natural, storytelling prose. No hashtags and no call to follow or like."
)


class CaptionError(RuntimeError):
    """No caption can be written: the song isn't named, or no source covers it."""


@dataclass
class Article:
    title: str
    url: str
    text: str


@dataclass
class Sources:
    articles: list[Article] = field(default_factory=list)
    facts: list[str] = field(default_factory=list)

    def corpus(self) -> str:
        return "\n".join([*(a.title + "\n" + a.text for a in self.articles), *self.facts])


@dataclass
class Written:
    text: str
    sources: list[dict[str, str]]
    notes: list[str]  # what a person should check before posting it; empty when it passed


class _Song(BaseModel):
    song: str
    artist: str


class _Caption(BaseModel):
    paragraphs: list[str] = Field(min_length=1, max_length=5)


class _Verdict(BaseModel):
    number: int
    kind: Literal["fact", "opinion"]
    # For a fact: the sources' own words that state it, or HEADLINE.
    evidence: str = Field(max_length=400)


class _Check(BaseModel):
    sentences: list[_Verdict]


def plain(text: str) -> str:
    """Lowercase and without accents, for matching across languages and spellings."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def identify(llm, title: str, description: str, headline: str) -> tuple[str, str]:
    prompt = (
        "Which song is this short video about? Answer with the song's name and its artist as "
        "the video's own text gives them; leave both empty if it doesn't name a song.\n\n"
        f"Headline: {headline or '(none)'}\nTitle: {title}\nDescription: {description[:1500]}"
    )
    reply = parse(
        _Song,
        llm.chat_json(
            [{"role": "user", "content": prompt}], _Song.model_json_schema(), temperature=0.0
        ),
    )
    song, artist = reply.song.strip(" \"'“”"), reply.artist.strip()
    if not song:
        raise CaptionError("the video doesn't say which song it is")
    return song, artist


def _wiki(http: httpx.Client, language: str, **params) -> dict:
    response = http.get(
        f"https://{language}.wikipedia.org/w/api.php",
        params={"format": "json", "formatversion": 2, **params},
    )
    response.raise_for_status()
    return response.json()


def relevant(text: str) -> str:
    """The lead and the sections about how the song came about and what it became."""
    parts = re.split(r"\n(==+ [^=\n]+ ==+)\n", text)
    kept = [parts[0].strip()]
    for heading, body in zip(parts[1::2], parts[2::2], strict=False):
        if SECTIONS.search(heading):
            kept.append(heading.strip("= ") + ":\n" + body.strip())
    return "\n\n".join(k for k in kept if k)[:ARTICLE_CHARS]


def articles(http: httpx.Client, language: str, song: str, artist: str):
    """Wikipedia articles in `language` that may be the song's, with their Wikidata ids:
    the title names the song (an exact match first) and the text names the artist."""
    found = _wiki(http, language, action="query", list="search", srlimit=5,
                  srsearch=f"{song} {artist}".strip())  # fmt: skip
    wanted_song, wanted_artist = plain(song), plain(artist)
    titles = [hit["title"] for hit in found.get("query", {}).get("search", [])]

    def closeness(title: str) -> int:
        # "Stronger" or "Stronger (Daft Punk song)" before "Harder, Better, Faster, Stronger".
        name = plain(title)
        return 0 if name == wanted_song or name.startswith(wanted_song + " (") else 1

    for title in sorted(titles, key=closeness):
        if wanted_song not in plain(title):
            continue
        pages = _wiki(http, language, action="query", prop="extracts|pageprops|info",
                      explaintext=1, inprop="url", ppprop="wikibase_item", redirects=1,
                      titles=title)["query"]["pages"]  # fmt: skip
        page = pages[0] if pages else {}
        text = page.get("extract") or ""
        if wanted_artist and not any(
            part in plain(text[:3000]) for part in [wanted_artist, *wanted_artist.split()[-1:]]
        ):
            continue
        yield (
            Article(title=page["title"], url=page.get("fullurl", ""), text=relevant(text)),
            page.get("pageprops", {}).get("wikibase_item", ""),
        )


def _entities(http: httpx.Client, ids: list[str], props: str, language: str) -> dict:
    response = http.get(
        "https://www.wikidata.org/w/api.php",
        params={"action": "wbgetentities", "ids": "|".join(ids), "props": props,
                "languages": f"{language}|en", "format": "json"},
    )  # fmt: skip
    response.raise_for_status()
    return response.json().get("entities", {})


def performed_by(facts_found: list[str], artist: str, found: Article) -> bool:
    """Whether the song is the artist's: Wikidata names them as its performer (or, when it
    names no performer, as a writer); with neither, the article's first sentence, which
    says whose song it is, names them. Another song that merely samples or mentions them
    names them later."""
    wanted = plain(artist)
    if not wanted:
        return True
    for kinds in (BY[:1], BY[1:]):
        named = [f.split(": ", 1)[1] for f in facts_found if f.startswith(kinds)]
        if named:
            names = [plain(n) for line in named for n in line.split(", ")]
            return any(wanted in n or n in wanted for n in names)
    first = SENTENCE_END.split(found.text.strip(), maxsplit=1)[0]
    return any(part in plain(first) for part in [wanted, *wanted.split()[-1:]])


def _date(value: dict) -> str:
    time = value["time"].lstrip("+")
    precision = value.get("precision", 11)
    return time[:4] if precision <= 9 else time[:7] if precision == 10 else time[:10]


def facts(http: httpx.Client, item: str, language: str) -> list[str]:
    """What Wikidata states about the song: performer, writers, producer, album, date."""

    def entities(ids: list[str], props: str) -> dict:
        return _entities(http, ids, props, language)

    claims = entities([item], "claims").get(item, {}).get("claims", {})
    values: dict[str, list] = {}
    for prop in PROPERTIES:
        for claim in claims.get(prop, []):
            value = claim.get("mainsnak", {}).get("datavalue", {}).get("value")
            if value is not None:
                values.setdefault(prop, []).append(value)
    linked = sorted(
        {v["id"] for vs in values.values() for v in vs if isinstance(v, dict) and "id" in v}
    )
    labels = {}
    for start in range(0, len(linked), 50):
        for qid, entity in entities(linked[start : start + 50], "labels").items():
            names = entity.get("labels", {})
            label = names.get(language) or names.get("en")
            if label:
                labels[qid] = label["value"]
    lines = []
    for prop, name in PROPERTIES.items():
        shown = [
            _date(v) if prop == "P577" else labels.get(v.get("id", ""), "")
            for v in values.get(prop, [])
        ]
        shown = list(dict.fromkeys(s for s in shown if s))
        if shown:
            lines.append(f"{name}: {', '.join(shown)}")
    return lines


def gather(song: str, artist: str, language: str, cache: Path, transport=None) -> Sources:
    """The song's articles (in the caption's language and in English) and its Wikidata
    facts, kept on disk so writing it again doesn't ask again."""
    key = re.sub(r"[^a-z0-9]+", "-", plain(f"{song} {artist} {language}")).strip("-")[:100]
    path = cache / f"{key}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return Sources([Article(**a) for a in data["articles"]], data["facts"])
    sources = Sources()
    item = ""
    client = httpx.Client(
        timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True, transport=transport
    )
    with client as http:
        for lang in dict.fromkeys([language, "en"]):
            for found, qid in articles(http, lang, song, artist):
                if item and qid and qid != item:
                    continue  # the other language's article is about another song
                stated = facts(http, qid, language) if qid and not item else sources.facts
                if not performed_by(stated, artist, found):
                    log.info("%s is by someone else, not %s", found.title, artist)
                    continue
                sources.articles.append(found)
                item, sources.facts = item or qid, stated
                break
    if not sources.articles:
        raise CaptionError(f"Wikipedia has no article on “{song}” by {artist or 'its artist'}")
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"articles": [asdict(a) for a in sources.articles],
                                "facts": sources.facts}), encoding="utf-8")  # fmt: skip
    return sources


NUMBER = re.compile(r"\d[\d.,]*\d|\d")
NAME = re.compile(r"[^\W\d_](?:[\w-]|['’](?=\w))*", re.UNICODE)
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+")


def unsupported_terms(text: str, corpus: str) -> list[str]:
    """Names and numbers in the caption the sources never mention: a capitalised word that
    doesn't open a sentence, a quoted title's words, any number of 10 or more. Checked in
    code, so a model's slip can't wave it through."""
    words = set(NAME.findall(plain(corpus)))
    numbers = {int(re.sub(r"[.,]", "", n)) for n in NUMBER.findall(corpus)}
    missing: list[str] = []
    for sentence in SENTENCE_END.split(text):
        tokens = list(re.finditer(NAME, sentence))
        for i, token in enumerate(tokens):
            word = token.group()
            opens = i == 0 or sentence[: token.start()].rstrip()[-1:] in '¡¿:“"«'
            name = word[:1].isupper() and not opens and len(word) > 2
            if name and plain(word) not in words and word not in missing:
                missing.append(word)
        for number in NUMBER.findall(sentence):
            value = int(re.sub(r"[.,]", "", number))
            if value >= 10 and value not in numbers and number not in missing:
                missing.append(number)
    return missing


def sentences(text: str) -> list[str]:
    return [s for paragraph in text.split("\n\n") for s in SENTENCE_END.split(paragraph) if s]


def _sources_block(sources: Sources) -> str:
    parts = [f"[{i + 1}] {a.title} ({a.url})\n{a.text}" for i, a in enumerate(sources.articles)]
    facts_block = "\n".join(f"- {f}" for f in sources.facts) or "- (none)"
    return f"FACTS (Wikidata):\n{facts_block}\n\nARTICLES (Wikipedia):\n" + "\n\n".join(parts)


def _write(llm, messages: list[dict]) -> str:
    reply = parse(
        _Caption,
        llm.chat_json(messages, _Caption.model_json_schema(), temperature=0.3),
    )
    return "\n\n".join(" ".join(p.split()) for p in reply.paragraphs if p.strip())


def quoted(evidence: str, corpus: str) -> bool:
    """Whether `evidence` is really in the sources: most of its five-word runs are, as a
    model copying a passage often changes a word or two but never invents the passage."""
    words = NAME.findall(plain(evidence))
    if len(words) < 3:
        return False
    flat = " ".join(NAME.findall(plain(corpus)))
    runs = [" ".join(words[i : i + 5]) for i in range(max(1, len(words) - 4))]
    return sum(run in flat for run in runs) >= len(runs) / 2


def _check(llm, text: str, sources: Sources, headline: str) -> list[str]:
    """The sentences stating a fact the sources don't. The model has to copy the words that
    back each fact, and the copy is looked up in the sources in code: a fact it can't quote
    for is unsupported, whatever the model says about it."""
    lines = sentences(text)
    numbered = "\n".join(f"{i + 1}. {s}" for i, s in enumerate(lines))
    prompt = (
        f"{_sources_block(sources)}\n\nThe reel's headline: {headline or '(none)'}\n\n"
        f"CAPTION, sentence by sentence:\n{numbered}\n\n"
        "For each sentence, say whether it states a fact (a name, date, number, place, or "
        "what happened, how or why) or only an opinion or feeling. For a fact, copy the "
        "words of the sources above that state it, exactly as they are written there, up to "
        "40 words; write HEADLINE if it only describes the reel as the headline does, and "
        "leave it empty if the sources don't state it."
    )
    reply = parse(
        _Check,
        llm.chat_json(
            [{"role": "user", "content": prompt}], _Check.model_json_schema(), temperature=0.0
        ),
    )
    corpus = sources.corpus()
    judged = {v.number: v for v in reply.sentences}
    found = []
    for number, line in enumerate(lines, 1):
        verdict = judged.get(number)
        if verdict is None or verdict.kind == "opinion":
            continue
        if verdict.evidence.strip().upper() == "HEADLINE" or quoted(verdict.evidence, corpus):
            continue
        found.append(f"the sources don't say this: “{line[:100]}”")
    return found


def emoji(ch: str) -> bool:
    return ord(ch) >= 0x1F300 or 0x2600 <= ord(ch) <= 0x27BF


def problems(
    text: str,
    sources: Sources,
    llm,
    words: tuple[int, int],
    headline: str = "",
    style_wants_emoji: bool = True,
) -> list[str]:
    found = [f"not in the sources: {term}" for term in unsupported_terms(text, sources.corpus())]
    count = len(re.findall(r"\w+", text))
    if not words[0] <= count <= words[1]:
        found.append(f"{count} words, not {words[0]}-{words[1]}")
    if style_wants_emoji and sum(map(emoji, text)) < 2:
        found.append("add two to four fitting emojis through the text")
    try:
        found += _check(llm, text, sources, headline)
    except LLMError as exc:
        found.append(f"couldn't check it against the sources ({exc})")
    return found


def write(
    llm,
    song: str,
    artist: str,
    sources: Sources,
    language: str,
    headline: str = "",
    style: str = "",
    words: tuple[int, int] = (150, 220),
) -> Written:
    """The caption, checked, and asked again once with what was wrong."""
    named = LANGUAGES.get(language, language)
    prompt = (
        f"Write the caption of an Instagram reel about the song “{song}” by {artist}, in "
        f"{named}. The reel's headline: {headline or '(none)'}.\n\n{_sources_block(sources)}\n\n"
        f"Write it as {style or STYLE} Aim for {words[0]}-{words[1]} words. Use only what "
        "the facts and articles above state: every name, date, number, place and event must "
        "come from them. Leave out anything they don't say, even if you believe it is true; "
        "a plainer caption is better than an invented detail. If the headline says the reel "
        "is another version of the song or a scene from a film, say so, and don't present it "
        "as the original recording."
    )
    messages = [{"role": "user", "content": prompt}]
    text = _write(llm, messages)
    emojis = "emoji" in (style or STYLE).casefold()
    wrong = problems(text, sources, llm, words, headline, emojis)
    if wrong:
        messages += [
            {"role": "assistant", "content": json.dumps({"paragraphs": text.split("\n\n")})},
            {"role": "user", "content": "Write it again, fixing these problems; drop a detail "
             "rather than keep one the sources don't give, and keep it "
             f"{words[0]}-{words[1]} words by using other facts the sources do give:\n- "
             + "\n- ".join(wrong)},
        ]  # fmt: skip
        text = _write(llm, messages)
        wrong = problems(text, sources, llm, words, headline, emojis)
    used = [{"title": a.title, "url": a.url} for a in sources.articles]
    return Written(text=text, sources=used, notes=wrong)
