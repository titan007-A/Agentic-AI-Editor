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
        if "questions" in schema["properties"]:
            return {"questions": QUESTIONS}
        if "entries" in schema["properties"]:
            assert "Bryan Adams" in prompt  # the PDF's text
            return {"entries": PDF_ENTRIES}
        if "kind" in schema["properties"]:
            message = prompt.rsplit("Their message: ", 1)[-1]
            if message.lower().startswith("why"):
                return {"kind": "answer", "reply": "#1 fits best: fits.", "request": ""}
            return {
                "kind": "refine",
                "reply": "Looking for older Bryan Adams concerts.",
                "request": "Bryan Adams live concerts from the 1980s",
            }
        reels = [line for line in prompt.split("\n") if line[:1].isdigit()]
        return {
            "ratings": [
                {
                    **NAMELESS,
                    "n": n,
                    "relevance": 9 if "Bryan Adams" in line else 1,
                    "why": "fits",
                }
                for n, line in enumerate(reels, 1)
            ]
        }


# The songs the fake model reads from a PDF; the second has nothing to find.
PDF_ENTRIES = [
    {"singer": "Bryan Adams", "song": "Heaven", "top": "Il capolavoro\\ndel 1985",
     "bottom": "Seguici"},
    {"singer": "Nobody", "song": "Nothing", "top": "", "bottom": ""},
    {"singer": " ", "song": " ", "top": "a heading", "bottom": ""},  # no song: left out
]  # fmt: skip


def pdf(lines: list[str]) -> bytes:
    """A one-page PDF with these lines of text, as a word processor's export has them."""

    def escaped(line: str) -> str:
        return line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    text = " ".join(f"({escaped(line)}) Tj 0 -16 Td" for line in lines)
    content = f"BT /F1 12 Tf 72 720 Td {text} ET"
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        "/Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offsets = "%PDF-1.4\n", []
    for n, body in enumerate(objects, 1):
        offsets.append(len(out))
        out += f"{n} 0 obj\n{body}\nendobj\n"
    table = "".join(f"{offset:010d} 00000 n \n" for offset in offsets)
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n{table}"
    out += f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF"
    return out.encode("latin-1")


# The fields a rating always has, as the model leaves them when the caption doesn't say.
NAMELESS = {"why": "", "song": "", "artist": "", "matches": "", "preset": "", "preset_why": ""}
QUESTIONS = [
    {"question": "Live or studio?", "choices": ["Live", "Studio", " live ", ""]},
    {"question": "Which era?", "choices": ["80s", "90s"]},
    {"question": "  ", "choices": ["a", "b"]},  # no question: dropped
    # The presets decide these: dropped.
    {"question": "What language should the subtitles be in?", "choices": ["it", "en"]},
    {"question": "Isolated vocals or the full mix?", "choices": ["Vocals", "Full"]},
    {"question": "What aspect ratio?", "choices": ["9:16", "1:1"]},
    {"question": "Short punchy edits or longer clips?", "choices": ["Short", "Long"]},
]


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


def test_feedback_steers_planning_and_rating_and_hidden_accounts_stay_out():
    llm = AccountPlanner()

    def find(query, count):
        return [hit("ADAMS1"), hit("ADAMS2"), hit("ADAMS3")]

    def info(url):
        code = research.code_of(url)
        channel = "spampage" if code == "ADAMS3" else "attic"
        return {"channel": channel, "description": f"Bryan Adams {code}", "like_count": 100}

    context = research.Context(
        liked=[research.taste_line("Heaven - Bryan Adams", "", "attic")],
        disliked=[
            research.taste_line("", "Bryan Adams karaoke cover", "covers", "Not the real singer")
        ],
        hidden={"spampage"},
    )
    result = research.run(
        "Bryan Adams",
        lambda: nullcontext(llm),
        set(),
        set(),
        lambda *a: None,
        find,
        info,
        0,
        context,
    )
    assert [r.code for r in result.reels] == ["ADAMS1", "ADAMS2"]
    assert result.left_out == {"hidden account": 1}
    for prompt in (llm.asked[0], llm.asked[-1]):  # planning, rating
        assert "liked before (find more like these):\n- Heaven - Bryan Adams (@attic)" in prompt
        assert "- Bryan Adams karaoke cover (@covers): Not the real singer" in prompt
    plain = Planner()
    research.plan(plain, "Bryan Adams")
    assert "liked before" not in plain.asked[0]  # no feedback, nothing said of it


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


def test_questions_come_with_their_choices_each_once():
    context = research.Context(handle="voiceshq", summary="Isolated vocals.", presets={"v": "V"})
    llm = Planner()
    asked = research.ask(llm, "muje italian gaane", context)
    assert asked == [
        {"question": "Live or studio?", "choices": ["Live", "Studio"]},
        {"question": "Which era?", "choices": ["80s", "90s"]},
    ]
    assert "@voiceshq, which posts: Isolated vocals." in llm.asked[0]
    assert "Their request: muje italian gaane" in llm.asked[0]
    assert research.ask(Planner(broken=True), "x") == []  # the research goes on without


def test_the_answers_are_part_of_what_to_find():
    answers = [
        {"question": "Live or studio?", "answer": "Live"},
        {"question": "Which era?", "answer": "  "},
    ]
    assert research.with_answers("Bryan Adams", answers) == (
        "Bryan Adams\nTheir answers to the researcher's questions:\n- Live or studio? Live"
    )
    assert research.with_answers("", answers).startswith("Their answers")
    assert research.with_answers("Bryan Adams", []) == "Bryan Adams"


def test_a_model_that_cant_plan_searches_the_request_without_the_answers():
    asked = research.with_answers("Bryan Adams", [{"question": "Live?", "answer": "Live"}])
    assert research.plan(Planner(broken=True), asked) == ("", ["Bryan Adams"])
    context = research.Context(handle="voiceshq", presets={"v": "Vocals only; isolated"})
    answers_only = research.with_answers("", [{"question": "Live?", "answer": "Live"}])
    assert research.plan(Planner(broken=True), answers_only, context)[1][0] == "voiceshq"


def test_the_chat_is_told_each_pick_with_its_reasoning():
    from types import SimpleNamespace

    item = SimpleNamespace(
        rank=0, code="ADAMS1", handle="attic", account="Attic", song="Heaven - Bryan Adams",
        relevance=0.9, why="live and loud", matches="live rock", preset="v", preset_why="vocals",
        likes=900, comments=12, per_day=30.5, virality=0.8, score=0.81, caption="Heaven, live 1985",
    )  # fmt: skip
    found = SimpleNamespace(
        brief="Bryan Adams live", handle="voiceshq", description="", queries=["Bryan Adams live"],
        answers=[{"question": "Live or studio?", "answer": "Live"}], left_out={"didn't fit": 3},
        considered=9, items=[item],
    )  # fmt: skip
    about = research.describe(found)
    assert "What was looked for: Bryan Adams live" in about
    assert "- Live or studio? Live" in about and "3 didn't fit" in about
    assert "#1 reel by @attic: song Heaven - Bryan Adams; fit 9/10 (live and loud)" in about
    assert "900 likes, 12 comments, 30.5 a day; viral 80%; score 0.81" in about
    reply = research.chat(Planner(), about, [("Editor", "hi")], "why #1?")
    assert reply.kind == "answer" and reply.reply == "#1 fits best: fits."


@pytest.mark.parametrize(
    ("link", "code"),
    [
        ("https://www.youtube.com/shorts/qOADrQfaGPY", "yt:qOADrQfaGPY"),
        ("https://youtube.com/shorts/qOADrQfaGPY?feature=share", "yt:qOADrQfaGPY"),
        ("https://www.youtube.com/watch?v=qOADrQfaGPY", "yt:qOADrQfaGPY"),  # edited from one
        ("https://youtu.be/qOADrQfaGPY", "yt:qOADrQfaGPY"),
        ("https://www.youtube.com/@ChiaraTomao", None),
    ],
)
def test_youtube_links_have_codes_too(link, code):
    assert research.code_of(link) == code
    if code:
        assert research.reel_url(code) == "https://www.youtube.com/shorts/qOADrQfaGPY"


def test_a_research_finds_youtube_shorts_too_but_never_videos():
    llm = Planner()
    asked = []

    def find(query, count):
        asked.append(query)
        if query.startswith("site:youtube.com/shorts"):
            return [
                {"href": "https://www.youtube.com/shorts/SHORT000001"},
                {"href": "https://www.youtube.com/shorts/LONGVIDEO01"},  # says it's longer
                {"href": "https://www.youtube.com/watch?v=WATCHVIDEO1"},  # a video: skipped
            ]
        return [hit("ADAMS1")]

    def info(url):
        if "SHORT000001" in url:
            return {"channel": "Chiara", "uploader_id": "@ChiaraTomao", "media_type": "short",
                    "duration": 30, "title": "Bryan Adams live", "description": "Summer of 69",
                    "view_count": 27885, "like_count": 320}  # fmt: skip
        if "LONGVIDEO01" in url:
            return {"channel": "Full", "uploader_id": "@full", "media_type": "video",
                    "duration": 600, "title": "Bryan Adams full concert"}  # fmt: skip
        return {"channel": "attic", "description": "Bryan Adams live", "like_count": 900}

    result = research.run(
        "Bryan Adams live", lambda: nullcontext(llm), set(), set(), lambda *a: None, find, info,
        0, sites=("instagram", "shorts"),
    )  # fmt: skip
    assert sorted(r.code for r in result.reels) == ["ADAMS1", "yt:SHORT000001"]
    assert result.left_out == {"not a Short": 1}
    assert {q.split(" ")[0] for q in asked} == {research.SITE, "site:youtube.com/shorts"}
    short = next(r for r in result.reels if r.short)
    assert (short.account, short.handle, short.views, short.likes) == (
        "Chiara",
        "chiaratomao",
        27885,
        320,
    )
    assert short.url == "https://www.youtube.com/shorts/SHORT000001"
    assert short.caption == "Bryan Adams live\nSummer of 69"
    rate_prompt = llm.asked[-1]
    assert "YouTube Short | account: Chiara" in rate_prompt
    assert "Instagram reel | account: attic" in rate_prompt


def test_low_quality_reels_stay_out_and_sd_ranks_below_hd():
    llm = Planner()

    def find(query, count):
        return [hit("HDREEL001"), hit("SDREEL001"), hit("LOWREEL01")]

    sizes = {"HDREEL001": (1088, 1936), "SDREEL001": (540, 960), "LOWREEL01": (240, 426)}

    def info(url):
        code = research.code_of(url)
        width, height = sizes[code]
        formats = [
            {"width": width // 2, "height": height // 2, "tbr": 300},
            {"width": width, "height": height, "tbr": 2474.2},
            {"vcodec": "none", "tbr": 128},  # audio alone
        ]
        return {"channel": code.lower(), "description": "Bryan Adams live", "like_count": 900,
                "formats": formats}  # fmt: skip

    result = research.run(
        "Bryan Adams live", lambda: nullcontext(llm), set(), set(), lambda *a: None, find, info, 0
    )
    assert [r.code for r in result.reels] == ["HDREEL001", "SDREEL001"]
    assert result.left_out == {"low quality": 1}
    hd, sd = result.reels
    assert (hd.width, hd.height, hd.bitrate, hd.quality) == (1088, 1936, 2474.2, "HD")
    assert sd.quality == "SD" and sd.score < hd.score
    assert research.Reel(code="x", url="", query="").quality == ""  # not known


def test_a_research_looks_at_the_accounts_source_pages_too():
    llm = Planner()
    asked = []

    def find(query, count):
        asked.append(query)
        if query.endswith('"bestvocals"'):  # the source page, by name
            return [hit("PAGEREEL1"), hit("FANREEL01")]
        return [hit("ADAMS1")]

    def info(url):
        code = research.code_of(url)
        if code.startswith("yt:"):
            return {"channel": "Chiara", "uploader_id": "@ChiaraTomao", "title": "Bryan Adams",
                    "media_type": "short", "duration": 30}  # fmt: skip
        channel = {"PAGEREEL1": "bestvocals", "FANREEL01": "bestvocals_fans"}.get(code, "attic")
        return {"channel": channel, "description": f"Bryan Adams {code}", "like_count": 100}

    def channel(url, count):
        assert url == "https://www.youtube.com/@ChiaraTomao/shorts" and count == research.PER_SOURCE
        return [{"url": "https://www.youtube.com/shorts/CHIARA00001", "title": "Bryan Adams live"},
                {"url": "https://www.youtube.com/watch?v=NOTASHORT01"}]  # fmt: skip

    context = research.Context(sources=["youtube:@ChiaraTomao", "instagram:bestvocals"])
    result = research.run(
        "Bryan Adams", lambda: nullcontext(llm), set(), set(), lambda *a: None, find, info, 0,
        context, channel=channel,
    )  # fmt: skip
    found = {r.code: r.query for r in result.reels}
    # The page's own reel and the channel's Short; the fan page found by the name isn't kept.
    assert found == {
        "yt:CHIARA00001": "from @ChiaraTomao",
        "PAGEREEL1": "from @bestvocals",
        "ADAMS1": "Bryan Adams live",
    }

    def broken(url, count):
        raise RuntimeError("channel gone")

    context = research.Context(sources=["youtube:@gone"])
    kept = research.run(
        "Bryan Adams", lambda: nullcontext(Planner()), set(), set(), lambda *a: None, find, info,
        0, context, channel=broken,
    )  # fmt: skip
    assert [r.code for r in kept.reels] == ["ADAMS1"]  # one page failing costs only its reels


def test_the_trend_radar_shows_fresh_fast_reels_and_songs_several_accounts_post():
    from types import SimpleNamespace

    now = datetime(2026, 10, 30, tzinfo=UTC)

    def item(code, handle, song, days_ago, per_day):
        posted = (now - timedelta(days=days_ago)).replace(tzinfo=None)  # as the database has it
        return SimpleNamespace(code=code, handle=handle, account="", song=song,
                               posted_at=posted, per_day=per_day)  # fmt: skip

    items = [
        item("A", "attic", "Heaven - Bryan Adams", 2, 900.0),
        item("B", "rockblog", "Heaven - Bryan Adams", 5, 300.0),
        item("A", "attic", "Heaven - Bryan Adams", 2, 900.0),  # found again: once
        item("C", "attic", "Summer of 69 - Bryan Adams", 1, 5000.0),
        item("D", "attic", "Summer of '69 - Bryan Adams", 3, 10.0),  # one account only
        item("E", "old", "Heaven - Bryan Adams", 30, 99999.0),  # posted too long ago
        SimpleNamespace(code="F", handle="x", account="", song="", posted_at=None, per_day=None),
    ]
    reels, songs = research.trends(items, now)
    assert [r.code for r in reels] == ["C", "A", "B", "D"]
    assert songs == [research.SongTrend("Heaven - Bryan Adams", 2, 2, 1200.0)]


def test_a_pdf_of_songs_is_read_and_each_song_searched_for():
    text = research.read_pdf(pdf(["1. Bryan Adams - Heaven", "Sopra: Il capolavoro"]))
    assert "Bryan Adams - Heaven" in text and "Sopra: Il capolavoro" in text
    entries = research.pdf_entries(Planner(), text)
    assert entries == [
        {"singer": "Bryan Adams", "song": "Heaven", "top": "Il capolavoro\ndel 1985",
         "bottom": "Seguici"},
        {"singer": "Nobody", "song": "Nothing", "top": "", "bottom": ""},
    ]  # fmt: skip

    asked = []

    def find(query, count):
        asked.append(query)
        if "Nobody" in query:
            return []
        return [hit("HEAVEN001"), hit("HEAVEN002"), hit("USED00001"), hit("CATS00001")]

    def info(url):
        code = research.code_of(url)
        about = "cat videos" if code.startswith("CATS") else "Bryan Adams Heaven live"
        return {"channel": code.lower(), "description": about, "like_count": 100}

    llm = Planner()
    given = research.run_pdf(
        entries, lambda: nullcontext(llm), {"USED00001"}, set(), lambda *a: None, find, info, 0
    )
    assert [(entry, r.code) for entry, r in given] == [(0, "HEAVEN001"), (0, "HEAVEN002")]
    assert any("Bryan Adams Heaven" in q for q in asked)
    assert "the song 'Heaven' by Bryan Adams" in llm.asked[-1]

    def down(query, count):
        raise TimeoutError

    with pytest.raises(RuntimeError, match="didn't answer"):
        research.run_pdf(
            entries, lambda: nullcontext(llm), set(), set(), lambda *a: None, down, pause=0
        )


@pytest.mark.parametrize(
    ("top", "bottom", "placed_top", "placed_bottom"),
    [
        # The model ran the "Bottom text:" line into the top.
        ("Live Aid 1985: the show\nFollow for more", "", "Live Aid 1985: the show",
         "Follow for more"),
        # Read right already: kept.
        ("Il successo del 1977", "Seguici", "Il successo del 1977", "Seguici"),
        # A label's second line stays with it; words the PDF doesn't have stay put.
        ("Mina at 32\nsinging live\nFollow for more", "Seguici", "Mina at 32\nsinging live",
         "Follow for more\nSeguici"),
    ],
)  # fmt: skip
def test_a_pdf_songs_words_go_above_or_below_as_the_pdf_labels_them(
    top, bottom, placed_top, placed_bottom
):
    text = "\n".join([
        "Songs - Top text: the best ones",  # a heading before the songs: not this song's
        "1) Queen - Bohemian Rhapsody", "Top text: Live Aid 1985: the show",
        "Bottom text: Follow for more",
        "2) Mina - Grande", "- Above: Mina at 32", "singing live", "Below: Follow for more",
        "3) Tozzi - Ti amo", "Sopra: Il successo del 1977", "Sotto: Seguici",
    ])  # fmt: skip
    song = {"Live": "Bohemian Rhapsody", "Il s": "Ti amo", "Mina": "Grande"}[top[:4]]
    entry = {"singer": "", "song": song, "top": top, "bottom": bottom}
    assert research.placed(entry, text) == {**entry, "top": placed_top, "bottom": placed_bottom}


def test_a_pdf_song_gets_up_to_four_reels_and_is_searched_again_when_none_fit():
    class Rater:
        def chat_json(self, messages, schema, **kwargs):
            reels = [line for line in messages[-1]["content"].split("\n") if line[:1].isdigit()]
            return {"ratings": [
                {**NAMELESS, "n": n, "relevance": 9, "why": "fits", "song": "Heaven",
                 "artist": "Bryan Adams"}
                for n, _ in enumerate(reels, 1)
            ]}  # fmt: skip

    asked = []

    def find(query, count):
        asked.append(query)
        if "Nobody" in query:
            # Nothing the first time; the second searches, with other words, find one.
            return [hit("NOTHING01")] if "performance" in query else []
        return [hit(f"HEAVEN00{n}") for n in range(6)]

    def info(url):
        code = research.code_of(url)
        return {"channel": code.lower(), "description": "Bryan Adams live", "like_count": 100}

    entries = [{"singer": "Bryan Adams", "song": "Heaven", "top": "", "bottom": ""},
               {"singer": "Nobody", "song": "Nothing", "top": "", "bottom": ""}]  # fmt: skip
    given = research.run_pdf(
        entries, lambda: nullcontext(Rater()), set(), set(), lambda *a: None, find, info, 0
    )
    assert [(e, r.code) for e, r in given] == [
        (0, "HEAVEN000"), (0, "HEAVEN001"), (0, "HEAVEN002"), (0, "HEAVEN003"), (1, "NOTHING01")
    ]  # fmt: skip
    # Heaven's reels fitted at once: it was searched for once.
    assert not any("Heaven" in q and "performance" in q for q in asked)
