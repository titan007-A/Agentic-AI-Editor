"""Instagram accounts of a niche, found through public web search only (never Instagram's
own API): the planning model writes searches for the niche, the engines' results give
profile links and, in their snippets, follower counts ("12.3K Followers, ... Posts"); lists
of "top accounts" give more handles, looked up the same way. The model then judges how
well each fits, with what the person liked and disliked before, and up to RUN_SIZE that
pass the filters and were never shown to that person before are kept.

Follower counts are what the search engine last saw, so they can be a little old."""

import logging
import re
import time
from collections.abc import Callable
from contextlib import nullcontext
from dataclasses import dataclass, field

from pydantic import BaseModel, Field

from agentic_editor.llm import LLMError, parse

log = logging.getLogger(__name__)

Progress = Callable[[str, float], None]
Find = Callable[[str, int], list[dict]]

RUN_SIZE = 100  # accounts kept by one run
PER_QUERY = 30  # results asked of the engines for each search
ROUNDS = 5  # rounds of searches, each with new ones, while fewer than RUN_SIZE are found
QUERIES = 24  # searches the model writes a round
LOOKUPS = 60  # handles without a follower count looked up by name, a round
PAUSE = 1.0  # s between searches, so the engines don't turn it away
MIN_FIT = 6  # of 10
RATE_BATCH = 15

# instagram.com/<this>/ is a page, not an account.
NOT_ACCOUNTS = {
    "p", "reel", "reels", "explore", "stories", "tv", "accounts", "about", "developer",
    "legal", "directory", "web", "challenge", "direct", "emails", "privacy", "terms",
    "session", "tags", "locations", "popular", "topics", "ar", "lite", "press",
}  # fmt: skip
PROFILE = re.compile(r"instagram\.com/([A-Za-z0-9._]{2,30})/?(?:[?#]|$)", re.I)
# Newer post and reel links carry the account: instagram.com/<handle>/reel/<code>/
POSTED = re.compile(r"instagram\.com/([A-Za-z0-9._]{2,30})/(?:p|reel|reels|tv)/", re.I)
# "Maria (@vocalmaria) • Instagram ..." in a result's title
MENTION = re.compile(r"(?<![\w.])@([A-Za-z0-9._]{2,30})(?![\w])")
# "12.3K Followers", "1,234 followers", "1.2M seguidores", "3 M abonnés"
FOLLOWERS = re.compile(
    r"(\d[\d.,]*)\s*([KkMmBb]?)\s*(?:Followers|followers|Follower|seguidores|abonnés|"
    r"follower|Abonnenten|follower\b|seguaci)"
)
POSTS = re.compile(r"(\d[\d.,]*)\s*([KkMm]?)\s*(?:Posts|posts|publicaciones|publications|post\b)")


@dataclass
class Account:
    handle: str
    name: str = ""
    followers: int | None = None
    posts: int | None = None
    about: str = ""  # what the search engine says: bio, title
    query: str = ""
    fit: float | None = None  # 0-10, from the model
    why: str = ""
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

    def says(self) -> str:
        parts = []
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


def poster_of(link: str, title: str = "") -> str | None:
    """The account a post or reel result is from: in its link, or as @handle in its title."""
    found = POSTED.search(link or "")
    if found and found.group(1).lower() not in NOT_ACCOUNTS:
        return found.group(1).lower().rstrip(".")
    if "instagram.com" in (link or ""):
        named = re.search(r"\(@([A-Za-z0-9._]{2,30})\)", title or "")
        if named and named.group(1).lower() not in NOT_ACCOUNTS:
            return named.group(1).lower().rstrip(".")
    return None


def name_of(title: str, handle: str) -> str:
    """'Nat Geo (@natgeo) • Instagram photos and videos' -> 'Nat Geo'."""
    name = re.split(r"\s*[\(•|·-]\s*@?", title or "", maxsplit=1)[0].strip()
    return "" if name.lower() in ("instagram", handle) else name[:80]


def gather(
    queries: list[str], find: Find, pause: float = PAUSE, progress: Progress | None = None,
    at: tuple[float, float] = (0.0, 1.0),
) -> tuple[dict[str, Account], list[str]]:  # fmt: skip
    """Accounts the searches find: profile results (with their snippet's numbers) and
    @handles named in lists of accounts; and the searches that failed."""
    found: dict[str, Account] = {}
    failed = []
    asks = [(q, f"site:instagram.com {q}") for q in queries] + [
        (q, f"best {q} instagram accounts to follow") for q in queries[: len(queries) // 3]
    ]
    for n, (query, asked) in enumerate(asks):
        if n and pause:
            time.sleep(pause)
        if progress:
            progress(f"searching: {query}", at[0] + (at[1] - at[0]) * n / len(asks))
        try:
            hits = find(asked, PER_QUERY)
        except Exception as exc:  # the engines are third parties: one failing is one less
            log.warning("search %r failed: %s", asked, exc)
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
                if body and len(body) > len(account.about):
                    account.about = body[:400]
                account.sources.add("profile")
            elif poster := poster_of(link, title):
                # A post or reel: its account, looked up later for its numbers.
                account = found.setdefault(poster, Account(poster, query=query))
                account.sources.add("post")
                if not account.about:
                    account.about = f"posted: {' '.join(f'{title} {body}'.split())[:200]}"
            elif "instagram.com" not in link:
                # A list of accounts on some site: the handles it names.
                for mention in MENTION.findall(f"{title} {body}"):
                    handle = mention.lower().rstrip(".")
                    if handle in NOT_ACCOUNTS or "." * 2 in handle:
                        continue
                    account = found.setdefault(handle, Account(handle, query=query))
                    account.sources.add("list")
                    if not account.about:
                        account.about = f"named in: {title[:120]}"
    return found, failed


def look_up(
    accounts: list[Account], find: Find, pause: float = PAUSE, limit: int = LOOKUPS
) -> None:
    """Follower counts (and a bio) for accounts found without one, from a search for each
    one's own profile page."""
    asked = 0
    for account in accounts:
        if account.followers is not None:
            continue
        if asked >= limit:
            break
        if asked and pause:
            time.sleep(pause)
        asked += 1
        try:
            hits = find(f"site:instagram.com/{account.handle}", 5)
        except Exception as exc:
            log.warning("looking up @%s failed: %s", account.handle, exc)
            continue
        for hit in hits:
            if handle_of(hit.get("href", "")) != account.handle:
                continue
            body, title = hit.get("body", ""), hit.get("title", "")
            account.followers = followers_in(body) or followers_in(title)
            account.posts = account.posts or posts_in(body)
            account.name = account.name or name_of(title, account.handle)
            if body:
                account.about = body[:400]
            account.sources.add("profile")
            break


class _Queries(BaseModel):
    queries: list[str] = Field(description="web searches, short, in the niche's words")


class _Fit(BaseModel):
    n: int
    fit: int = Field(ge=0, le=10, description="how well this account is in the niche asked for")
    why: str = Field(description="one short line: what the account is, why it fits or not")


class _Fits(BaseModel):
    fits: list[_Fit]


PLAN = """Someone wants to find Instagram accounts in this niche, to reach out to them:
{niche}
{filters}
{taste}
Write {count} different web searches that would find such accounts' Instagram profile pages:
the kinds of account, the topics, the words such accounts put in their name and bio, in
the niche's own language(s), and related sub-niches. Short searches, no "site:", no quotes,
none like these already used: {used}"""

RATE = """Someone wants Instagram accounts in this niche, to reach out to them:
{niche}
{filters}
{taste}
For each account below (what a web search says about it), rate 0-10 how well it is an
account in that niche (10: exactly that; 5: related; 0: not at all, a brand page when
creators were asked for, a fan page of something else, a dead or spam account). Say in one
line what it is and why.
{accounts}"""


def taste_line(liked: list[str], disliked: list[str]) -> str:
    lines = []
    if liked:
        lines.append("They liked accounts like: " + "; ".join(liked[:15]))
    if disliked:
        lines.append("They did NOT want accounts like: " + "; ".join(disliked[:15]))
    return "\n".join(lines)


def plan(
    llm, niche: str, filters: Filters, taste: str, used: list[str], count: int = QUERIES
) -> list[str]:
    """Searches for the niche from the planning model; the niche itself when it fails."""
    prompt = PLAN.format(
        niche=niche.strip(),
        filters=f"Wanted: {filters.says()}." if filters.says() else "",
        taste=taste,
        count=count,
        used="; ".join(used[-40:]) or "(none yet)",
    )
    try:
        asked = [{"role": "user", "content": prompt}]
        reply = parse(_Queries, llm.chat_json(asked, _Queries.model_json_schema(), temperature=0.4))
        queries = [" ".join(q.replace('"', " ").split()) for q in reply.queries]
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
    return out[:count]


def _one_line(text: str, limit: int = 300) -> str:
    return " ".join(text.split())[:limit]


def rate(llm, niche: str, filters: Filters, taste: str, accounts: list[Account]) -> None:
    """Each account's fit (0-10) and why, from the planning model; a batch it can't rate
    keeps None (left out)."""
    for start in range(0, len(accounts), RATE_BATCH):
        group = accounts[start : start + RATE_BATCH]
        lines = "\n".join(
            f"{n}. @{a.handle} | name: {a.name or '?'} | followers: "
            f"{a.followers if a.followers is not None else '?'} | says: {_one_line(a.about)}"
            for n, a in enumerate(group, 1)
        )
        prompt = RATE.format(
            niche=niche.strip(),
            filters=f"Wanted: {filters.says()}." if filters.says() else "",
            taste=taste,
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


def _opened(planner):
    """The planning model to use in a `with`: a factory (the router's planner, which holds
    the GPU while open) is opened for just that step; a model object is used as it is."""
    if hasattr(planner, "chat_json"):
        return nullcontext(planner)
    return planner()


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
) -> tuple[list[Account], list[str]]:
    """Up to `size` accounts of the niche, best fit first, none of `seen` (shown before or
    hidden), all passing `filters`; and notes on what went wrong on the way. `planner` is
    opened only to plan and to judge, so the GPU is free while the web is searched."""
    if find is None:
        from agentic_editor.research import _engines as find
    say = progress or (lambda message, value: None)
    kept: dict[str, Account] = {}
    judged: set[str] = set()
    used: list[str] = []
    notes: list[str] = []
    for round_ in range(ROUNDS):
        lo, hi = round_ / ROUNDS, (round_ + 1) / ROUNDS
        say("planning searches", lo)
        with _opened(planner) as llm:
            queries = plan(llm, niche, filters, taste, used)
        if not queries:
            break
        used += queries
        found, failed = gather(queries, find, pause, say, (lo, lo + (hi - lo) * 0.5))
        if failed:
            notes.append(f"{len(failed)} searches got no answer from the search engines")
        fresh = [a for h, a in found.items() if h not in seen and h not in judged and h not in kept]
        say(f"looking up {sum(a.followers is None for a in fresh)} accounts' followers",
            lo + (hi - lo) * 0.55)  # fmt: skip
        look_up(fresh, find, pause)
        able = [a for a in fresh if filters.keeps(a)]
        say(f"judging {len(able)} accounts", lo + (hi - lo) * 0.75)
        if able:
            with _opened(planner) as llm:
                rate(llm, niche, filters, taste, able)
        for account in able:
            judged.add(account.handle)
            if account.fit is not None and account.fit >= MIN_FIT:
                kept[account.handle] = account
        judged.update(a.handle for a in fresh)
        if len(kept) >= size:
            break
    ranked = sorted(kept.values(), key=lambda a: (-(a.fit or 0), -(a.followers or 0)))[:size]
    if len(ranked) < size:
        notes.append(f"found {len(ranked)} new accounts that fit; the searches ran out")
    say(f"found {len(ranked)} accounts", 1.0)
    return ranked, notes
