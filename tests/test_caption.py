import json

import httpx
import pytest

from agentic_editor import caption

STRONGER_KANYE = (
    "Stronger is a song by Kanye West from Graduation. It samples Daft Punk.\n\n"
    "== Background ==\nWest recorded it in 2007.\n\n== Track listing ==\n1. Stronger"
)
HBFS = (
    "Harder, Better, Faster, Stronger is a song by Daft Punk, released in 2001 on Discovery."
    "\n\n== Composition ==\nIt samples Cola Bottle Baby by Edwin Birdsong, a 1979 funk song."
)


def wiki(request: httpx.Request) -> httpx.Response:
    """Wikipedia and Wikidata, with two songs named Stronger."""
    params = dict(request.url.params)
    if request.url.host == "www.wikidata.org":
        ids = params["ids"].split("|")
        if params["props"] == "claims":
            performer = {"Q1": "Q10", "Q2": "Q20"}[ids[0]]
            claims = {
                "P175": [{"mainsnak": {"datavalue": {"value": {"id": performer}}}}],
                "P577": [{"mainsnak": {"datavalue": {"value": {"time": "+2001-03-12T00:00:00Z",
                                                               "precision": 11}}}}],
            }  # fmt: skip
            return httpx.Response(200, json={"entities": {ids[0]: {"claims": claims}}})
        names = {"Q10": "Kanye West", "Q20": "Daft Punk"}
        labels = {i: {"labels": {"en": {"value": names[i]}}} for i in ids}
        return httpx.Response(200, json={"entities": labels})
    if params.get("list") == "search":
        hits = [
            {"title": "Stronger (Kanye West song)"},
            {"title": "Harder, Better, Faster, Stronger"},
        ]
        return httpx.Response(200, json={"query": {"search": hits}})
    title = params["titles"]
    text, qid = (STRONGER_KANYE, "Q1") if "Kanye" in title else (HBFS, "Q2")
    page = {"title": title, "fullurl": f"https://en.wikipedia.org/wiki/{title}", "extract": text,
            "pageprops": {"wikibase_item": qid}}  # fmt: skip
    return httpx.Response(200, json={"query": {"pages": [page]}})


def test_the_article_kept_is_the_one_whose_performer_is_the_artist(tmp_path):
    sources = caption.gather("Stronger", "Daft Punk", "en", tmp_path, httpx.MockTransport(wiki))
    assert [a.title for a in sources.articles] == ["Harder, Better, Faster, Stronger"]
    assert sources.facts == ["performer: Daft Punk", "publication date: 2001-03-12"]
    # Kept on disk: a second ask doesn't go to Wikipedia.
    again = caption.gather("Stronger", "Daft Punk", "en", tmp_path, httpx.MockTransport(None))
    assert again.facts == sources.facts


def test_no_article_on_the_song_writes_no_caption(tmp_path):
    def nothing(request):
        return httpx.Response(200, json={"query": {"search": []}})

    with pytest.raises(caption.CaptionError, match="no article"):
        caption.gather("Unknown Song", "Nobody", "es", tmp_path, httpx.MockTransport(nothing))


def test_only_the_sections_about_the_song_s_making_are_kept():
    kept = caption.relevant(STRONGER_KANYE)
    assert "Background:\nWest recorded it in 2007." in kept and "Track listing" not in kept


def test_names_and_numbers_the_sources_never_mention_are_found_in_code():
    corpus = "Shape of You by Ed Sheeran was released on 6 January 2017 in 34 countries."
    text = (
        "Lanzada en 2017, “Shape of You” de Ed Sheeran llegó a 34 países. 🎶 "
        "La grabó con Rihanna en 2015. Un éxito enorme."
    )
    assert caption.unsupported_terms(text, corpus) == ["Rihanna", "2015"]


def test_a_quote_counts_only_if_the_sources_hold_it():
    corpus = "Jones disliked the demo and wanted to cut Jackson's 29-second introduction."
    assert caption.quoted("Jones disliked the demo and wanted to cut the introduction", corpus)
    assert not caption.quoted("Jones loved the demo from the very first listen", corpus)


class Writer:
    """A planner that writes the captions given in turn, and checks every sentence by
    quoting `evidence` for it."""

    def __init__(self, captions, evidence):
        self.captions, self.evidence, self.asked = list(captions), evidence, []

    def chat_json(self, messages, schema, **kwargs):
        self.asked.append(messages)
        if "paragraphs" in schema["properties"]:
            return {"paragraphs": self.captions.pop(0)}
        count = messages[0]["content"].count("\n", messages[0]["content"].index("CAPTION"))
        return {"sentences": [{"number": n, "kind": "fact", "evidence": self.evidence}
                              for n in range(1, count + 1)]}  # fmt: skip


SOURCES = caption.Sources(
    [caption.Article("Billie Jean", "https://en.wikipedia.org/wiki/Billie_Jean",
                     "Billie Jean is a song by Michael Jackson released in 1983 on Thriller.")],
    ["performer: Michael Jackson"],
)  # fmt: skip


def test_a_caption_that_fails_its_checks_is_written_again_with_what_was_wrong():
    invented = ["Billie Jean de Michael Jackson salió en 1983 🎵 con Prince 🎤."]
    fixed = ["Billie Jean de Michael Jackson salió en 1983 en Thriller 🎵 🎤."]
    llm = Writer([invented, fixed], "Billie Jean is a song by Michael Jackson released in 1983")
    written = caption.write(llm, "Billie Jean", "Michael Jackson", SOURCES, "es", words=(5, 40))
    assert written.text == fixed[0] and written.notes == []
    assert "not in the sources: Prince" in llm.asked[2][-1]["content"]
    assert written.sources == [{"title": "Billie Jean", "url": SOURCES.articles[0].url}]


def test_what_still_fails_after_one_more_try_is_left_for_a_person_to_check():
    llm = Writer([["Una canción de 1983 🎵 🎤."]] * 2, "the moon landing was filmed in a studio")
    written = caption.write(llm, "Billie Jean", "Michael Jackson", SOURCES, "es", words=(3, 40))
    assert written.notes == ["the sources don't say this: “Una canción de 1983 🎵 🎤.”"]


def test_the_song_is_named_from_the_video_or_no_caption_is_written():
    class Namer:
        def __init__(self, song):
            self.song = song

        def chat_json(self, messages, schema, **kwargs):
            return {"song": self.song, "artist": "Queen"}

    assert caption.identify(Namer("“Bohemian Rhapsody”"), "t", "", "") == (
        "Bohemian Rhapsody",
        "Queen",
    )
    with pytest.raises(caption.CaptionError):
        caption.identify(Namer(""), "IMG_4379", "", "")


def test_the_cache_file_is_plain_json(tmp_path):
    caption.gather("Stronger", "Daft Punk", "en", tmp_path, httpx.MockTransport(wiki))
    (saved,) = tmp_path.glob("*.json")
    assert (
        json.loads(saved.read_text())["articles"][0]["title"] == "Harder, Better, Faster, Stronger"
    )


def topics(request: httpx.Request) -> httpx.Response:
    """Wikipedia and Wikidata for a topic whose first search hit is a disambiguation page."""
    params = dict(request.url.params)
    if request.url.host == "www.wikidata.org":
        ids = params["ids"].split("|")
        if params["props"] == "claims":
            claims = {
                "P17": [{"mainsnak": {"datavalue": {"value": {"id": "Q878"}}}}],
                "P571": [{"mainsnak": {"datavalue": {"value": {"time": "+2004-01-06T00:00:00Z",
                                                               "precision": 11}}}}],
            }  # fmt: skip
            return httpx.Response(200, json={"entities": {ids[0]: {"claims": claims}}})
        labels = {i: {"labels": {"en": {"value": "United Arab Emirates"}}} for i in ids}
        return httpx.Response(200, json={"entities": labels})
    if params.get("list") == "search":
        hits = [{"title": "Burj (disambiguation)"}, {"title": "Burj Khalifa"}]
        return httpx.Response(200, json={"query": {"search": hits}})
    title = params["titles"]
    if "disambiguation" in title:
        page = {
            "title": title,
            "extract": "Burj may refer to:",
            "pageprops": {"disambiguation": ""},
        }
    else:
        text = (
            "The Burj Khalifa is a skyscraper in Dubai.\n\n== Construction ==\nWork began in 2004."
        )
        page = {"title": title, "fullurl": f"https://en.wikipedia.org/wiki/{title}",
                "extract": text, "pageprops": {"wikibase_item": "Q12495"}}  # fmt: skip
    return httpx.Response(200, json={"query": {"pages": [page]}})


def test_a_topic_is_written_from_its_article_not_a_disambiguation_page(tmp_path):
    sources = caption.gather_topic("Burj Khalifa", "en", tmp_path, httpx.MockTransport(topics))
    (article,) = sources.articles
    assert article.title == "Burj Khalifa" and "Construction:\nWork began in 2004." in article.text
    assert sources.facts == ["country: United Arab Emirates", "inception: 2004-01-06"]


def test_a_topic_wikipedia_doesnt_cover_writes_no_caption(tmp_path):
    def nothing(request):
        return httpx.Response(200, json={"query": {"search": []}})

    with pytest.raises(caption.CaptionError, match="no article on “Nowhere”"):
        caption.gather_topic("Nowhere", "it", tmp_path, httpx.MockTransport(nothing))


def test_a_topic_caption_is_asked_for_about_the_topic():
    fixed = ["Billie Jean de Michael Jackson salió en 1983 en Thriller 🎵 🎤."]
    llm = Writer([fixed], "Billie Jean is a song by Michael Jackson released in 1983")
    caption.write(llm, "", "", SOURCES, "es", words=(5, 40), topic="Thriller (album)")
    prompt = llm.asked[0][0]["content"]
    assert "about Thriller (album), in Spanish" in prompt and "the song" not in prompt
