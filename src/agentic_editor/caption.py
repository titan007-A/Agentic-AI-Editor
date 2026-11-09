"""The post's caption, written by the local planning model from what Wikipedia and Wikidata
say about the song (or another topic a person names), and checked before anyone sees it.

Accuracy comes from the sources, not the model's memory: the model is told to use only the
articles and facts it is given, every name and number in what it writes must appear in them
(checked in code, not by a model), and the model is then asked which sentences the sources
don't back. A caption that still fails is kept with the reasons, for a person to look at.

A song Wikipedia has no article on is still written about, from what else there is: the
artist's article and what a web search says about the song (see `elsewhere`), and when even
those are missing the job writes about what the video shows. The checks stay the same, so
a thinner source makes a plainer caption, never an invented one.
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
# For a topic that isn't a song: a person, a place, an event, a film, a thing.
TOPIC_PROPERTIES = {
    "P31": "instance of",
    "P17": "country",
    "P131": "located in",
    "P27": "country of citizenship",
    "P106": "occupation",
    "P569": "date of birth",
    "P19": "place of birth",
    "P570": "date of death",
    "P571": "inception",
    "P580": "start time",
    "P582": "end time",
    "P585": "point in time",
}
DATES = {"P569", "P570", "P571", "P577", "P580", "P582", "P585"}
STYLE = (
    "three paragraphs, 150 to 220 words in all. The first sets up the song, the artist and "
    "the year with a striking fact; the second goes into how it was written or recorded, a "
    "twist, a dispute or a technical detail; the third is about its legacy today and ends "
    "on a reflective note. Two to four fitting emojis spread through the text, not bunched "
    "at the end. Natural, storytelling prose. No hashtags and no call to follow or like."
)
TOPIC_STYLE = (
    "three paragraphs, 150 to 220 words in all. The first sets up the subject with a "
    "striking fact; the second goes into a lesser-known detail, a twist or how it came "
    "about; the third is about why it still matters and ends on a reflective note. Two to "
    "four fitting emojis spread through the text, not bunched at the end. Natural, "
    "storytelling prose. No hashtags and no call to follow or like."
)


VIDEO_STYLE = (
    "three paragraphs, 150 to 220 words in all. The first says what happens in the video "
    "with its most striking detail; the second gives the background: who or what it is, "
    "where and when, how it came about; the third says why it matters and ends on a "
    "reflective note. Two to four fitting emojis spread through the text, not bunched at "
    "the end. Natural, storytelling prose. No hashtags and no call to follow or like."
)


def video_style(wished: str) -> str:
    """How a video's caption is written: the default shape, with what the page asks for
    (in a preset that says what it should be about, not how to write it) on top."""
    if not wished.strip():
        return VIDEO_STYLE
    return f"{VIDEO_STYLE} What the page wants from it: {wished.strip()}"


SEEN = "What the video shows"
SAID = "What is said in the video"
POSTED = "The post's own description"


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


def performed_in(llm, title: str, description: str) -> tuple[str, str] | None:
    """The song sung in the video and who sings it there, as its post's caption says (a
    live cover is the person singing, not the song's own artist); None when it doesn't."""
    prompt = (
        "Which song is sung in this short video, and who sings it in the video? Answer from "
        "the video's own text only. For a cover or a talent-show performance the singer is "
        "the person performing it in the video, not the song's original artist. Leave both "
        "empty if the text doesn't name the song and who sings it.\n\n"
        f"Title: {title}\nDescription: {description[:1500]}"
    )
    reply = parse(
        _Song,
        llm.chat_json(
            [{"role": "user", "content": prompt}], _Song.model_json_schema(), temperature=0.0
        ),
    )
    song, singer = reply.song.strip(" \"'“”‘’"), reply.artist.strip()
    return (song, singer) if song and singer else None


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


# A title's bracket saying the article is about something else of the same name.
OTHER_WORK = re.compile(
    r"\((?:[^)]*\s)?(?:album|álbum|EP|film|película|pel[íi]cula|serie|series|TV|novel|novela|"
    r"telenovela|band|banda|grupo|singer|cantante|rapper|discography|discografía)\b[^)]*\)",
    re.I,
)


def articles(http: httpx.Client, language: str, song: str, artist: str):
    """Wikipedia articles in `language` that may be the song's, with their Wikidata ids:
    the title names the song (an exact match first) and the text names the artist."""
    wanted_song, wanted_artist = plain(song), plain(artist)
    titles: list[str] = []
    # The plain search ranks the artist's own pages first ("Givenchy duki" doesn't reach
    # "Givenchy (Duki song)" in five); one for the song's name in the title does.
    for query in (f"{song} {artist}".strip(), f'intitle:"{song.replace(chr(34), "")}" {artist}'):
        found = _wiki(http, language, action="query", list="search", srlimit=5, srsearch=query)
        titles += [h["title"] for h in found.get("query", {}).get("search", [])]
    titles = [t for t in dict.fromkeys(titles) if not OTHER_WORK.search(t)]

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


# Words a sentence uses for a song that only borrows from the artist.
BORROWS = re.compile(r"sampl|interpolat|mention|referenc|inspired|parod|answer|tribute", re.I)
# An article's first sentence saying it is about a song, in the languages captions use.
A_SONG = re.compile(
    r"song|single|track|canci[oó]n|sencillo|tema|chanson|canzone|can[cç][aã]o|Lied", re.I
)


def performed_by(facts_found: list[str], artist: str, found: Article) -> bool:
    """Whether the song is the artist's: Wikidata names them as its performer (or, when it
    names no performer, as a writer), or the article's lead says it is theirs. Wikidata's
    lists are often short ("Bad Romance" names only RedOne) and go by legal names ("Tupac
    Shakur" for 2Pac), so a list without them isn't the last word."""
    wanted = plain(artist)
    if not wanted:
        return True
    for kinds in (BY[:1], BY[1:]):
        named = [f.split(": ", 1)[1] for f in facts_found if f.startswith(kinds)]
        if named:
            names = [plain(n) for line in named for n in line.split(", ")]
            if any(wanted in n or n in wanted for n in names):
                return True
            break
    return named_in_lead(found.text, artist)


def named_in_lead(text: str, artist: str) -> bool:
    """Whether the article's lead says the song is the artist's: its first sentence names
    them ("a song by American singer Lady Gaga"), or a later lead sentence names them in
    full without saying the song only borrows from them: "It is best known as Natalie
    Imbruglia's debut single" counts, "It samples Daft Punk" doesn't, and neither does an
    album's article that lists the songs it covers."""
    wanted = plain(artist)
    lead = SENTENCE_END.split(text.split("\n\n")[0].strip())
    if not lead or not lead[0]:
        return False
    if any(part in plain(lead[0]) for part in [wanted, *wanted.split()[-1:]]):
        return True
    # A later sentence counts only in an article that opens by calling itself a song's:
    # an album's article names many singers it isn't by.
    if not A_SONG.search(lead[0]):
        return False
    return any(wanted in plain(s) and not BORROWS.search(s) for s in lead[1:])


def _date(value: dict) -> str:
    time = value["time"].lstrip("+")
    precision = value.get("precision", 11)
    return time[:4] if precision <= 9 else time[:7] if precision == 10 else time[:10]


def facts(
    http: httpx.Client, item: str, language: str, properties: dict[str, str] = PROPERTIES
) -> list[str]:
    """What Wikidata states about the item: for a song its performer, writers, producer,
    album and date."""

    def entities(ids: list[str], props: str) -> dict:
        return _entities(http, ids, props, language)

    claims = entities([item], "claims").get(item, {}).get("claims", {})
    values: dict[str, list] = {}
    for prop in properties:
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
    for prop, name in properties.items():
        shown = [
            _date(v) if prop in DATES else labels.get(v.get("id", ""), "")
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


def topic_article(http: httpx.Client, language: str, topic: str):
    """The Wikipedia article in `language` the topic names, with its Wikidata id: the
    search hit whose title is closest to it, never a disambiguation page."""
    found = _wiki(http, language, action="query", list="search", srlimit=5, srsearch=topic)
    wanted = plain(topic)
    titles = [hit["title"] for hit in found.get("query", {}).get("search", [])]

    def closeness(title: str) -> int:
        name = plain(title)
        if name == wanted or name.startswith(wanted + " ("):
            return 0
        return 1 if all(word in name for word in wanted.split()) else 2

    for title in sorted(titles, key=closeness):
        pages = _wiki(http, language, action="query", prop="extracts|pageprops|info",
                      explaintext=1, inprop="url", ppprop="wikibase_item|disambiguation",
                      redirects=1, titles=title)["query"]["pages"]  # fmt: skip
        page = pages[0] if pages else {}
        props = page.get("pageprops", {})
        text = page.get("extract") or ""
        if "disambiguation" in props or not text:
            continue
        # The lead and the sections in order: for a topic, every section may matter.
        flat = re.sub(r"\n(==+) ([^=\n]+) \1\n", r"\n\n\2:\n", text).strip()[:ARTICLE_CHARS]
        found = Article(title=page["title"], url=page.get("fullurl", ""), text=flat)
        return found, props.get("wikibase_item", "")
    return None


def gather_topic(
    topic: str, language: str, cache: Path, transport=None, english_first: bool = False
) -> Sources:
    """The topic's article in the caption's language and in English, and its Wikidata
    facts, kept on disk like a song's. `english_first` for a topic named in English: the
    English article settles what it is, and only the other language's article about the
    same thing is kept (searched first, Spanish Wikipedia matched another name)."""
    order = ["en", language] if english_first else [language, "en"]
    key = re.sub(r"[^a-z0-9]+", "-", plain(f"topic {topic} {'-'.join(order)}")).strip("-")[:100]
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
        for lang in dict.fromkeys(order):
            hit = topic_article(http, lang, topic)
            if hit is None:
                continue
            found, qid = hit
            if item and qid != item:
                continue  # the other language's article is about something else
            if qid and not item:
                sources.facts = facts(http, qid, language, TOPIC_PROPERTIES)
            sources.articles.append(found)
            item = item or qid
    if not sources.articles:
        raise CaptionError(f"Wikipedia has no article on “{topic}”")
    cache.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"articles": [asdict(a) for a in sources.articles],
                                "facts": sources.facts}), encoding="utf-8")  # fmt: skip
    return sources


QUOTES = " \"'“”‘’«»"
FILE_NAME = re.compile(r"\.(?:mp3|mp4|m4a|wav|aac|ogg|mov)$", re.I)
# Between the artists of a song made together: "Mike Towers y Jay Wheeler", "A x B".
TOGETHER = re.compile(
    r"\s+(?:x|&|y|e|and|junto a|con|with|feat\.?|ft\.?|featuring|vs\.?)\s+|\s*[,/+]\s*",
    re.I,
)
EDGES = " \t-_,;:|/\\"
HANDLE = re.compile(r"^\w+(?:[._]+\w+)+$")  # an account name: nicki.nicole, cris_emejota


def names_to_try(song: str, artist: str) -> list[tuple[str, str]]:
    """The song and artist as named, then tidier: the song without a file ending or a
    note about the take ("(Live)", "- Remastered"), an account name's dots as spaces, and
    the first of several artists. A model reading a post copies names as they are written
    there ("Tour_En_Vivo.mp3", "nicki.nicole")."""
    from agentic_editor.shazam import FEATURING, _tidy, _without_notes

    song, artist = song.strip(QUOTES), artist.strip(QUOTES + "@")
    songs = [song, _tidy(_without_notes(FILE_NAME.sub("", song).replace("_", " ")))]
    tidy = re.sub(r"[._]+", " ", artist) if HANDLE.match(artist) else artist
    tidy = _tidy(FEATURING.sub("", tidy))
    artists = [artist, tidy, TOGETHER.split(tidy)[0].strip(EDGES)]
    return list(dict.fromkeys((s, a) for s in songs for a in artists if s))


def find_song(
    song: str, artist: str, language: str, cache: Path, transport=None
) -> tuple[Sources, tuple[str, str]]:
    """The song's sources (see gather) under the first of its names (see names_to_try)
    Wikipedia has an article for, with that name."""
    first: CaptionError | None = None
    for name in names_to_try(song, artist):
        try:
            return gather(*name, language, cache, transport), name
        except CaptionError as exc:
            first = first or exc
    raise first or CaptionError(f"Wikipedia has no article on “{song}”")


# What a musician's article says in its first lines, in the languages captions are written in.
MUSICIAN = re.compile(
    r"sing|rapper|band|musician|songwriter|composer|group|duo|DJ|producer|vocalist|cantante|"
    r"cantautor|grupo|banda|músic|rapero|chanteu|chanteuse|groupe|musicien|cantor|compositor|"
    r"Sänger|Musiker|cantant|gruppo|musicist|orchestra|choir|coro",
    re.I,
)
WEB_RESULTS = 8


def artist_article(http: httpx.Client, language: str, artist: str, song: str):
    """The artist's Wikipedia article in `language`: a search hit whose title is their
    name and whose first lines say they make music (a rapper called Juvenile, not the
    article on juveniles). Kept: the lead, and the paragraphs that name the song."""
    wanted = plain(artist)
    if not wanted:
        return None
    found = _wiki(http, language, action="query", list="search", srlimit=5, srsearch=artist)
    titles = [hit["title"] for hit in found.get("query", {}).get("search", [])]
    for title in titles:
        name = plain(title)
        if not (name == wanted or name.startswith(wanted + " (")):
            continue
        pages = _wiki(http, language, action="query", prop="extracts|pageprops|info",
                      explaintext=1, inprop="url", ppprop="wikibase_item|disambiguation",
                      redirects=1, titles=title)["query"]["pages"]  # fmt: skip
        page = pages[0] if pages else {}
        text = page.get("extract") or ""
        if "disambiguation" in page.get("pageprops", {}) or not MUSICIAN.search(text[:400]):
            continue
        parts = re.split(r"\n(?:==+ [^=\n]+ ==+)\n|\n", text)
        lead = parts[0].strip()
        about = [p.strip() for p in parts[1:] if plain(song) and plain(song) in plain(p)]
        kept = "\n\n".join([lead, *about])[:ARTICLE_CHARS]
        return Article(title=page["title"], url=page.get("fullurl", ""), text=kept)
    return None


def web_results(song: str, artist: str, find) -> list[Article]:
    """What a web search says about the song: the results that name it, each its page's
    title, address and the search engine's lines from it."""
    try:
        hits = find(f'"{song}" {artist} song', WEB_RESULTS)
    except Exception as exc:  # the engines are a third party's: no answer is no results
        log.warning("web search for %s failed: %s", song, exc)
        return []
    wanted = plain(song)
    kept = []
    for hit in hits:
        body, title = " ".join(str(hit.get("body", "")).split()), str(hit.get("title", ""))
        if len(body) < 60 or wanted not in plain(f"{title} {body}"):
            continue
        kept.append(Article(title=title or hit.get("href", ""), url=hit.get("href", ""), text=body))
    return kept


def _search(query: str, count: int) -> list[dict]:
    from agentic_editor.research import _engines

    return _engines(query, count)


def elsewhere(
    song: str, artist: str, language: str, cache: Path, transport=None, find=_search
) -> Sources:
    """For a song Wikipedia has no article on: the artist's article (in the caption's
    language and in English) and what a web search says about the song, kept on disk like
    a song's. Raises CaptionError when there is neither."""
    key = re.sub(r"[^a-z0-9]+", "-", plain(f"elsewhere {song} {artist} {language}"))
    path = cache / f"{key.strip('-')[:100]}.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        return Sources([Article(**a) for a in data["articles"]], data["facts"])
    sources = Sources()
    client = httpx.Client(
        timeout=30, headers={"User-Agent": USER_AGENT}, follow_redirects=True, transport=transport
    )
    with client as http:
        for name in dict.fromkeys(a for _, a in names_to_try(song, artist)):
            for lang in dict.fromkeys([language, "en"]):
                try:
                    found = artist_article(http, lang, name, song)
                except httpx.HTTPError as exc:
                    log.warning("Wikipedia couldn't be asked about %s: %s", name, exc)
                    found = None
                if found:
                    sources.articles.append(found)
            if sources.articles:
                break
    sources.articles += web_results(song, artist, find)
    if not sources.articles:
        raise CaptionError(f"nothing found on “{song}” by {artist or 'its artist'}")
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
        "40 words, in the language they are written in, never translated, even when the "
        "caption is in another language; write HEADLINE if it only describes the reel as the "
        "headline does, and leave it empty if the sources don't state it."
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
    topic: str = "",
) -> Written:
    """The caption, checked, and asked again once with what was wrong. About `topic` when
    one is given, else about the song."""
    named = LANGUAGES.get(language, language)
    style = style or (TOPIC_STYLE if topic else STYLE)
    subject = topic or f"the song “{song}” by {artist}"
    prompt = (
        f"Write the caption of an Instagram reel about {subject}, in {named}. The reel's "
        f"headline: {headline or '(none)'}.\n\n{_sources_block(sources)}\n\n"
        f"Write it as {style} Aim for {words[0]}-{words[1]} words. Use only what "
        "the facts and articles above state: every name, date, number, place and event must "
        "come from them. Leave out anything they don't say, even if you believe it is true; "
        "a plainer caption is better than an invented detail."
    )
    if not topic:
        prompt += (
            " If the headline says the reel is another version of the song or a scene from a "
            "film, say so, and don't present it as the original recording."
        )
    messages = [{"role": "user", "content": prompt}]
    text = _write(llm, messages)
    emojis = "emoji" in style.casefold()
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


class _Seen(BaseModel):
    description: str


ARTICLES = 3  # Wikipedia articles read for a video's caption, at most


class _Subject(BaseModel):
    topic: str  # what the reel is about, in a few words
    facts: list[str] = Field(max_length=6)  # what the video itself shows and says
    articles: list[str] = Field(max_length=ARTICLES)  # Wikipedia titles to read; maybe none


WATCH_PROMPT = (
    "These frames come from one short video, in order. Say plainly what it shows: what "
    "happens, the people (by what they do or wear, never by a guessed name), animals, "
    "vehicles, machines or objects that matter (their kind or model when it is clear), "
    "the place, and any writing you can read on screen. Name nothing you can't see; say "
    '"unclear" rather than guess. Four to eight sentences.'
)


def watch(vision, frames: list[Path]) -> str:
    """What the vision model sees in the video's frames, in plain sentences."""
    reply = vision.chat_json(
        [{"role": "user", "content": WATCH_PROMPT}],
        _Seen.model_json_schema(),
        images=frames,
        temperature=0.0,
    )
    return parse(_Seen, reply).description.strip()


def video_subject(
    llm, seen: str, said: str, posted: str, one_liner: str, guide: str
) -> tuple[str, list[str], list[str]]:
    """What the reel is about (a few words), the facts the video itself gives, and the
    Wikipedia articles to read about them (none when nothing in it is something an
    encyclopedia covers)."""
    reply = llm.chat_json(
        [
            {
                "role": "user",
                "content": (
                    "A short video is posted with a caption that tells its story. From what "
                    "is known about it: say what it is about in a few words (the event, "
                    "thing or person at its heart); list the facts it gives (what happens, "
                    "who or what, where, when), each only if it is seen, said or posted "
                    f"below; and give the titles of up to {ARTICLES} English Wikipedia "
                    "articles to read for those facts: the specific event if it has one, "
                    "then the machine, place, person, unit or phenomenon it shows. No "
                    "articles when nothing there is that specific.\n\n"
                    f"{SEEN}: {seen or '(nothing)'}\n{SAID}: {said or '(nothing)'}\n"
                    f"{POSTED}: {posted or '(none)'}\nThe line shown on it: "
                    f"{one_liner or '(none)'}\nWhat the page wants the caption to be about: "
                    f"{guide or '(nothing said)'}"
                ),
            }
        ],
        _Subject.model_json_schema(),
        temperature=0.0,
    )
    found = parse(_Subject, reply)
    titles = [a.strip() for a in found.articles if a.strip()][:ARTICLES]
    return found.topic.strip(), [f.strip() for f in found.facts if f.strip()], titles


def video_sources(
    seen: str,
    said: str,
    posted: str,
    url: str,
    articles: list[str],
    language: str,
    cache: Path,
    transport=None,
) -> Sources:
    """The video itself (what is seen and said), the post's description, and Wikipedia
    on each of `articles` it has, in the caption's language and in English: everything a
    caption may state."""
    sources = Sources()
    for title in articles:
        try:
            found = gather_topic(title, language, cache, transport, english_first=True)
        except (CaptionError, httpx.HTTPError):
            continue  # the video, its post and the other articles are enough to go on
        known = {a.url for a in sources.articles}
        sources.articles += [a for a in found.articles if a.url not in known]
        sources.facts += [f for f in found.facts if f not in sources.facts]
    own = [Article(SEEN, "", seen), Article(SAID, "", said), Article(POSTED, url, posted)]
    sources.articles = [a for a in own if a.text.strip()] + sources.articles
    if not sources.articles:
        raise CaptionError("nothing is known about the video to write from")
    return sources
