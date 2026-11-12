from agentic_editor import niche
from agentic_editor.niche import Account, Filters


def test_follower_counts_are_read_the_way_search_engines_show_them():
    assert (
        niche.followers_in("12.3K Followers, 210 Following, 1,024 Posts - See Instagram") == 12300
    )
    assert niche.followers_in("1,234 followers") == 1234
    assert niche.followers_in("2.5M seguidores, 30 seguidos") == 2_500_000
    assert niche.followers_in("1.234 Follower") == 1234  # a thousands dot
    assert niche.followers_in("3 M abonnés") == 3_000_000
    assert niche.followers_in("Photos and videos") is None
    assert niche.posts_in("12.3K Followers, 210 Following, 1,024 Posts") == 1024


def test_only_profile_pages_are_accounts():
    assert niche.handle_of("https://www.instagram.com/vocal.coach_maria/") == "vocal.coach_maria"
    assert niche.handle_of("https://instagram.com/SingWithMe?hl=en") == "singwithme"
    pages = [
        "https://www.instagram.com/p/Cx1/",
        "https://www.instagram.com/reel/Ab/",
        "https://www.instagram.com/explore/tags/singing/",
        "https://example.com/x",
    ]
    for page in pages:
        assert niche.handle_of(page) is None
    assert niche.name_of("Maria Lopez (@vocal.coach_maria) • Instagram", "vocal.coach_maria") == (
        "Maria Lopez"
    )


def test_filters_leave_out_what_they_cant_vouch_for():
    big = Filters(min_followers=100_000)
    assert big.keeps(Account("a", followers=150_000))
    assert not big.keeps(Account("b", followers=90_000))
    assert not big.keeps(Account("c"))  # unknown: left out rather than shown wrong
    assert Filters().keeps(Account("d"))
    capped = Filters(min_followers=1000, max_followers=50_000)
    assert not capped.keeps(Account("e", followers=60_000))
    assert "at least 100,000 followers" in big.says()


def hits(*rows):
    return [{"href": h, "title": t, "body": b} for h, t, b in rows]


def test_profiles_and_handles_named_in_lists_are_gathered_once():
    def find(query, count):
        if query.startswith("site:instagram.com"):
            return hits(
                ("https://www.instagram.com/vocalmaria/", "Maria (@vocalmaria) • Instagram",
                 "45.1K Followers, 300 Following, 812 Posts - Vocal coach in Madrid"),
                ("https://www.instagram.com/p/Cx1/", "a post", "12K Followers"),
            )  # fmt: skip
        return hits(("https://blog.example/top-vocal-coaches", "Top 10 vocal coaches",
                     "Follow @vocalmaria and @belt.it.out for daily tips"))  # fmt: skip

    found, failed = niche.gather(["vocal coach spain", "singing teacher", "voz"], find, pause=0)
    assert set(found) == {"vocalmaria", "belt.it.out"} and not failed
    maria = found["vocalmaria"]
    assert maria.followers == 45_100 and maria.posts == 812 and maria.name == "Maria"
    assert found["belt.it.out"].followers is None and "list" in found["belt.it.out"].sources


def test_handles_without_a_count_are_looked_up_by_their_own_page():
    asked = []

    def find(query, count):
        asked.append(query)
        return hits(("https://www.instagram.com/belt.it.out/", "Belt It Out (@belt.it.out)",
                     "230K Followers, 5 Following, 900 Posts - belting tips"))  # fmt: skip

    accounts = [Account("belt.it.out"), Account("known", followers=10)]
    niche.look_up(accounts, find, pause=0)
    assert accounts[0].followers == 230_000 and asked == ["site:instagram.com/belt.it.out"]


class Model:
    """The planning model: searches, then fits by handle."""

    def __init__(self, fits):
        self.fits, self.prompts = fits, []

    def chat_json(self, messages, schema, **kw):
        prompt = messages[-1]["content"]
        self.prompts.append(prompt)
        if "Write" in prompt:
            n = len(self.prompts)
            return {"queries": [f"vocal coach {n}", f"singing teacher {n}"]}
        lines = [line for line in prompt.splitlines() if line[:1].isdigit()]
        out = []
        for line in lines:
            n = int(line.split(".")[0])
            handle = line.split("@", 1)[1].split()[0]
            out.append({"n": n, "fit": self.fits.get(handle, 2), "why": f"{handle} line"})
        return {"fits": out}


def test_a_run_keeps_fitting_new_accounts_best_first_up_to_its_size():
    def find(query, count):
        n = abs(hash(query)) % 1000
        return hits(*[
            (f"https://www.instagram.com/coach{n}{i}/", f"Coach (@coach{n}{i})",
             f"{(i + 1) * 20}K Followers, 10 Posts - vocal coach")
            for i in range(4)
        ])  # fmt: skip

    model = Model({})
    model.fits = {}
    seen_before = set()

    # Every coach fits 8, except the ones the person was shown already.
    class Fits(dict):
        def get(self, key, default=None):
            return 8

    model.fits = Fits()
    big = Filters(min_followers=40_000)
    accounts, _ = niche.run("vocal coaches in Spain", big, model, seen_before, pause=0, find=find,
                            size=10)  # fmt: skip
    assert len(accounts) == 10
    assert all(a.followers >= 40_000 for a in accounts)
    assert [a.followers for a in accounts] == sorted((a.followers for a in accounts), reverse=True)
    assert len({a.handle for a in accounts}) == 10
    again, _ = niche.run("vocal coaches in Spain", Filters(min_followers=40_000), model,
                         {a.handle for a in accounts}, pause=0, find=find, size=10)  # fmt: skip
    assert not {a.handle for a in again} & {a.handle for a in accounts}  # never repeated


def test_accounts_the_model_doesnt_think_fit_are_left_out_and_said_so():
    def find(query, count):
        return hits(
            ("https://www.instagram.com/brandshop/", "Shop", "5K Followers - buy stuff"),
            ("https://www.instagram.com/realcoach/", "Coach", "8K Followers - lessons"),
        )

    accounts, notes = niche.run("vocal coaches", Filters(), Model({"realcoach": 9}), set(),
                                pause=0, find=find, size=5)  # fmt: skip
    assert [a.handle for a in accounts] == ["realcoach"]
    assert accounts[0].message_url == "https://ig.me/m/realcoach"
    assert any("ran out" in n for n in notes)
