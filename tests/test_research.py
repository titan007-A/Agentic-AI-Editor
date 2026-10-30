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
                {
                    "n": n,
                    "relevance": 9 if "Bryan Adams" in line else 1,
                    "why": "fits",
                    **NAMELESS,
                }
                for n, line in enumerate(reels, 1)
            ]
        }


# The fields a rating always has, as the model leaves them when the caption doesn't say.
NAMELESS = {"song": "", "artist": "", "preset": ""}


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
    assert result.left_out == {"edited": 1, "shown before": 1, "didn't fit": 1}
    assert result.queries[0] == "Bryan Adams live" and result.brief == "Bryan Adams singing live"
    assert result.reels[0].relevance == 0.9 and result.considered == 2
    # The rating is asked with the brief, and the request as they wrote it.
    assert "Bryan Adams singing live (their words: Bryan Adams live)" in llm.asked[-1]
    assert len(opened) == 2  # the model holds the GPU only to plan and to rate
    assert steps == sorted(steps)


def test_one_account_fills_at_most_two_of_the_eight_but_a_song_may_come_again():
    def reel(code, handle, song, score, account=""):
        return Reel(code, "", "", handle=handle, account=account, song=song, score=score)

    ranked = [
        reel("A1", "attic", "Heaven - Bryan Adams", 0.9, "The Video Attic"),
        reel("A2", "attic", "Summer of 69 - Bryan Adams", 0.85, "Attic Two"),
        reel("A3", "attic", "Run to You - Bryan Adams", 0.8),  # a third from @attic
        reel("B1", "rockblog", "heaven – bryan adams", 0.75),  # another reel of Heaven
        reel("C1", "", "", 0.65, "No Handle"),  # known by its display name alone
        reel("C2", "", "", 0.6, "No Handle"),
        reel("C3", "", "", 0.55, "No Handle"),
        reel("D1", "", "", 0.5),  # nothing known of its account: no cap
        reel("D2", "", "", 0.45),
        reel("B2", "other", "Heaven - Bryan Adams", 0.42),  # a third reel of Heaven
        reel("E1", "vault", "Caruso - Lucio Dalla", 0.4),
        reel("E2", "vault", "Felicità - Al Bano", 0.3),
    ]
    picked, passed, cutoff = research.pick(ranked)
    assert [r.code for r in picked] == ["A1", "A2", "B1", "C1", "C2", "D1", "D2", "E1"]
    assert passed == {"same account": 2, "same song": 1} and cutoff == 0.3


def test_the_rated_song_and_artist_are_named_together():
    class Rater:
        def chat_json(self, messages, schema, **kwargs):
            first = {**NAMELESS, "n": 1, "relevance": 8, "song": "Heaven", "artist": "Bryan Adams"}
            return {"ratings": [first, {**NAMELESS, "n": 2, "relevance": 8, "artist": "Queen"}]}

    reels = [Reel("A", "", ""), Reel("B", "", "")]
    research.rate(Rater(), "80s rock", reels)
    assert [(r.song, r.artist) for r in reels] == [("Heaven - Bryan Adams", "Bryan Adams"),
                                                  ("", "Queen")]  # fmt: skip


def test_an_account_s_name_is_read_from_how_people_write_it():
    assert research.handle_of("@VoicesHQ") == "voiceshq"
    assert research.handle_of("https://www.instagram.com/the.vocal_vault/?hl=en") == (
        "the.vocal_vault"
    )
    assert research.handle_of("voiceshq ") == "voiceshq"
    assert research.handle_of("not an account!") is None


def test_presets_are_described_by_what_they_make():
    from agentic_editor.presets import load_presets
    from agentic_editor.settings import ROOT

    about = research.describe_presets(load_presets(ROOT / "presets"))
    assert "isolated vocals" in about["voicehq-9x16"]
    assert "Song: {song}" in about["voicehq-9x16"]
    assert "post caption in 'es'" in about["song-stories-es-9x16"]


class AccountPlanner(Planner):
    """Reads an account's reels into a summary, and rates with a song and a preset."""

    def chat_json(self, messages, schema, **kwargs):
        prompt = messages[-1]["content"]
        if "summary" in schema["properties"]:
            self.asked.append(prompt)
            return {"summary": "Isolated vocals of 80s rock singers, Bryan Adams most liked."}
        reply = super().chat_json(messages, schema, **kwargs)
        for rating, line in zip(reply.get("ratings", []), prompt.split("Reels:\n")[-1].split("\n"),
                                strict=False):  # fmt: skip
            rating.update(song=line.split("caption: ")[-1].split(" |")[0], matches="vocals",
                          preset="voicehq-9x16", preset_why="vocals only")  # fmt: skip
        return reply


def test_an_account_is_learned_from_its_own_reels():
    llm = AccountPlanner()

    def find(query, count):
        return [hit("OWN001"), hit("OWN002"), hit("OTHER1")]

    def info(url):
        code = research.code_of(url)
        channel = "voiceshq" if code.startswith("OWN") else "someone"
        likes = {"OWN001": 50, "OWN002": 900}.get(code, 10)
        return {"channel": channel, "description": f"vocals {code}", "like_count": likes}

    learned = research.learn("voiceshq", lambda: nullcontext(llm), lambda *a: None, find, info, 0)
    assert learned.own == ["OWN002", "OWN001"]  # best liked first
    assert learned.summary.startswith("Isolated vocals")
    assert learned.best[0] == {"code": "OWN002", "likes": 900, "caption": "vocals OWN002"}
    assert "900 likes: vocals OWN002" in llm.asked[-1]

    nobody = research.learn(
        "voiceshq", lambda: nullcontext(llm), lambda *a: None, find, lambda url: {}, 0
    )
    assert nobody == research.Learned("", [], [])


def test_a_research_for_an_account_finds_reels_like_its_own_but_not_its_own():
    llm = AccountPlanner()

    def find(query, count):
        return [hit("OWN001"), hit("OWN009"), hit("ADAMS1"), hit("ADAMS2")]

    def info(url):
        code = research.code_of(url)
        if code == "OWN009":  # one of its own the account's learning didn't find
            return {"channel": "voiceshq", "description": "Bryan Adams vocals"}
        return {"channel": "attic", "description": f"Bryan Adams {code}", "like_count": 100}

    context = research.Context(
        handle="voiceshq",
        summary="Isolated vocals of 80s rock singers.",
        presets={"voicehq-9x16": "Vocals only; isolated vocals", "podcast-9x16": "Podcast"},
        own={"OWN001"},
    )
    result = research.run(
        "", lambda: nullcontext(llm), set(), set(), lambda *a: None, find, info, 0, context
    )
    assert [r.code for r in result.reels] == ["ADAMS1", "ADAMS2"]
    assert result.left_out == {"the account's own": 2}
    plan_prompt, rate_prompt = llm.asked[0], llm.asked[-1]
    assert "@voiceshq" in plan_prompt and "voicehq-9x16: Vocals only" in plan_prompt
    assert "Isolated vocals of 80s rock singers" in rate_prompt
    first = result.reels[0]
    assert (first.song, first.preset, first.preset_why) == (
        "Bryan Adams ADAMS1",
        "voicehq-9x16",
        "vocals only",
    )
    assert result.queries[0] == "Bryan Adams live"  # no focus: the model's searches only


def test_a_model_that_cant_plan_an_account_s_searches_leaves_its_name_and_presets():
    context = research.Context(handle="voiceshq", presets={"v": "Vocals only; isolated"})
    assert research.plan(Planner(broken=True), "", context) == ("", ["voiceshq", "Vocals only"])


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


def test_every_reel_gets_a_preset_to_edit_it_with():
    context = research.Context(presets={"voicehq-9x16": "vocals", "vocal-music-it-9x16": "it"})
    reels = [Reel("A", "", "", preset="vocal-music-it-9x16"), Reel("B", "", "")]
    research.suggest(reels, context)
    assert [r.preset for r in reels] == ["vocal-music-it-9x16"] * 2
    assert "chose most" in reels[1].preset_why
    alone = [Reel("C", "", "")]
    research.suggest(alone, context)
    assert alone[0].preset == "voicehq-9x16" and "first preset" in alone[0].preset_why
