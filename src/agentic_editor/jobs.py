"""Background jobs: one job is one source video rendered with one or more presets."""

import json
import logging
import threading
from collections import Counter
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

from huey import SqliteHuey
from sqlalchemy import delete, func, select, update
from sqlalchemy.orm import sessionmaker

from agentic_editor import accounts, caption, pipeline, research, style
from agentic_editor.backends import get_backends
from agentic_editor.db import (
    AccountSnapshot,
    Event,
    FeedItem,
    HiddenAccount,
    Job,
    Output,
    Research,
    ResearchAccount,
    ResearchItem,
    ResearchMessage,
    StyleDraft,
    User,
    make_sessionmaker,
)
from agentic_editor.presets import PostCaption, load_presets
from agentic_editor.router import ModelRouter
from agentic_editor.settings import Settings, get_settings

log = logging.getLogger(__name__)

STAGE_PROGRESS = {
    "download": 0.02,
    "analyze": 0.05,
    "separate": 0.10,
    "transcribe": 0.25,
    "plan": 0.45,
}
RENDER_START = 0.55
RELEARN_DAYS = 7  # a research learns its account again when it was learned longer ago
SNAPSHOTS = 30  # an account's latest snapshots kept (see learn_account)
TASTE = 12  # reels of each kind (liked, not wanted) a research is told of
TASTE_LOOKED_AT = 300  # the latest reels of earlier researches looked through for them
INTERRUPTED = "stopped when the server shut down; regenerate to run it again"

_queue_file = get_settings().queue_database
_queue_file.parent.mkdir(parents=True, exist_ok=True)
huey = SqliteHuey("agentic-editor", filename=str(_queue_file), results=False)


@lru_cache
def sessions() -> sessionmaker:
    return make_sessionmaker(get_settings().database)


class Tracker:
    """Turns pipeline progress callbacks into stored events and a rising progress value."""

    def __init__(self, Session: sessionmaker, job_id: str, outputs: int):
        self.Session = Session
        self.job_id = job_id
        self.outputs = outputs  # videos the job is expected to make
        self.finished = 0
        self.value = 0.0
        self.stage = "download"

    def __call__(self, stage: str, message: str) -> None:
        if stage == "title":  # the source's own title, known once it is downloaded
            with self.Session() as db:
                db.get(Job, self.job_id).title = message
                db.commit()
            return
        # A job that got the GPU or encoder it waited for is back at the stage it was at.
        if stage == "resume":
            stage = self.stage
        elif stage != "wait":
            self.stage = stage
        share = (1 - RENDER_START) / self.outputs
        if stage == "done":
            self.finished += 1
            value = RENDER_START + share * self.finished
        elif stage in ("render", "review"):
            value = RENDER_START + share * (self.finished + (0.3 if stage == "render" else 0.7))
        else:
            value = STAGE_PROGRESS.get(stage, self.value)
        self.value = max(self.value, min(value, 0.99))
        with self.Session() as db:
            db.add(Event(job_id=self.job_id, stage=stage, message=message, progress=self.value))
            job = db.get(Job, self.job_id)
            job.stage, job.progress = stage, self.value
            db.commit()


# An autopilot job waits behind people's own jobs only this long; then it is one of them.
AGING = timedelta(minutes=30)
_claiming = threading.Lock()


def _utc(at: datetime) -> datetime:
    # SQLite hands times back without their zone; they were written in UTC.
    return at if at.tzinfo else at.replace(tzinfo=UTC)


Turn = tuple[str, str | int]  # ("job", job id) or ("caption", output id)


def next_turn(db, now: datetime) -> Turn | None:
    """The queued job or post caption whose turn it is. Jobs and captions share the
    workers and the GPU, so they take turns together: people's own work before
    autopilot's (until it has waited AGING), then the account with the least running,
    then the one served longest ago, then the oldest. One person's long queue (jobs, and
    the captions each of them adds) never holds everyone else up."""
    jobs_ = db.scalars(select(Job).where(Job.status == "queued")).all()
    captions = db.execute(
        select(Output.id, Output.caption_queued_at, Job.id, Job.owner_id, Job.created_at)
        .join(Output.job)
        .where(Output.caption_status == "queued")
    ).all()
    if not jobs_ and not captions:
        return None
    ids = [j.id for j in jobs_] + [row[2] for row in captions]
    automatic = set(db.scalars(select(FeedItem.job_id).where(FeedItem.job_id.in_(ids))))
    running = Counter(db.scalars(select(Job.owner_id).where(Job.status == "running")))
    running.update(
        db.scalars(select(Job.owner_id).join(Job.outputs).where(Output.caption_status == "writing"))
    )
    served: dict[str | None, datetime] = {}
    for owner, at in [
        *db.execute(
            select(Job.owner_id, func.max(Job.started_at))
            .where(Job.started_at.is_not(None))
            .group_by(Job.owner_id)
        ).all(),
        *db.execute(
            select(Job.owner_id, func.max(Output.caption_started_at))
            .join(Output.job)
            .where(Output.caption_started_at.is_not(None))
            .group_by(Job.owner_id)
        ).all(),
    ]:
        served[owner] = max(served.get(owner, _utc(at)), _utc(at))
    never = datetime.min.replace(tzinfo=UTC)

    def turn(owner: str | None, job_id: str, asked: datetime, ident: str):
        background = job_id in automatic and now - asked < AGING
        return (background, running[owner], served.get(owner, never), asked, ident)

    options = [
        (turn(j.owner_id, j.id, _utc(j.created_at), f"j{j.id}"), ("job", j.id)) for j in jobs_
    ] + [
        (turn(owner, job_id, _utc(asked or made), f"c{output_id:09d}"), ("caption", output_id))
        for output_id, asked, job_id, owner, made in captions
    ]
    return min(options, key=lambda option: option[0])[1]


def claim_turn(Session: sessionmaker, now: datetime | None = None) -> Turn | None:
    """Mark the job or caption whose turn it is as started and return it; None when
    nothing is queued. Every queued job and caption has one task in the queue, but a task
    runs whichever one's turn it is when a worker takes it up, not the one it was made for."""
    with _claiming:
        while True:
            with Session() as db:
                at = now or datetime.now(UTC)
                chosen = next_turn(db, at)
                if chosen is None:
                    return None
                kind, ident = chosen
                if kind == "job":
                    change = (
                        update(Job)
                        .where(Job.id == ident, Job.status == "queued")
                        .values(status="running", started_at=at)
                    )
                else:
                    change = (
                        update(Output)
                        .where(Output.id == ident, Output.caption_status == "queued")
                        .values(caption_status="writing", caption_started_at=at)
                    )
                taken = db.execute(change).rowcount
                db.commit()
                if taken:
                    return chosen


def take_turn(made_for: Turn) -> None:
    """A queued job's or caption's turn: runs whichever one's turn it is now."""
    Session = sessions()
    chosen = claim_turn(Session)
    if chosen is None:
        return  # deleted, or run by an earlier turn
    if chosen != made_for:
        log.info("the turn of %s %s went to %s %s", *made_for, *chosen)
    kind, ident = chosen
    if kind == "job":
        run_job(ident, Session, get_settings())
    else:
        run_caption(ident, Session, get_settings())


def run_job(job_id: str, Session: sessionmaker, settings: Settings, run=pipeline.run) -> None:
    track = Tracker(Session, job_id, 1)
    # Everything is inside the try, the final write too: a job must never stay "running".
    try:
        with Session() as db:
            job = db.get(Job, job_id)
            job.status = "running"
            job.started_at = job.started_at or datetime.now(UTC)
            db.commit()
            source, preset_names, notes = job.source, list(job.presets), job.notes
            owner = db.get(User, job.owner_id) if job.owner_id else None
            folder = accounts.presets_dir(owner, settings.presets_dir)

        available = load_presets(folder)
        missing = [name for name in preset_names if name not in available]
        if missing:
            raise ValueError(f"unknown preset(s): {', '.join(missing)}")
        presets = [available[name] for name in preset_names]
        track.outputs = max(1, sum(p.hook.variants for p in presets))
        result = run(source, presets, settings, get_backends(), notes, track, run_id=job_id)

        with Session() as db:
            job = db.get(Job, job_id)
            job.title = result.title
            for out in result.outputs:
                job.outputs.append(
                    Output(
                        preset=out.preset,
                        path=str(out.path),
                        thumbnail=str(out.thumbnail) if out.thumbnail else None,
                        duration=round(out.edl.duration, 2),
                        title=out.edl.title,
                        planner=out.planner,
                        renders=out.renders,
                        issues=out.issues,
                        edl=out.edl.model_dump(mode="json"),
                        confidence=out.confidence,
                        doubts=out.doubts,
                        caption_song=out.song[0] if out.song else "",
                        caption_artist=out.song[1] if out.song else "",
                    )
                )
            job.status, job.stage, job.progress = "done", "done", 1.0
            # Some versions made and some not is still a finished job; the rest is in its error.
            job.error = "; ".join(result.failures) or None
            message = "all presets rendered" if not result.failures else "rendered, with failures"
            db.add(Event(job_id=job_id, stage="finished", message=message, progress=1.0))
            captioned = [o for o in job.outputs if available[o.preset].post_caption.enabled]
            for output in captioned:
                output.caption_status = "queued"
                output.caption_queued_at = datetime.now(UTC)
            db.commit()
            for output in captioned:
                write_caption(output.id)
    except Exception as exc:
        log.exception("job %s failed", job_id)
        with Session() as db:
            job = db.get(Job, job_id)
            job.status, job.error = "failed", f"{type(exc).__name__}: {exc}"
            db.add(Event(job_id=job_id, stage="failed", message=job.error, progress=track.value))
            db.commit()


def source_text(output_path: Path, media_dir: Path) -> tuple[str, str]:
    """The title and description of the video an output was made from. Older outputs
    kept only the title next to them; the download's own record has the description."""
    meta_file = output_path.parent / "source.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8")) if meta_file.exists() else {}
    if not meta.get("description") and meta.get("url"):
        for other in (media_dir / "sources").glob("*/source.json"):
            found = json.loads(other.read_text(encoding="utf-8"))
            if found.get("url") == meta["url"]:
                meta = {**found, **{k: v for k, v in meta.items() if v}}
                break
    return meta.get("title", ""), meta.get("description", "")


WATCHED_FRAMES = 6


def caption_from_video(
    router,
    settings: Settings,
    source_ref: str,
    edl: dict,
    output_path: Path,
    description: str,
    headline: str,
    wanted: PostCaption,
) -> caption.Written:
    """A caption about what the video shows: the vision model watches frames of the stretch
    the reel uses, then the planner names its subject and writes from what is seen and
    said, the post's description and Wikipedia on that subject."""
    from agentic_editor.analysis import subjects
    from agentic_editor.analyze import file_hash
    from agentic_editor.transcript import Transcript

    source = pipeline.resolve_source(source_ref, settings)
    clips = edl.get("clips") or [{"start": 0.0, "end": 0.0}]
    start, end = float(clips[0]["start"]), float(clips[0]["end"])
    work = settings.cache_dir / file_hash(source.path)
    frames_dir = output_path.parent / "caption-frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    length = max(end - start, 0.1)
    frames = [
        subjects.grab(
            source.path, start + length * (i + 0.5) / WATCHED_FRAMES,
            frames_dir / f"frame-{i}.jpg", settings.ffmpeg_bin,
        )
        for i in range(WATCHED_FRAMES)
    ]  # fmt: skip
    # What is said: the original sound's transcript before the vocals', if the job made one.
    said = ""
    for made in sorted(work.glob("transcript-*.json"), key=lambda p: "original" not in p.name):
        heard = Transcript.model_validate_json(made.read_text(encoding="utf-8"))
        said = (
            heard.text_between(start, end) if end > start else " ".join(w.text for w in heard.words)
        )
        break
    with router.vision() as vision:
        seen = caption.watch(vision, frames)
    one_liner = edl.get("one_liner") or ""
    words = (wanted.min_words, wanted.max_words)
    with router.planner() as llm:
        llm.context = caption.CONTEXT
        topic, facts, articles = caption.video_subject(
            llm, seen, said, description, one_liner, wanted.style
        )
        sources = caption.video_sources(
            seen, said, description, source.url or "", articles, wanted.language,
            settings.cache_dir / "captions",
        )  # fmt: skip
        written = caption.write(
            llm, "", "", sources, wanted.language, headline or one_liner,
            caption.video_style(wanted.style), words, topic=topic or one_liner or "the video",
        )  # fmt: skip
    log.info("caption from the video: about %r, facts %s, articles %s", topic, facts, articles)
    return written


def run_caption(output_id: int, Session: sessionmaker, settings: Settings, router=None) -> None:
    """Write an output's post caption (see caption.py). Like a job, it never stays
    "writing": whatever happens is stored."""
    try:
        with Session() as db:
            output = db.get(Output, output_id)
            output.caption_status = "writing"
            db.commit()
            owner = db.get(User, output.job.owner_id) if output.job.owner_id else None
            available = load_presets(accounts.presets_dir(owner, settings.presets_dir))
            preset = available.get(output.preset)
            wanted = preset.post_caption if preset else PostCaption()
            decisions = output.edl.get("decisions", [])
            path = Path(output.path)
            named = output.caption_song, output.caption_artist
            topic = output.caption_topic
            source_ref, edl = output.job.source, output.edl
        headline = next((d.split(": ", 1)[1] for d in decisions if d.startswith("headline ")), "")
        title, description = source_text(path, settings.media_dir)
        cache = settings.cache_dir / "captions"
        words = (wanted.min_words, wanted.max_words)
        router = router or ModelRouter(settings, get_backends(), lambda *args: None)
        if wanted.about == "video" and not topic and not named[0]:
            # A post about what the video shows (unless a person named a song or topic).
            written = caption_from_video(
                router, settings, source_ref, edl, path, description, headline, wanted
            )
        else:
            with router.planner() as llm:
                llm.context = caption.CONTEXT
                if topic:
                    # The preset's style is written for songs; a topic gets the general one.
                    sources = caption.gather_topic(topic, wanted.language, cache)
                    written = caption.write(
                        llm, "", "", sources, wanted.language, headline, words=words, topic=topic
                    )
                else:
                    song, artist = (
                        named if named[0] else caption.identify(llm, title, description, headline)
                    )
                    with Session() as db:
                        output = db.get(Output, output_id)
                        output.caption_song, output.caption_artist = song, artist
                        db.commit()
                    sources = caption.gather(song, artist, wanted.language, cache)
                    written = caption.write(
                        llm, song, artist, sources, wanted.language, headline, wanted.style, words
                    )
        with Session() as db:
            output = db.get(Output, output_id)
            output.caption, output.caption_sources = written.text, written.sources
            output.caption_notes, output.caption_status = written.notes, "done"
            db.commit()
    except Exception as exc:
        log.exception("caption for output %s failed", output_id)
        reason = (
            str(exc) if isinstance(exc, caption.CaptionError) else f"{type(exc).__name__}: {exc}"
        )
        with Session() as db:
            output = db.get(Output, output_id)
            output.caption_status, output.caption_notes = "failed", [reason]
            db.commit()


def unused_name(base: str, taken: set[str]) -> str:
    name, n = base, 2
    while name in taken:
        name, n = f"{base}-{n}", n + 1
    return name


def run_learning(
    draft_id: str, Session: sessionmaker, settings: Settings, learn=style.learn
) -> None:
    """Draft a preset from a reference reel. It runs on the job queue, so it never has
    the GPU at the same time as an edit."""

    def progress(stage: str, message: str) -> None:
        with Session() as db:
            db.get(StyleDraft, draft_id).message = message
            db.commit()

    try:
        with Session() as db:
            draft = db.get(StyleDraft, draft_id)
            draft.status = "running"
            db.commit()
            source, name = draft.source, draft.name
            owner = db.get(User, draft.owner_id) if draft.owner_id else None
            folder = accounts.presets_dir(owner, settings.presets_dir)

        learned = learn(source, settings, get_backends(), name, progress)
        preset = learned.preset
        if not name:
            taken = set(load_presets(folder)) if folder.is_dir() else set()
            preset = preset.model_copy(update={"name": unused_name(preset.name, taken)})
        with Session() as db:
            draft = db.get(StyleDraft, draft_id)
            draft.preset, draft.notes = preset.model_dump(mode="json"), learned.notes
            draft.status, draft.message = "done", "draft ready"
            db.commit()
    except Exception as exc:
        log.exception("learning style draft %s failed", draft_id)
        with Session() as db:
            draft = db.get(StyleDraft, draft_id)
            draft.status, draft.error = "failed", f"{type(exc).__name__}: {exc}"
            db.commit()


def reel_codes(sources) -> set[str]:
    return {code for source in sources if (code := research.code_of(source))}


def taste(db, found: Research) -> dict:
    """What a person's feedback teaches a research (see research.Context): for an account,
    the reels of its earlier researches they liked or edited and those they didn't want;
    for a description alone only those they didn't want, as its topic is another each
    time; and the accounts they hid, always."""
    owner = found.owner_id
    used = reel_codes(db.scalars(select(Job.source).where(Job.owner_id == owner)))
    items = db.scalars(
        select(ResearchItem)
        .join(Research)
        .where(
            Research.owner_id == owner,
            Research.account_id == found.account_id,
            Research.id != found.id,
        )
        .order_by(ResearchItem.id.desc())
        .limit(TASTE_LOOKED_AT)
    )
    liked: dict[str, str] = {}
    disliked: dict[str, str] = {}
    for item in items:
        line = research.taste_line(item.song, item.caption, item.handle, item.verdict_reason)
        if item.verdict == "bad":
            disliked.setdefault(item.code, line)
        elif found.account_id and (item.verdict == "good" or item.code in used):
            liked.setdefault(item.code, line)
    hidden = db.scalars(select(HiddenAccount.handle).where(HiddenAccount.owner_id == owner))
    return {
        "liked": list(liked.values())[:TASTE],
        "disliked": list(disliked.values())[:TASTE],
        "hidden": set(hidden),
    }


def learn_account(
    account_id: str, Session: sessionmaker, planner, progress, find=None, info=None
) -> None:
    """Learn what an Instagram account posts (research.learn) and keep it on the account."""
    with Session() as db:
        handle = db.get(ResearchAccount, account_id).handle
    options = {k: v for k, v in (("find", find), ("info", info)) if v is not None}
    learned = research.learn(handle, planner, progress, **options)
    with Session() as db:
        account = db.get(ResearchAccount, account_id)
        account.summary, account.own, account.best = learned.summary, learned.own, learned.best
        account.learned_at = datetime.now(UTC)
        if learned.stats:
            db.add(AccountSnapshot(account_id=account_id, reels=learned.stats))
            db.flush()
            older = db.scalars(
                select(AccountSnapshot.id)
                .where(AccountSnapshot.account_id == account_id)
                .order_by(AccountSnapshot.taken_at.desc(), AccountSnapshot.id.desc())
                .offset(SNAPSHOTS)
            ).all()
            if older:
                db.execute(delete(AccountSnapshot).where(AccountSnapshot.id.in_(older)))
        db.commit()


def stale(learned_at: datetime | None, now: datetime | None = None) -> bool:
    """Whether an account was never learned, or learned too long ago."""
    if learned_at is None:
        return True
    now = now or datetime.now(UTC)
    # The database gives back the UTC time it was given, without its zone.
    return now - learned_at.replace(tzinfo=UTC) > timedelta(days=RELEARN_DAYS)


def run_account_learning(
    account_id: str, Session: sessionmaker, settings: Settings, router=None, find=None, info=None
) -> None:
    """Learn an account on its own ("Learn again", or just added). Like a job, it never
    stays "running": whatever happens is stored."""

    def progress(message: str, value: float) -> None:
        with Session() as db:
            db.get(ResearchAccount, account_id).learn_message = message
            db.commit()

    try:
        with Session() as db:
            account = db.get(ResearchAccount, account_id)
            if account is None:
                return
            account.learn_status, account.learn_error = "running", None
            db.commit()
        router = router or ModelRouter(settings, get_backends(), lambda *args: None)
        learn_account(account_id, Session, router.planner, progress, find, info)
        with Session() as db:
            account = db.get(ResearchAccount, account_id)
            account.learn_status = "done"
            account.learn_message = (
                f"read {len(account.own)} of its reels"
                if account.own
                else "none of its reels found; researches go by its presets"
            )
            db.commit()
    except Exception as exc:
        log.exception("learning account %s failed", account_id)
        with Session() as db:
            account = db.get(ResearchAccount, account_id)
            if account is not None:
                account.learn_status, account.learn_error = "failed", f"{type(exc).__name__}: {exc}"
                db.commit()


def research_context(
    research_id: str, Session: sessionmaker, settings: Settings, planner, progress, find, info
) -> research.Context:
    """Whom a research is for: its account (learned first when that wasn't done in a
    while) and the presets, or all the owner's presets for a description alone."""
    with Session() as db:
        found = db.get(Research, research_id)
        account = db.get(ResearchAccount, found.account_id) if found.account_id else None
        user = db.get(User, found.owner_id) if found.owner_id else None
        learned = taste(db, found)
    available = load_presets(accounts.presets_dir(user, settings.presets_dir))
    if account is None:
        return research.Context(presets=research.describe_presets(available), **learned)
    if stale(account.learned_at):
        learn_account(account.id, Session, planner, progress, find, info)
    with Session() as db:
        account = db.get(ResearchAccount, account.id)
    chosen = {n: p for n, p in available.items() if n in account.presets}
    return research.Context(
        handle=account.handle,
        summary=account.summary,
        presets=research.describe_presets(chosen or available),
        own=set(account.own),
        sources=list(account.sources or []),
        **learned,
    )


def start_daily(Session: sessionmaker, now: datetime | None = None) -> list[str]:
    """Start the morning research of each Instagram account whose hour has come and that
    had none yet today (a server that was off at that hour starts it once it is back).
    `now` is local time, which the hours are in. Returns the researches started."""
    now = now or datetime.now()
    started = []
    with Session() as db:
        due = db.scalars(
            select(ResearchAccount).where(
                ResearchAccount.daily_hour.is_not(None), ResearchAccount.daily_hour <= now.hour
            )
        )
        for account in due:
            if account.daily_on == now.date():
                continue
            found = Research(
                description="",
                owner_id=account.owner_id,
                account_id=account.id,
                handle=account.handle,
                sites=list(research.SITES),
                daily=True,
            )
            db.add(found)
            db.flush()
            account.daily_on = now.date()
            started.append(found.id)
        db.commit()
    for research_id in started:
        log.info("starting the morning research %s", research_id)
        find_reels(research_id)
    return started


def run_questions(
    research_id: str, Session: sessionmaker, settings: Settings, router=None, find=None, info=None
) -> None:
    """Think of the questions to ask before a research. None come (the model can't
    answer, or the research is gone), and the research goes on without them."""

    def progress(message: str, value: float) -> None:
        with Session() as db:
            db.get(Research, research_id).message = message
            db.commit()

    try:
        router = router or ModelRouter(settings, get_backends(), lambda *args: None)
        context = research_context(
            research_id, Session, settings, router.planner, progress, find, info
        )
        with Session() as db:
            description = db.get(Research, research_id).description
        with router.planner() as llm:
            questions = research.ask(llm, description, context)
    except Exception:
        log.exception("questions for research %s failed", research_id)
        questions = []
    with Session() as db:
        found = db.get(Research, research_id)
        if found is None or found.status != "asking":
            return
        found.questions = questions
        if questions:
            found.message = "a few questions first"
            db.commit()
            return
        found.status, found.message = "queued", "waiting for a free slot"
        db.commit()
    find_reels(research_id)


def run_chat(message_id: int, Session: sessionmaker, settings: Settings, router=None) -> None:
    """Answer a chat message on a finished research: from the reasoning stored with its
    reels, or by starting a follow-up research for what it asks, linked to this one, which
    like any never shows a reel shown before."""
    with Session() as db:
        said = db.get(ResearchMessage, message_id)
        if said is None or said.answered:
            return
        found = said.research
        about = research.describe(found)
        history = [
            ("Editor" if m.role == "user" else "Researcher", m.text)
            for m in found.messages
            if m.id < message_id
        ]
        text, research_id = said.text, found.id
    follow_up = None
    try:
        router = router or ModelRouter(settings, get_backends(), lambda *args: None)
        with router.planner() as llm:
            reply = research.chat(llm, about, history, text)
        answer = reply.reply.strip() or "Looking for that now."
        if reply.kind == "refine" and reply.request.strip():
            follow_up = reply.request.strip()
    except Exception:
        log.exception("chat message %s failed", message_id)
        answer = "I couldn't answer just now; try again in a minute."
    with Session() as db:
        said = db.get(ResearchMessage, message_id)
        found = db.get(Research, research_id)
        if said is None or found is None:  # the research was removed meanwhile
            return
        said.answered = True
        line = ResearchMessage(research_id=research_id, role="researcher", text=answer)
        if follow_up:
            # The answers stay: they were about what to find, and still are.
            new = Research(
                description=follow_up,
                owner_id=found.owner_id,
                account_id=found.account_id,
                handle=found.handle,
                parent_id=found.id,
                answers=found.answers,
                sites=found.sites,
            )
            db.add(new)
            db.flush()
            line.follow_up_id = new.id
        db.add(line)
        db.commit()
    if follow_up:
        find_reels(line.follow_up_id)


def run_pdf_research(
    research_id, Session, router, pdf_text, used, seen, progress, context, sites, options
) -> None:
    """A PDF research: its songs read from the PDF, then the best reels of each."""
    progress("reading the PDF", 0.02)
    with router.planner() as llm:
        entries = research.pdf_entries(llm, pdf_text)
    with Session() as db:
        db.get(Research, research_id).entries = entries
        db.commit()
    given = research.run_pdf(
        entries, router.planner, used, seen, progress, context=context, sites=sites, **options
    )
    with Session() as db:
        found = db.get(Research, research_id)
        for rank, (entry, reel) in enumerate(given):
            found.items.append(item_of(reel, rank, entry))
        empty = len(entries) - len({entry for entry, _ in given})
        found.message = f"found reels for {len(entries) - empty} of {len(entries)} songs"
        found.status, found.progress = "done", 1.0
        db.commit()


def item_of(reel: research.Reel, rank: int, entry: int | None = None) -> ResearchItem:
    return ResearchItem(
        rank=rank,
        entry=entry,
        code=reel.code,
        url=reel.url,
        query=reel.query,
        account=reel.account,
        handle=reel.handle,
        caption=reel.caption or reel.snippet or reel.title,
        likes=reel.likes,
        comments=reel.comments,
        views=reel.views,
        width=reel.width,
        height=reel.height,
        bitrate=reel.bitrate,
        duration=reel.duration,
        posted_at=reel.posted,
        thumbnail=reel.thumbnail,
        relevance=reel.relevance,
        why=reel.why,
        song=reel.song,
        matches=reel.matches,
        preset=reel.preset,
        preset_why=reel.preset_why,
        per_day=reel.per_day,
        virality=reel.virality,
        score=reel.score,
        seen=reel.seen,
    )


def run_research(
    research_id: str, Session: sessionmaker, settings: Settings, router=None, find=None, info=None
) -> None:
    """Find and rank reels for a research (see research.py), learning its account first
    when that wasn't done in a while. Like a job, it never stays "running": whatever
    happens is stored."""

    def progress(message: str, value: float) -> None:
        with Session() as db:
            found = db.get(Research, research_id)
            found.message, found.progress = message, max(found.progress, value)
            db.commit()

    try:
        with Session() as db:
            found = db.get(Research, research_id)
            found.status = "running"
            db.commit()
            owner, sites = found.owner_id, tuple(found.sites or ["instagram"])
            # The answers to the questions asked first are part of what to find.
            description = research.with_answers(found.description, found.answers or [])
            # Reels already edited, or shown by an earlier research, are left out.
            used = reel_codes(db.scalars(select(Job.source).where(Job.owner_id == owner)))
            seen = set(
                db.scalars(
                    select(ResearchItem.code)
                    .join(Research)
                    .where(Research.owner_id == owner, Research.id != research_id)
                )
            )
        router = router or ModelRouter(settings, get_backends(), lambda *args: None)
        options = {k: v for k, v in (("find", find), ("info", info)) if v is not None}
        context = research_context(
            research_id, Session, settings, router.planner, progress, find, info
        )
        with Session() as db:
            found = db.get(Research, research_id)
            mode, pdf_text = found.mode, found.pdf_text
        if mode == "pdf":
            run_pdf_research(
                research_id, Session, router, pdf_text, used, seen, progress, context, sites,
                options,
            )  # fmt: skip
            return
        result = research.run(
            description,
            router.planner,
            used,
            seen,
            progress,
            context=context,
            sites=sites,
            **options,
        )
        with Session() as db:
            found = db.get(Research, research_id)
            found.queries, found.used = result.queries, result.used
            found.considered, found.left_out = result.considered, result.left_out
            found.cutoff, found.brief = result.cutoff, result.brief
            for n, reel in enumerate(result.reels):
                found.items.append(item_of(reel, n))
            count = len(result.reels)
            found.message = f"found {count} reel{'' if count == 1 else 's'}"
            left = [
                f"{n} {what}"
                for n, what in (
                    (result.unrelated, "that didn't fit"),
                    (result.repeats, "shown before"),
                )
                if n
            ]
            if left:
                found.message += f"; left out {' and '.join(left)}"
            if result.failed_searches:
                found.message += f"; {len(result.failed_searches)} searches got no answer"
            found.status, found.progress = "done", 1.0
            db.commit()
    except Exception as exc:
        log.exception("research %s failed", research_id)
        with Session() as db:
            found = db.get(Research, research_id)
            found.status, found.error = "failed", f"{type(exc).__name__}: {exc}"
            db.commit()


@huey.task()
def process(job_id: str) -> None:
    """One queued job's turn (see take_turn): `job_id` runs only when nothing fairer waits."""
    take_turn(("job", job_id))


@huey.task()
def learn_style(draft_id: str) -> None:
    run_learning(draft_id, sessions(), get_settings())


@huey.task()
def write_caption(output_id: int) -> None:
    """One queued caption's turn, shared with the jobs (see take_turn)."""
    take_turn(("caption", output_id))


# How long a research's turn waits before looking again while another research runs.
RESEARCH_RETRY = 10
BUSY = "busy"


def next_research(db) -> Research | None:
    """The queued research whose turn it is: the account served longest ago first, then
    the oldest research, so one person's pile of researches doesn't hold anyone up."""
    queued = db.scalars(select(Research).where(Research.status == "queued")).all()
    if not queued:
        return None
    served = dict(
        db.execute(
            select(Research.owner_id, func.max(Research.started_at))
            .where(Research.started_at.is_not(None))
            .group_by(Research.owner_id)
        ).all()
    )
    never = datetime.min.replace(tzinfo=UTC)

    def turn(found: Research):
        last = served.get(found.owner_id)
        return (_utc(last) if last else never, _utc(found.created_at), found.id)

    return min(queued, key=turn)


def claim_research(Session: sessionmaker, now: datetime | None = None) -> str | None:
    """Mark the research whose turn it is running and return its id; BUSY while another
    research runs (one at a time: two at once search the same sites together, which then
    refuse them, and hold both workers the jobs need); None when none is queued."""
    with _claiming:
        while True:
            with Session() as db:
                running = select(func.count()).select_from(Research)
                if db.scalar(running.where(Research.status == "running")):
                    return BUSY
                at = now or datetime.now(UTC)
                found = next_research(db)
                if found is None:
                    return None
                taken = db.execute(
                    update(Research)
                    .where(Research.id == found.id, Research.status == "queued")
                    .values(status="running", started_at=at)
                ).rowcount
                db.commit()
                if taken:
                    return found.id


@huey.task()
def find_reels(research_id: str) -> None:
    """One queued research's turn: runs the research whose turn it is (see
    claim_research), or, while another runs, waits RESEARCH_RETRY seconds in the queue's
    schedule, holding no worker meanwhile."""
    Session = sessions()
    chosen = claim_research(Session)
    if chosen == BUSY:
        find_reels.schedule((research_id,), delay=RESEARCH_RETRY)
        return
    if chosen is None:
        return  # removed, or run by an earlier turn
    if chosen != research_id:
        log.info("research %s's turn went to research %s", research_id, chosen)
    run_research(chosen, Session, get_settings())


@huey.task()
def read_account(account_id: str) -> None:
    run_account_learning(account_id, sessions(), get_settings())


@huey.task()
def ask_questions(research_id: str) -> None:
    run_questions(research_id, sessions(), get_settings())


@huey.task()
def answer_chat(message_id: int) -> None:
    run_chat(message_id, sessions(), get_settings())


PROCESS, LEARN = process.task_class.__name__, learn_style.task_class.__name__
CAPTION, RESEARCH = write_caption.task_class.__name__, find_reels.task_class.__name__
ACCOUNT, QUESTIONS = read_account.task_class.__name__, ask_questions.task_class.__name__
CHAT = answer_chat.task_class.__name__


def recover(Session: sessionmaker) -> None:
    """Settle what a server that stopped mid-job left behind; run it before the worker
    starts. The queue drops a task once a worker takes it, so a job or draft still marked
    running will never finish: it is marked failed. One still queued whose task is gone
    (it never reached the queue) is queued again."""
    pending = {(task.name, task.args[0]) for task in huey.pending() if task.args}
    # A research turn waiting for the running one sits in the schedule, not the queue.
    pending |= {(task.name, task.args[0]) for task in huey.scheduled() if task.args}
    with Session() as db:
        for job in db.scalars(select(Job).where(Job.status == "running")).all():
            job.status, job.error = "failed", INTERRUPTED
            db.add(Event(job_id=job.id, stage="failed", message=INTERRUPTED, progress=job.progress))
        for draft in db.scalars(select(StyleDraft).where(StyleDraft.status == "running")).all():
            draft.status, draft.error = "failed", INTERRUPTED
        for found in db.scalars(select(Research).where(Research.status == "running")).all():
            found.status, found.error = "failed", INTERRUPTED
        learning = select(ResearchAccount).where(ResearchAccount.learn_status == "running")
        for account in db.scalars(learning).all():
            account.learn_status, account.learn_error = "failed", INTERRUPTED
        for output in db.scalars(select(Output).where(Output.caption_status == "writing")).all():
            output.caption_status, output.caption_notes = "failed", [INTERRUPTED]
        # A turn runs whichever job or caption is due, so what counts is a turn for each.
        turns = sum(1 for name, _ in pending if name in (PROCESS, CAPTION))
        queued_jobs = list(db.scalars(select(Job.id).where(Job.status == "queued")))
        queued_captions = list(
            db.scalars(select(Output.id).where(Output.caption_status == "queued"))
        )
        missing = max(0, len(queued_jobs) + len(queued_captions) - turns)
        unturned = [(PROCESS, j) for j in queued_jobs if (PROCESS, j) not in pending] + [
            (CAPTION, o) for o in queued_captions if (CAPTION, o) not in pending
        ]
        lost = unturned[:missing]
        lost_jobs = [ident for name, ident in lost if name == PROCESS]
        lost_captions = [ident for name, ident in lost if name == CAPTION]
        lost_drafts = [
            draft_id
            for draft_id in db.scalars(select(StyleDraft.id).where(StyleDraft.status == "queued"))
            if (LEARN, draft_id) not in pending
        ]
        # Like jobs: a turn runs whichever research is due, so a turn for each is enough.
        research_turns = sum(1 for name, _ in pending if name == RESEARCH)
        queued_research = list(db.scalars(select(Research.id).where(Research.status == "queued")))
        lost_research = [r for r in queued_research if (RESEARCH, r) not in pending][
            : max(0, len(queued_research) - research_turns)
        ]
        queued = select(ResearchAccount.id).where(ResearchAccount.learn_status == "queued")
        lost_accounts = [a for a in db.scalars(queued) if (ACCOUNT, a) not in pending]
        # Asking with no questions yet: they were being thought of. With questions, it
        # waits for the person, not the queue.
        thinking = [
            found.id
            for found in db.scalars(select(Research).where(Research.status == "asking"))
            if not found.questions and (QUESTIONS, found.id) not in pending
        ]
        waiting = select(ResearchMessage.id).where(
            ResearchMessage.role == "user", ResearchMessage.answered.is_(False)
        )
        lost_messages = [m for m in db.scalars(waiting) if (CHAT, m) not in pending]
        db.commit()
    for job_id in lost_jobs:
        log.info("queueing job %s again", job_id)
        process(job_id)
    for draft_id in lost_drafts:
        log.info("queueing style draft %s again", draft_id)
        learn_style(draft_id)
    for output_id in lost_captions:
        log.info("queueing the caption of output %s again", output_id)
        write_caption(output_id)
    for research_id in lost_research:
        log.info("queueing research %s again", research_id)
        find_reels(research_id)
    for account_id in lost_accounts:
        log.info("queueing the learning of account %s again", account_id)
        read_account(account_id)
    for research_id in thinking:
        log.info("queueing the questions of research %s again", research_id)
        ask_questions(research_id)
    for message_id in lost_messages:
        log.info("queueing chat message %s again", message_id)
        answer_chat(message_id)
