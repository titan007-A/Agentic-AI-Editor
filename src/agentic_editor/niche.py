"""Instagram accounts of a niche, found through public web search only (never Instagram's
own API): the planning model writes searches for the niche and names accounts it knows;
the engines' results give profile links and, in their snippets, follower counts ("12.3K
Followers, ... Posts"); posts and reels give their account; lists of "top accounts" (the
pages themselves, read) give more handles. Handles without numbers, and every account the
model named, are looked up by their own profile page (one the engines don't know is left
out: the model may have made it up). The model then judges how well each fits, with what
the person liked and disliked before, and up to RUN_SIZE that pass the filters and were
never shown to that person before are kept.

Pages, people or both: most want pages (theme, fan, meme, clip, news pages, a series),
not a person's own account. The model says which kind each account is while it judges
it (page, person or business), the searches are written in the words such pages use, and
a person's own account is left out when pages are asked for (pages and businesses when
people are). The model tells people from the rest reliably; a series' or magazine's own
account it calls a business as often as a page, so businesses aren't left out by kind:
their fit decides (one there to sell something is rated low when pages are wanted).

Follower counts are what the search engine last saw, so they can be a little old."""

import logging
import re
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from agentic_editor import websearch
from agentic_editor.llm import LLMError, parse

log = logging.getLogger(__name__)

Progress = Callable[[str, float], None]
# (query, how many, page) -> results with href, title and body
Find = Callable[..., list[dict]]
# Pages (of PER_QUERY results) asked of the engines for a profile search.
PAGES = (1,)
# What people type in a filter for "no filter".
ANY = {"", "any", "all", "none", "-", "anywhere", "no", "n/a"}

RUN_SIZE = 100  # accounts kept by one run
PER_QUERY = 30  # results asked of the engines for each search
ROUNDS = 8  # rounds of searches, each with new ones, while fewer than RUN_SIZE are found
QUERIES = 12  # searches the model writes a round
KNOWN = 30  # accounts the model is asked to name a round
LOOKUPS = 90  # handles without a follower count looked up by name, a round
LIST_PAGES = 4  # pages listing accounts read a round
LIST_HANDLES = 60  # handles taken from one such page at most
PAUSE = 1.0  # s between one worker's searches, so the engines don't turn it away
MIN_FIT = 6  # of 10
RATE_BATCH = 15

# instagram.com/<this>/ is a page, not an account.
NOT_ACCOUNTS = {
    "p", "reel", "reels", "explore", "stories", "tv", "accounts", "about", "developer",
    "legal", "directory", "web", "challenge", "direct", "emails", "privacy", "terms",
    "session", "tags", "locations", "popular", "topics", "ar", "lite", "press",
}  # fmt: skip
# A profile page, or one of its tabs (instagram.com/<handle>/reels/ shows the same numbers).
PROFILE = re.compile(
    r"instagram\.com/([A-Za-z0-9._]{2,30})/?(?:(?:reels|tagged|guides|igtv)/?)?(?:[?#]|$)", re.I
)
# Newer post and reel links carry the account: instagram.com/<handle>/reel/<code>/
POSTED = re.compile(r"instagram\.com/([A-Za-z0-9._]{2,30})/(?:p|reel|reels|tv)/[^/?#]", re.I)
# A post's snippet: "7,335 likes, 34 comments - elpatron7787 on September 14, 2026: ..."
MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December"
POSTER_SAYS = re.compile(
    rf"(?:^|[-\u00b7:]\s)([A-Za-z0-9._]{{2,30}}) on (?:{MONTHS}) \d{{1,2}}, \d{{4}}:"
)
# Profile links in a page's markup, to read lists of accounts.
LINKED = re.compile(r"instagram\.com/([A-Za-z0-9._]{2,30})/?[\"'?#<\s]", re.I)
# "Maria (@vocalmaria) • Instagram ..." in a result's title
MENTION = re.compile(r"(?<![\w.])@([A-Za-z0-9._]{2,30})(?![\w])")
# "12.3K Followers", "1,234 followers", "1.2M seguidores", "3 M abonnés"
FOLLOWERS = re.compile(
    r"(\d[\d.,]*)\s*([KkMmBb]?)\s*(?:Followers|followers|Follower|seguidores|abonnés|"
    r"follower|Abonnenten|follower\b|seguaci)"
)
POSTS = re.compile(r"(\d[\d.,]*)\s*([KkMm]?)\s*(?:Posts|posts|publicaciones|publications|post\b)")
# "2 Following", "984 following", "1,203 seguidos": a page follows few, a person many.
FOLLOWING = re.compile(
    r"(\d[\d.,]*)\s*([KkMm]?)\s*(?:Following|following|seguidos|abonnements|Gefolgt|seguiti)\b"
)

# What kind of account a search wants (Filters.kind) -> the kinds of account it keeps.
KINDS: dict[str, set[str] | None] = {
    "pages": {"page", "business"},
    "people": {"person"},
    "any": None,
}


@dataclass
class Account:
    handle: str
    name: str = ""
    followers: int | None = None
    posts: int | None = None
    following: int | None = None
    about: str = ""  # what the search engine says: bio, title
    query: str = ""
    fit: float | None = None  # 0-10, from the model
    why: str = ""
    kind: str = ""  # "page", "person" or "business", from the model
    sources: set[str] = field(default_factory=set)

    @property
    def url(self) -> str:
        return f"https://www.instagram.com/{self.handle}/"

    @property
    def message_url(self) -> str:
        return f"https://ig.me/m/{self.handle}"


@dataclass
class Filters:
    min_followers: int = 0
    max_followers: int | None = None
    language: str = ""
    country: str = ""
    kind: str = "any"  # "pages", "people" or "any" (KINDS)

    def says(self) -> str:
        parts = []
        if self.kind == "pages":
            parts.append("pages, not a person's own account")
        elif self.kind == "people":
            parts.append("people's own accounts, not pages and not companies")
        if self.min_followers:
            parts.append(f"at least {self.min_followers:,} followers")
        if self.max_followers:
            parts.append(f"at most {self.max_followers:,} followers")
        if self.language:
            parts.append(f"posting in {self.language}")
        if self.country:
            parts.append(f"from or about {self.country}")
        return ", ".join(parts)

    def keeps(self, account: Account) -> bool:
        if (self.min_followers or self.max_followers) and account.followers is None:
            return False  # can't tell: left out rather than shown wrong
        if account.followers is not None:
            if account.followers < self.min_followers:
                return False
            if self.max_followers and account.followers > self.max_followers:
                return False
        return True

    def takes(self, kind: str) -> bool:
        """Whether an account of this kind (as the model judged it) is one asked for."""
        wanted = KINDS.get(self.kind)
        return wanted is None or kind in wanted


def kind_of(text: str | None) -> str:
    """A kind as typed or stored ("Pages", "", None, "people") -> one of KINDS; pages when
    it says nothing, since that is what most searches are for."""
    text = (text or "").strip().lower()
    return text if text in KINDS else "pages"


def engines(query: str, count: int, page: int = 1) -> list[dict]:
    """Web results for a query (see websearch); none when it found none."""
    return websearch.search(query, count, page)


def plain(text: str) -> str:
    """A filter's text, empty when it says "any"."""
    return "" if text.strip().lower() in ANY else text.strip()


def number(digits: str, unit: str) -> int | None:
    """'12.3', 'K' -> 12300; '1,234', '' -> 1234; '1.234' (a thousands dot) -> 1234."""
    unit = unit.upper()
    text = digits.strip().rstrip(".,")
    if unit:
        value = float(text.replace(",", "."))
        return round(value * {"K": 1e3, "M": 1e6, "B": 1e9}[unit])
    plain = re.sub(r"[.,]", "", text)
    return int(plain) if plain.isdigit() else None


def followers_in(text: str) -> int | None:
    found = FOLLOWERS.search(text or "")
    if not found:
        return None
    try:
        return number(found.group(1), found.group(2))
    except ValueError:
        return None


def following_in(text: str) -> int | None:
    found = FOLLOWING.search(text or "")
    if not found:
        return None
    try:
        return number(found.group(1), found.group(2))
    except ValueError:
        return None


def posts_in(text: str) -> int | None:
    found = POSTS.search(text or "")
    if not found:
        return None
    try:
        return number(found.group(1), found.group(2))
    except ValueError:
        return None


def handle_of(link: str) -> str | None:
    """The account an Instagram link is the profile page of; None for a post, reel or
    any other page."""
    found = PROFILE.search(link or "")
    if not found:
        return None
    handle = found.group(1).lower().rstrip(".")
    if handle in NOT_ACCOUNTS or handle.startswith("."):
        return None
    return handle


def poster_of(link: str, title: str = "", body: str = "") -> str | None:
    """The account a post or reel result is from: in its link, as @handle in its title, or
    the way Instagram's snippet names it ("... comments - maria on May 3, 2026: ...")."""
    found = POSTED.search(link or "")
    if found and found.group(1).lower() not in NOT_ACCOUNTS:
        return found.group(1).lower().rstrip(".")
    if "instagram.com" in (link or ""):
        named = re.search(r"\(@([A-Za-z0-9._]{2,30})\)", title or "")
        if named and named.group(1).lower() not in NOT_ACCOUNTS:
            return named.group(1).lower().rstrip(".")
        said = POSTER_SAYS.search(body or "")
        if said and said.group(1).lower() not in NOT_ACCOUNTS:
            return said.group(1).lower().rstrip(".")
    return None


def name_of(title: str, handle: str) -> str:
    """'Nat Geo (@natgeo) • Instagram photos and videos' -> 'Nat Geo'."""
    name = re.split(r"\s*[\(•|·-]\s*@?", title or "", maxsplit=1)[0].strip()
    return "" if name.lower() in ("instagram", handle) else name[:80]


def gather(
    queries: list[str], find: Find, pause: float = PAUSE, progress: Progress | None = None,
    at: tuple[float, float] = (0.0, 1.0), lists: dict[str, str] | None = None,
    kind: str = "any",
) -> tuple[dict[str, Account], list[str]]:  # fmt: skip
    """Accounts the searches find: profile results (with their snippet's numbers), the
    accounts of posts and reels, and @handles named in lists of accounts; and the searches
    that failed. Pages that list accounts are added to `lists` (link: title), to be read.
    `kind` "pages" asks for lists of pages rather than of accounts."""
    found: dict[str, Account] = {}
    failed = []
    # Profile pages carry "N Followers, N Following, N Posts": asking for those words brings
    # profiles (with their numbers) rather than single posts.
    asks = [
        (q, f'site:instagram.com {q} "followers" posts', page) for q in queries for page in PAGES
    ]
    asks += [(q, f"site:instagram.com {q}", 1) for q in queries]
    listing = "pages" if kind == "pages" else "accounts"
    some = queries[: len(queries) // 3]
    asks += [(q, f"best {q} instagram {listing} to follow", 1) for q in some]
    done = 0
    answers = websearch.each(asks, lambda ask: find(ask[1], PER_QUERY, ask[2]), pause)
    for (query, asked, _), hits in answers:
        done += 1
        if progress:
            progress(f"searching: {query}", at[0] + (at[1] - at[0]) * done / len(asks))
        if isinstance(hits, Exception):
            log.warning("search %r failed: %s", asked, hits)
            failed.append(query)
            continue
        for hit in hits:
            link, title, body = hit.get("href", ""), hit.get("title", ""), hit.get("body", "")
            handle = handle_of(link)
            if handle:
                account = found.setdefault(handle, Account(handle, query=query))
                account.name = account.name or name_of(title, handle)
                account.followers = account.followers or followers_in(body) or followers_in(title)
                account.posts = account.posts or posts_in(body)
                account.following = account.following or following_in(body)
                if body and len(body) > len(account.about):
                    account.about = body[:400]
                account.sources.add("profile")
            elif poster := poster_of(link, title, body):
                # A post or reel: its account, looked up later for its numbers.
                account = found.setdefault(poster, Account(poster, query=query))
                account.name = account.name or name_of(title, poster)
                account.sources.add("post")
                if not account.about:
                    account.about = f"posted: {' '.join(f'{title} {body}'.split())[:200]}"
            elif "instagram.com" not in link:
                # A list of accounts on some site: the handles it names, and the page itself
                # to read (a snippet shows only a few).
                if lists is not None and link.startswith("http") and link not in lists:
                    lists[link] = title
                for mention in MENTION.findall(f"{title} {body}"):
                    handle = mention.lower().rstrip(".")
                    if handle in NOT_ACCOUNTS or "." * 2 in handle:
                        continue
                    account = found.setdefault(handle, Account(handle, query=query))
                    account.sources.add("list")
                    if not account.about:
                        account.about = f"named in: {title[:120]}"
    return found, failed


BROWSER = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) "
    "Version/18.0 Safari/605.1.15"
)
UNSEEN = re.compile(r"<(script|style|noscript)\b.*?</\1>", re.I | re.S)
# @words in pages that aren't accounts
NOT_HANDLES = {"media", "import", "font", "keyframes", "context", "type", "id", "graph"}


def read_page(url: str) -> str:
    response = httpx.get(
        url, timeout=10, follow_redirects=True, headers={"User-Agent": BROWSER}
    )  # fmt: skip
    response.raise_for_status()
    return response.text[:3_000_000]


def listed(page: str) -> list[str]:
    """The accounts a page lists: its links to Instagram profiles, else the @handles in
    its text."""
    handles: list[str] = []

    def add(handle: str) -> None:
        handle = handle.lower().rstrip(".")
        if (
            handle not in NOT_ACCOUNTS and handle not in NOT_HANDLES and handle not in handles
            and ".." not in handle and not handle.startswith(".")
        ):  # fmt: skip
            handles.append(handle)

    for link in LINKED.finditer(page):
        add(link.group(1))
    if len(handles) < 3:
        text = re.sub(r"<[^>]+>", " ", UNSEEN.sub(" ", page))
        for mention in MENTION.findall(text):
            add(mention)
    return handles[:LIST_HANDLES]


def read_lists(pages: dict[str, str], read: Callable[[str], str]) -> dict[str, Account]:
    """Accounts the pages that list accounts (link: title) name."""
    found: dict[str, Account] = {}
    for url, page in websearch.each(list(pages), read, 0.0):
        if isinstance(page, Exception):
            log.info("couldn't read %s: %s", url, page)
            continue
        for handle in listed(page):
            account = found.setdefault(handle, Account(handle, query="a list of accounts"))
            account.sources.add("list")
            if not account.about:
                account.about = f"named in: {pages[url] or url}"[:200]
    return found


def look_up(
    accounts: list[Account], find: Find, pause: float = PAUSE, limit: int = LOOKUPS
) -> None:
    """Follower counts (and a bio) for accounts found without one, from a search for each
    one's own profile page; up to `limit` of them, in the order given."""
    todo = [a for a in accounts if a.followers is None][:limit]

    def ask(account: Account) -> list[dict]:
        return find(f'site:instagram.com/{account.handle} "followers"', 5, 1)

    for account, hits in websearch.each(todo, ask, pause):
        if isinstance(hits, Exception):
            log.warning("looking up @%s failed: %s", account.handle, hits)
            continue
        for hit in hits:
            if handle_of(hit.get("href", "")) != account.handle:
                continue
            body, title = hit.get("body", ""), hit.get("title", "")
            account.followers = followers_in(body) or followers_in(title)
            account.posts = account.posts or posts_in(body)
            account.following = account.following or following_in(body)
            account.name = account.name or name_of(title, account.handle)
            if body:
                account.about = body[:400]
            account.sources.add("profile")
            break


class _Queries(BaseModel):
    queries: list[str] = Field(description="web searches, short, in the niche's words")
    accounts: list[str] = Field(
        default_factory=list,
        description="exact handles of real Instagram accounts in this niche, without @",
    )


class _Fit(BaseModel):
    n: int
    kind: Literal["page", "person", "business"] = Field(
        description="page: run as a channel about a topic (a series' page too); person: one "
        "person's own account; business: a shop, brand, gym, agency, league or product"
    )
    fit: int = Field(ge=0, le=10, description="how well this account is in the niche asked for")
    why: str = Field(description="one short line: what the account is, why it fits or not")


class _Fits(BaseModel):
    fits: list[_Fit]


PLAN = """Someone wants to find Instagram accounts in this niche, to reach out to them:
{niche}
{filters}
{taste}
{kind}
Write {count} different web searches that would find such accounts' Instagram profile pages:
the kinds of account, the topics, the words such accounts put in their name and bio, in
the niche's own language(s), and related sub-niches. Short searches, no "site:", no quotes,
none like these already used: {used}
Also name up to {known} real Instagram accounts of that kind in this niche that you know, by
their exact handle (without @), best known first. Only ones you are sure exist: none rather
than a guess. Not these, already found: {named}"""

# What the planning model is told of the kind of account wanted (Filters.kind).
PLAN_KIND = {
    "pages": """They want PAGES: accounts run as a channel about the topic, not about whoever
runs them: theme pages, fan pages, meme pages, clip and compilation pages, news and update
pages, edit pages, quote pages, an animated or web series. NOT a person's own account (an
artist's or animator's portfolio, an athlete, a coach or trainer, an influencer about their
own life) and NOT a shop, gym or agency selling something. So write searches the way such
pages describe themselves, with words like: page, daily, memes, clips, edits, fan page, updates,
news, compilation, highlights, "follow for more", "DM for promo".""",
    "people": """They want PEOPLE: one person's own account (a creator, artist, coach, expert,
athlete posting their own work or life), NOT theme or fan pages that post about a topic and
NOT companies or shops.""",
    "any": "",
}

RATE = """Someone wants Instagram accounts in this niche, to reach out to them:
{niche}
{filters}
{taste}
For each account below (what a web search says about it), first say what kind it is:
- "page": run as a channel about a topic, not about whoever runs it: a theme, fan, meme,
  clip, compilation, news, update, edit or quote page; an animated or web series', a show's
  or a character's own page, even when a studio makes it; a magazine or media outlet.
- "person": one person's own account: their art, work, sport or life; an artist, animator,
  illustrator, creator, coach, trainer, athlete, model or influencer, however big.
- "business": sells something or runs events: a shop, brand, gym, school or course, agency,
  studio's own portfolio, league, promotion or governing body, a product's official account.
A person shows: a person's name as the name; pronouns, age, "I"/"my"; a job title; "open
for commissions", "freelance", "available for work"; following hundreds or thousands. A
page shows: the topic as the name; "daily", "page", "fan page", "memes", "clips", "edits",
"updates", "follow for more", "DM for promo", "credits to the owners"; following few; many
posts.
Then rate 0-10 how well it is an account in that niche (10: exactly that; 5: related; 0: not
at all, a brand page when creators were asked for, a fan page of something else, a dead or
spam account){kind_rule}. Say in one line what it is and why.
{accounts}"""

# The rating rule for an account of another kind than the one wanted (Filters.kind).
RATE_KIND = {
    "pages": "; they want pages: a person's own account is 2 at most, and so is a shop, gym, "
    "class or agency that is there to sell something",
    "people": "; they want people: a page or a business is 2 at most",
    "any": "",
}


def taste_line(liked: list[str], disliked: list[str]) -> str:
    lines = []
    if liked:
        lines.append("They liked accounts like: " + "; ".join(liked[:15]))
    if disliked:
        lines.append("They did NOT want accounts like: " + "; ".join(disliked[:15]))
    return "\n".join(lines)


HANDLE = re.compile(r"^(?:https?://)?(?:www\.)?(?:instagram\.com/)?@?([A-Za-z0-9._]{2,30})/?$")


def plan(
    llm, niche: str, filters: Filters, taste: str, used: list[str], count: int = QUERIES,
    named: list[str] | None = None, known: int = KNOWN,
) -> tuple[list[str], list[str]]:  # fmt: skip
    """Searches for the niche from the planning model, and the handles of accounts it knows
    in it; the niche itself as the search when it fails."""
    prompt = PLAN.format(
        niche=niche.strip(),
        filters=f"Wanted: {filters.says()}." if filters.says() else "",
        taste=taste,
        kind=PLAN_KIND.get(filters.kind, ""),
        count=count,
        used="; ".join(used[-40:]) or "(none yet)",
        known=known,
        named=", ".join((named or [])[-80:]) or "(none yet)",
    )
    handles: list[str] = []
    try:
        asked = [{"role": "user", "content": prompt}]
        reply = parse(_Queries, llm.chat_json(asked, _Queries.model_json_schema(), temperature=0.4))
        queries = [" ".join(q.replace('"', " ").split()) for q in reply.queries]
        for said in reply.accounts:
            found = HANDLE.match(said.strip())
            handle = found.group(1).lower().rstrip(".") if found else ""
            if handle and handle not in NOT_ACCOUNTS and handle not in handles:
                handles.append(handle)
    except LLMError as exc:
        log.warning("couldn't plan niche searches: %s", exc)
        queries = []
    extra = " ".join(x for x in (filters.language, filters.country) if x)
    if not queries:
        queries = [niche.strip(), f"{niche.strip()} {extra}".strip()]
    seen = {q.lower() for q in used}
    out = []
    for query in queries:
        query = query.replace("site:", "").strip()
        if query and query.lower() not in seen and len(query) <= 120:
            seen.add(query.lower())
            out.append(query)
    return out[:count], handles[:known]


def _one_line(text: str, limit: int = 300) -> str:
    return " ".join(text.split())[:limit]


def _count(n: int | None) -> str:
    return "?" if n is None else str(n)


def rate(llm, niche: str, filters: Filters, taste: str, accounts: list[Account]) -> None:
    """Each account's fit (0-10) and why, from the planning model; a batch it can't rate
    keeps None (left out)."""
    for start in range(0, len(accounts), RATE_BATCH):
        group = accounts[start : start + RATE_BATCH]
        lines = "\n".join(
            f"{n}. @{a.handle} | name: {a.name or '?'} | followers: {_count(a.followers)} | "
            f"following: {_count(a.following)} | posts: {_count(a.posts)} | says: "
            f"{_one_line(a.about)}"
            for n, a in enumerate(group, 1)
        )
        prompt = RATE.format(
            niche=niche.strip(),
            filters=f"Wanted: {filters.says()}." if filters.says() else "",
            taste=taste,
            kind_rule=RATE_KIND.get(filters.kind, ""),
            accounts=lines,
        )
        try:
            asked = [{"role": "user", "content": prompt}]
            reply = parse(_Fits, llm.chat_json(asked, _Fits.model_json_schema(), temperature=0.0))
        except LLMError as exc:
            log.warning("couldn't rate accounts %d-%d: %s", start + 1, start + len(group), exc)
            continue
        for fit in reply.fits:
            if 1 <= fit.n <= len(group):
                group[fit.n - 1].fit = fit.fit
                group[fit.n - 1].why = fit.why.strip()[:200]
                group[fit.n - 1].kind = fit.kind


def _opened(planner):
    """The planning model to use in a `with`: a factory (the router's planner, which holds
    the GPU while open) is opened for just that step; a model object is used as it is."""
    if hasattr(planner, "chat_json"):
        return nullcontext(planner)
    return planner()


# What a run says of the fitting accounts it left out for being of another kind.
OTHER_KIND = {
    "pages": "left out {n} accounts in the niche that are a person's own, not pages",
    "people": "left out {n} accounts in the niche that are pages or companies, not people",
}

# Who is looked up first: accounts the model named (they must be checked: it may make one
# up), then those from posts and reels, then those named in lists.
LOOKUP_ORDER = {"suggested": 0, "post": 1, "list": 2}


def run(
    niche: str,
    filters: Filters,
    planner,
    seen: set[str],
    taste: str = "",
    progress: Progress | None = None,
    find: Find | None = None,
    pause: float = PAUSE,
    size: int = RUN_SIZE,
    read: Callable[[str], str] | None = None,
) -> tuple[list[Account], list[str]]:
    """Up to `size` accounts of the niche, best fit first, none of `seen` (shown before or
    hidden), all passing `filters` (of the kind it asks for); and notes on what went wrong
    on the way. `planner` is
    opened only to plan and to judge, so the GPU is free while the web is searched."""
    if find is None:
        find = engines
    if read is None:
        read = read_page
    say = progress or (lambda message, value: None)
    kept: dict[str, Account] = {}
    judged: set[str] = set()
    used: list[str] = []
    named: list[str] = []
    lists_read: set[str] = set()
    notes: list[str] = []
    failures = 0
    other_kind = 0  # fitting accounts of another kind than the one asked for
    for round_ in range(ROUNDS):
        lo, hi = round_ / ROUNDS, (round_ + 1) / ROUNDS
        span = hi - lo
        say("planning searches", lo)
        with _opened(planner) as llm:
            queries, known = plan(llm, niche, filters, taste, used, named=named + list(kept))
        known = [h for h in known if h not in named]
        if not queries and not known:
            break
        used += queries
        named += known
        lists: dict[str, str] = {}
        found, failed = gather(
            queries, find, pause, say, (lo, lo + span * 0.4), lists, kind=filters.kind
        )
        failures += len(failed)
        for handle in known:
            found.setdefault(handle, Account(handle, query="named by the model"))
            found[handle].sources.add("suggested")
        pages = {u: t for u, t in lists.items() if u not in lists_read}
        pages = dict(list(pages.items())[:LIST_PAGES])
        if pages:
            lists_read.update(pages)
            say(f"reading {len(pages)} lists of accounts", lo + span * 0.45)
            for handle, account in read_lists(pages, read).items():
                if handle in found:
                    found[handle].sources |= account.sources
                else:
                    found[handle] = account
        fresh = [a for h, a in found.items() if h not in seen and h not in judged and h not in kept]
        fresh.sort(key=lambda a: min((LOOKUP_ORDER.get(s, 3) for s in a.sources), default=3))
        say(f"looking up {min(LOOKUPS, sum(a.followers is None for a in fresh))} accounts' "
            "followers", lo + span * 0.5)  # fmt: skip
        look_up(fresh, find, pause)
        judged.update(a.handle for a in fresh)
        # An account only the model named that no profile page was found for may not exist.
        fresh = [a for a in fresh if a.sources != {"suggested"}]
        able = [a for a in fresh if filters.keeps(a)]
        say(f"judging {len(able)} accounts", lo + span * 0.75)
        if able:
            with _opened(planner) as llm:
                rate(llm, niche, filters, taste, able)
        for account in able:
            if account.fit is None or account.fit < MIN_FIT:
                continue
            if filters.takes(account.kind):
                kept[account.handle] = account
            else:
                other_kind += 1
        if len(kept) >= size:
            break
    if failures:
        notes.append(f"{failures} searches got no answer from the search engines")
    if other_kind:
        notes.append(OTHER_KIND[filters.kind].format(n=other_kind))
    ranked = sorted(kept.values(), key=lambda a: (-(a.fit or 0), -(a.followers or 0)))[:size]
    if len(ranked) < size:
        notes.append(f"found {len(ranked)} new accounts that fit; the searches ran out")
    say(f"found {len(ranked)} accounts", 1.0)
    return ranked, notes
