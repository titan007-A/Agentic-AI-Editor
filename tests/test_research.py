from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest

from agentic_editor import research
from agentic_editor.llm import LLMError
from agentic_editor.research import Reel

NOW = datetime(2026, 10, 30, tzinfo=UTC)


class Planner:
    """The planning model: searches for the description, and a rating per reel from
    whether its caption says "Bryan Adams"."""

    def __init__(self, broken=False):
        self.asked = []
        self.broken = broken

    def chat_json(self, messages, schema, **kwargs):
        prompt = messages[-1]["content"]
        self.asked.append(prompt)
        if self.broken:
            raise LLMError("no answer")
        if "queries" in schema["properties"]:
            return {
                "brief": "Bryan Adams singing live",
                "queries": ["Bryan Adams live", "#summerof69", "Bryan Adams live", "  "],
            }
        reels = [line for line in prompt.split("\n") if line[:1].isdigit()]
        return {
            "ratings": [
                {"n": n, "relevance": 9 if "Bryan Adams" in line else 1, "why": "fits"}
                for n, line in enumerate(reels, 1)
            ]
        }


def hit(code, title="", body=""):
    return {"href": f"https://www.instagram.com/reel/{code}/?igsh=x", "title": title, "body": body}


@pytest.mark.parametrize(
    ("link", "code"),
    [
        ("https://www.instagram.com/reel/DcafVd6BocI/", "DcafVd6BocI"),
        ("https://instagram.com/reels/DcafVd6BocI?utm_source=ig", "DcafVd6BocI"),
        ("https://www.instagram.com/p/CAjcakIDpkV/?stkn=MWhp", "CAjcakIDpkV"),
        ("https://www.instagram.com/thevideoattic/reel/DcafVd6BocI/", "DcafVd6BocI"),
        ("https://www.instagram.com/thevideoattic/", None),
        ("https://www.youtube.com/watch?v=abc", None),
    ],
)
def test_a_reel_is_known_by_its_code_however_it_is_linked(link, code):
    assert research.code_of(link) == code


def test_the_request_is_understood_and_searched_with_the_model_s_searches():
    llm = Planner()
    brief, queries = research.plan(llm, "muje Bryan Adams ke live gaane dundh de")
    assert brief == "Bryan Adams singing live"
    # The request's own words aren't searched ("gaane" finds Hindi songs), and too few
    # searches are asked for once more.
    assert queries == ["Bryan Adams live", "summerof69"]
    assert len(llm.asked) == 2 and "muje Bryan Adams" in llm.asked[0]
    # A model that can't answer still leaves the request to search.
    assert research.plan(Planner(broken=True), "Bryan Adams live") == ("", ["Bryan Adams live"])


def test_searches_find_each_reel_once_and_go_on_past_a_failing_engine():
    asked = []

    def find(query, count):
        asked.append(query)
        if "broken" in query:
            raise TimeoutError("no engine answered")
        return [hit("AAAAA1", "first"), hit("BBBBB2"), {"href": "https://example.com/x"}]

    reels, failed = research.search(["live", "broken", "again"], find, pause=0)
    assert [r.code for r in reels] == ["AAAAA1", "BBBBB2"]
    assert reels[0].url == "https://www.instagram.com/reel/AAAAA1/"
    assert reels[0].title == "first" and reels[0].query == "live"
    assert failed == ["broken"]
    assert asked[0] == "site:instagram.com/reel live"


def test_a_reel_instagram_wont_show_keeps_what_the_search_said():
    shown = Reel("AAAAA1", research.reel_url("AAAAA1"), "q", snippet="from the engine")
    hidden = Reel("BBBBB2", research.reel_url("BBBBB2"), "q", snippet="from the engine")

    def info(url):
        if "BBBBB2" in url:
            raise RuntimeError("login required")
        return {
            "uploader": "The Video Attic",
            "description": "Bryan Adams live",
            "like_count": 2100,
            "comment_count": 40,
            "timestamp": 1761782400,
            "thumbnail": "https://cdn/x.jpg",
        }

    research.look_up([shown, hidden], info, workers=2)
    assert shown.looked_up and shown.account == "The Video Attic" and shown.likes == 2100
    assert shown.comments == 40 and shown.posted.year == 2025
    assert not hidden.looked_up and hidden.likes is None and hidden.snippet == "from the engine"


def test_virality_counts_reactions_and_how_fast_they_came():
    old = Reel("A", "", "", likes=10_000, posted=NOW - timedelta(days=1000))
    new = Reel("B", "", "", likes=10_000, posted=NOW - timedelta(days=2))
    small = Reel("C", "", "", likes=50, comments=5, posted=NOW - timedelta(days=2))
    unknown = Reel("D", "", "")
    research.spread([old, new, small, unknown], NOW)
    assert new.virality == 1.0
    assert new.virality > old.virality > small.virality > unknown.virality == 0.0


def test_a_viral_reel_that_doesnt_fit_stays_below_one_that_does():
    fits = Reel("A", "", "", relevance=0.9, virality=0.1)
    viral = Reel("B", "", "", relevance=0.2, virality=1.0)
    both = Reel("C", "", "", relevance=0.9, virality=0.9)
    unrated = Reel("D", "", "", relevance=None, virality=0.5)
    ranked = research.rank([viral, unrated, fits, both])
    assert [r.code for r in ranked] == ["C", "A", "D", "B"]


def test_a_research_finds_rates_and_ranks_leaving_out_what_was_edited():
    llm = Planner()
    opened = []

    def planner():
        opened.append(1)
        return nullcontext(llm)

    def find(query, count):
        return [hit("USED01"), hit("SEEN01"), hit("ADAMS1"), hit("OTHER1")]

    def info(url):
        about = {
            "ADAMS1": ("Bryan Adams - Summer of 69 live", 900),
            "SEEN01": ("Bryan Adams Heaven, Rock in Rio", 300),
            "OTHER1": ("cat videos compilation", 90_000),
        }
        caption, likes = about[research.code_of(url)]
        return {"uploader": "x", "description": caption, "like_count": likes}

    steps = []
    result = research.run(
        "Bryan Adams live",
        planner,
        used={"USED01"},
        seen={"SEEN01"},
        progress=lambda message, value: steps.append(value),
        find=find,
        info=info,
        pause=0,
    )
    # Edited, shown before and unrelated (a viral cat video) are all left out.
    assert [r.code for r in result.reels] == ["ADAMS1"]
    assert (result.used, result.repeats, result.unrelated) == (1, 1, 1)
    assert result.queries[0] == "Bryan Adams live" and result.brief == "Bryan Adams singing live"
    assert result.reels[0].relevance == 0.9
    assert len(opened) == 2  # the model holds the GPU only to plan and to rate
    assert steps == sorted(steps)


def test_a_research_the_engines_turned_away_says_so():
    def find(query, count):
        raise TimeoutError

    with pytest.raises(RuntimeError, match="didn't answer"):
        research.run(
            "x", lambda: nullcontext(Planner()), set(), set(), lambda *a: None, find, pause=0
        )


def test_an_empty_search_is_no_reels_but_a_failing_engine_is_a_failure(monkeypatch):
    import ddgs
    from ddgs.exceptions import DDGSException, RatelimitException

    answers = {"empty": DDGSException("No results found."), "busy": RatelimitException("202")}

    class Engines:
        def __init__(self, **kwargs):
            pass

        def text(self, query, max_results):
            raise answers[query.split()[-1]]

    monkeypatch.setattr(ddgs, "DDGS", Engines)
    reels, failed = research.search(["empty", "busy"], pause=0)
    assert reels == [] and failed == ["busy"]


def test_one_account_fills_at_most_two_of_the_eight():
    ranked = [Reel(f"A{n}", "", "", account="VoicesHQ") for n in range(4)]
    ranked += [Reel(f"B{n}", "", "", account="") for n in range(10)]
    kept = research.best(ranked)
    assert [r.code for r in kept] == ["A0", "A1", "B0", "B1", "B2", "B3", "B4", "B5"]
