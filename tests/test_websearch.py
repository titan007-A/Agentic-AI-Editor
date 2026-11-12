import pytest

from agentic_editor import websearch
from agentic_editor.websearch import SearchError

R = "https://r.search.yahoo.com/_ylt=x;_ylu=y/RV=2/RE=1/RO=10/RU={}/RK=2/RS=abc-"


def ru(link: str) -> str:
    """A link the way Yahoo wraps it (its escapes in lower case)."""
    import re
    from urllib.parse import quote

    return R.format(re.sub(r"%[0-9A-F]{2}", lambda m: m.group().lower(), quote(link, safe="")))


# The two layouts Yahoo serves (trimmed from real pages): ddgs read only the first.
OLD_LAYOUT = f"""<html><body><div id="web"><ol>
<li><div class="dd fst algo algo-sr relsrch Sr"><div class="compTitle options-toggle">
<a class="d-ib" href="{ru("https://www.instagram.com/gym.fail.video/reels/")}">
<div class="d-ib p-abs"><span class="d-ib va-mid"><span>Instagram</span>
https://www.instagram.com › gym › reels</span></div>
<h3 class="title"><span><b>gym</b> <b>fail</b>.video (@<b>gym</b>.<b>fail</b>.video) • Instagram
photos and videos</span></h3></a></div>
<div class="compText aAbs"><p> 144 <b>Followers</b>, 318 Following, 170 <b>Posts</b> - See
Instagram photos and videos</p></div></div></li>
<li><div class="dd algo algo-sr relsrch Sr"><div class="compTitle">
<a href="https://www.bing.com/aclick?ld=ad"><h3 class="title">An ad</h3></a></div></div></li>
</ol></div></body></html>"""

NEW_LAYOUT = f"""<html><body>
<section class="dd fst algo s-algo algo-sr Sr"><div class="grp grp-talgo">
<div class="compTitle p-r"><h3 class="title d-ib mt-42">
<a class="s-title fz-m" href="{ru("https://www.instagram.com/cherylporterdiva/")}"
aria-label="Cheryl Porter Vocal Coach (@cherylporterdiva) - Instagram"><span class="title-url">
<span>Instagram</span>https://www.instagram.com › cherylporterdiva</span>Cheryl Porter Vocal Coach
(@cherylporterdiva) - Instagram</a></h3></div></div>
<div class="grp grp-talgo-ext"><div class="compText"><p class="s-desc"> 6M Followers, 1,122
Following, 3,348 Posts - Cheryl Porter <b>Vocal</b> <b>Coach</b></p></div>
<section class="dd lst algo s-algo algo-sr richAlgo"><div class="grp grp-talgo">
<div class="compTitle p-r"><h3 class="title"><a class="s-title fz-m"
href="{ru("https://www.instagram.com/reel/DdSAPOIveYK/")}"
aria-label="El Patron | Ya no vayas al &lt;b&gt;gym&lt;/b&gt;">El Patron</a></h3></div></div>
<div class="grp grp-talgo-ext"><div class="compText"><p class="s-desc">7,335 likes, 34 comments -
elpatron7787 on September 14, 2026: "Ya no vayas al gym"</p></div></div></section>
</div></section></body></html>"""

NOTHING_PAGE = """<html><body><section class="dd algo">
<p>We did not find results for: <b>site:instagram.com/zzqq</b>.</p></section></body></html>"""


def test_both_of_yahoos_page_layouts_are_read():
    old = websearch.yahoo_results(OLD_LAYOUT)
    assert old == [
        {
            "href": "https://www.instagram.com/gym.fail.video/reels/",
            "title": "gym fail.video (@gym.fail.video) • Instagram photos and videos",
            "body": "144 Followers, 318 Following, 170 Posts - See Instagram photos and videos",
        }
    ]  # the ad is left out
    new = websearch.yahoo_results(NEW_LAYOUT)
    assert [h["href"] for h in new] == [
        "https://www.instagram.com/cherylporterdiva/",
        "https://www.instagram.com/reel/DdSAPOIveYK/",
    ]
    assert new[0]["title"] == "Cheryl Porter Vocal Coach (@cherylporterdiva) - Instagram"
    assert new[0]["body"].startswith("6M Followers, 1,122 Following")
    assert new[1]["title"] == "El Patron | Ya no vayas al gym"
    assert "elpatron7787 on September 14" in new[1]["body"]


class Page:
    def __init__(self, text, status=200):
        self.text, self.status_code = text, status


class Client:
    def __init__(self, pages):
        self.pages, self.asked = list(pages), []

    def get(self, url, params=None):
        self.asked.append(dict(params or {}))
        return self.pages.pop(0)


def test_a_search_asks_as_many_yahoo_pages_as_the_results_asked_for_take(monkeypatch):
    def page(start, n):
        rows = "".join(
            f'<section class="dd algo"><h3><a class="s-title" href="{ru(f"https://x.test/{i}")}"'
            f' aria-label="r{i}">r{i}</a></h3><p class="s-desc">about {i}</p></section>'
            for i in range(start, start + n)
        )
        return Page(f"<html><body>{rows}</body></html>")

    client = Client([page(1, 7), page(8, 7), page(15, 7)])
    monkeypatch.setattr(websearch, "_client", lambda fresh=False: client)
    monkeypatch.setattr(websearch, "YAHOO_PAUSE", 0)
    hits = websearch.yahoo("q", 15)
    assert [h["href"] for h in hits] == [f"https://x.test/{i}" for i in range(1, 16)]
    assert [a.get("b") for a in client.asked] == [None, "8", "15"]


def test_yahoo_saying_nothing_matched_is_an_answer_and_a_page_it_cant_read_is_not(monkeypatch):
    client = Client([Page(NOTHING_PAGE)])
    monkeypatch.setattr(websearch, "_client", lambda fresh=False: client)
    assert websearch.yahoo("site:instagram.com/zzqq", 5) == []
    # A page with neither results nor "nothing found": asked again with a fresh client, then
    # a failure (so another engine is asked).
    client = Client([Page("<html>check</html>"), Page("<html>check</html>")])
    monkeypatch.setattr(websearch, "_client", lambda fresh=False: client)
    with pytest.raises(SearchError):
        websearch.yahoo("q", 5)
    assert len(client.asked) == 2


@pytest.fixture
def engines(monkeypatch, tmp_path):
    websearch.reset()
    monkeypatch.setattr(websearch, "_cache_dir", lambda: tmp_path)
    calls = []

    def use(*named):
        def engine(name, behaviour):
            def run(query, count, page=1):
                calls.append(name)
                if isinstance(behaviour, Exception):
                    raise behaviour
                return behaviour

            return name, run

        monkeypatch.setattr(websearch, "_engines", lambda: [engine(n, b) for n, b in named])
        return calls

    yield use
    websearch.reset()


def test_when_an_engine_fails_the_next_is_asked_and_after_failing_often_it_rests(engines):
    hit = [{"href": "https://x.test/1", "title": "t", "body": "b"}]
    calls = engines(("yahoo", TimeoutError("timed out")), ("ddgs", hit))
    for n in range(websearch.FAILS_TO_REST):
        assert websearch.search(f"q{n}", 5) == hit
    assert calls.count("yahoo") == websearch.FAILS_TO_REST
    calls.clear()
    assert websearch.search("another", 5) == hit
    assert calls == ["ddgs"]  # yahoo rests


def test_a_search_every_engine_fails_says_so_and_a_cached_answer_needs_no_engine(engines):
    calls = engines(("yahoo", TimeoutError("timed out")), ("ddgs", ConnectionError("reset")))
    with pytest.raises(SearchError, match="timed out"):
        websearch.search("q", 5)
    hit = [{"href": "https://x.test/1", "title": "t", "body": "b"}]
    calls = engines(("yahoo", hit))
    assert websearch.search("cached", 5) == hit
    calls.clear()
    assert websearch.search("cached", 5) == hit and calls == []
    # Nothing found isn't kept: the next search asks again.
    calls = engines(("yahoo", []))
    websearch.search("empty", 5)
    websearch.search("empty", 5)
    assert calls == ["yahoo", "yahoo"]
