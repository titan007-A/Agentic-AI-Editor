"""A hunt: one stretch of a Kick stream read for its moments, each moment's 15-30 s clip
cut from the stream and made into a Job (kept whole: the preset only dresses it); and
followed streamers clipped while they are live (check_live), one stretch at a time.

A hunt reads the chat across its stretch (every few seconds of a short one; a long one is
sampled, then read again in full around its likely moments), the clips viewers made live,
and the sound of the stream's lightest quality (all of a stretch up to an hour, else
around the likely moments). It finds more likely moments than it wants clips and makes
only those whose opening grabs, each opening on its hook (hooks.py). Only the moments'
clips are taken in full quality, so a six-hour stream costs minutes of video, not hours.
"""

import json
import logging
import math
import re
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import sessionmaker

from agentic_editor import features
from agentic_editor.clipper import hooks, kick, moments
from agentic_editor.db import Job, KickHunt, KickStreamer, User
from agentic_editor.download import Source
from agentic_editor.settings import Settings, get_settings

log = logging.getLogger(__name__)

FEATURE = "kick_clipper"
SOURCE = "kick:"  # a Job's source: its moment's video, media/sources/<key>/
LENGTH = (moments.SHORTEST, moments.LONGEST)  # every clip's length, seconds
SHORTEST_STRETCH = 20  # seconds; a stretch shorter has no room for a clip
CHAT_READS = 600  # chat reads a hunt makes at most
READERS = 4  # chat reads at once
LOUD_MOST = 3600  # a longer stretch is listened to only around its likely moments
CHAT_AFTER = 15  # the chat is read this far past the stretch: it answers late
CLOSER = (90, 60)  # around a likely moment of a long stretch, read again: before, after
FULL_QUALITY = 1080
LIVE_STRETCH = 600  # a live stream is clipped ten minutes at a time
LIVE_LAG = 45  # its newest seconds wait: Kick's video of it is a little behind
LIVE_FIRST = 600  # following a stream already under way starts this far back
LIVE_LAST = 60  # what's left when a stream ends is clipped when at least this long
BUSY = ("reading", "listening", "cutting")
HLS_PROTOCOLS = "file,http,https,tcp,tls,crypto"


def clock(seconds: float) -> str:
    """1:02:03, or 2:03 under an hour."""
    s = max(0, int(seconds))
    h, m, s = s // 3600, s % 3600 // 60, s % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def folder_name(hunt: KickHunt) -> str:
    return " ".join((hunt.folder or "").split()) or f"Kick {hunt.username or hunt.slug}"


def key_of(hunt_id: str, index: int) -> str:
    return f"kick-{hunt_id}-{index}"


def fellow_moments(video: Path) -> list[Path]:
    """The videos of the other moments of the hunt `video` is a moment's video of (kept as
    sources/kick-<hunt>-<n>/source.mp4, see make_job); none for any other video."""
    parts = video.parent.name.split("-")
    if video.name != "source.mp4" or len(parts) != 3 or parts[0] != "kick":
        return []
    found = video.parent.parent.glob(f"kick-{parts[1]}-*/source.mp4")
    return sorted(p for p in found if p.parent != video.parent)


def hunt_of_source(source: str) -> str | None:
    """The hunt a moment's Job came from ("kick:kick-<hunt>-<n>")."""
    if not source.startswith(SOURCE):
        return None
    parts = source.removeprefix(SOURCE).split("-")
    return parts[1] if len(parts) == 3 and parts[0] == "kick" else None


def saved_source(source: str, settings: Settings) -> Source:
    """A moment's video, cut by its hunt, for a Job."""
    folder = settings.media_dir / "sources" / source.removeprefix(SOURCE)
    meta = folder / "source.json"
    if not meta.is_file():
        raise FileNotFoundError(f"the moment's video isn't kept any more ({folder.name})")
    data = json.loads(meta.read_text(encoding="utf-8"))
    path = folder / Path(data.get("path", "source.mp4")).name
    if not path.is_file():
        raise FileNotFoundError(f"the moment's video isn't kept any more ({folder.name})")
    return Source(
        path=path.resolve(),
        title=data.get("title", ""),
        uploader=data.get("uploader", ""),
        url=data.get("url", ""),
        description=data.get("description", ""),
    )


def _set(Session: sessionmaker, hunt_id: str, **fields) -> None:
    with Session() as db:
        hunt = db.get(KickHunt, hunt_id)
        if hunt is None:  # deleted while it ran
            return
        for name, value in fields.items():
            setattr(hunt, name, value)
        db.commit()


# Each likely moment's opening judged and only strong hooks made (clipper/hooks.py); off
# only where no models run (tests).
JUDGE_HOOKS = True


def run(hunt_id: str, Session: sessionmaker, settings: Settings, client=None, queue=None) -> None:
    """The hunt's turn: its moments found, cut and queued as Jobs. Everything is inside
    the try: a hunt must never stay busy."""
    from agentic_editor import jobs

    queue = queue or jobs.process
    with Session() as db:
        if db.get(KickHunt, hunt_id) is None:  # deleted while it waited
            return
    own_client = client is None
    client = client or kick.Client()
    try:
        made = _hunt(hunt_id, Session, settings, client, JUDGE_HOOKS)
        for job_id in made:
            queue(job_id)
    except Exception as exc:
        log.exception("kick hunt %s failed", hunt_id)
        reason = str(exc) if isinstance(exc, kick.KickError | ValueError) else repr(exc)
        _set(Session, hunt_id, status="failed", error=reason, message="couldn't finish")
    finally:
        if own_client:
            client.close()


def _hunt(
    hunt_id: str, Session: sessionmaker, settings: Settings, client, judge_hooks: bool = True
) -> list[str]:
    with Session() as db:
        hunt = db.get(KickHunt, hunt_id)
        hunt.status, hunt.message, hunt.error = "reading", "opening the stream's video", None
        db.commit()
        stream = kick.Stream(
            id=hunt.stream_id, title=hunt.title,
            started=(hunt.started or datetime.now(UTC)).replace(tzinfo=UTC),
            duration=0.0, live=hunt.live, source=hunt.source, uuid=hunt.uuid,
        )  # fmt: skip
        slug, channel_id, live, count = hunt.slug, hunt.channel_id, hunt.live, hunt.count
        name = hunt.username or hunt.slug
        wanted = (hunt.start, hunt.end)
        avoid = clipped_before(db, hunt)
    if not stream.source:
        raise kick.KickError("Kick hasn't made this stream's video yet; try again in a while")
    variants = client.variants(stream.source)
    light = client.playlist(kick.smallest(variants))
    if not light.pieces:
        raise kick.KickError("the stream's video is empty so far")
    stream.duration = light.duration
    start = min(max(0.0, wanted[0]), light.duration)
    end = min(wanted[1] or light.duration, light.duration)
    if end - start < SHORTEST_STRETCH:
        raise ValueError(
            f"the stretch {clock(start)}-{clock(end)} is too short for a clip "
            f"(the stream's video is {clock(light.duration)} so far)"
        )
    _set(Session, hunt_id, start=start, end=end, message="reading the chat")

    def told(message: str) -> None:
        _set(Session, hunt_id, message=message)

    reach = min(end + CHAT_AFTER, light.duration)
    lines, read = read_chat(client, channel_id, light, start, reach, told)
    try:
        clips = [(light.time_of(c.made), c.duration, c.views) for c in client.clips(slug, stream)]
    except kick.KickError as exc:
        log.info("hunt %s: viewers' clips unread: %s", hunt_id, exc)
        clips = []
    sampled = (reach - start) / CHAT_READS > kick.CHAT_SPAN
    regions = []
    if sampled or end - start > LOUD_MOST:
        rough = moments.find(start, end, count * 3, lines, read, None, clips, avoid=avoid)
        regions = regions_of(rough, start, end)
    if sampled and regions:
        told("reading the chat around the likely moments")
        for a, b in regions:
            dense, dense_read = read_chat(client, channel_id, light, a, b, told)
            upto = max((r[1] for r in dense_read), default=b)
            read = [r for r in read if not a <= r[0] < upto] + dense_read
            lines = [x for x in lines if not a <= x[0] < upto] + dense
    _set(Session, hunt_id, status="listening", message="listening to the stream")
    try:
        loudness = listen_over(light, start, end, regions)
    except (OSError, RuntimeError) as exc:
        log.warning("hunt %s: the stream's sound couldn't be read: %s", hunt_id, exc)
        loudness = None
    least = moments.STRONG if live else 0.0
    # More likely moments than clips wanted: only those with a strong hook are made.
    looked = hooks.wanted(count) if judge_hooks else count
    found = moments.find(start, end, looked, lines, read, loudness, clips, least=least, avoid=avoid)
    if not found:
        _set(
            Session, hunt_id, status="done", moments=[],
            message=f"nothing stood out in {clock(start)}-{clock(end)}",
        )  # fmt: skip
        return []
    judged = ""
    if judge_hooks:
        look = client.playlist(kick.best(variants, hooks.LOOK_HEIGHT))
        found, judged = hooks.choose(
            found, count, lambda a, b, dest: preview(look, a, b, dest), (start, end), lines,
            (name, plain_title(stream.title)), settings, told, avoid,
        )  # fmt: skip
        if not found:
            _set(
                Session, hunt_id, status="done", moments=[],
                message=f"no moment in {clock(start)}-{clock(end)} had a strong enough hook "
                f"({judged})",
            )  # fmt: skip
            return []
    _set(Session, hunt_id, status="cutting", message=f"cutting {len(found)} moments")
    full = client.playlist(kick.best(variants, FULL_QUALITY))
    made: list[str] = []
    kept: list[dict] = []
    for index, moment in enumerate(found):
        told(f"cutting moment {index + 1} of {len(found)}")
        try:
            job_id = make_job(hunt_id, index, moment, full, Session, settings)
        except (OSError, RuntimeError) as exc:
            log.warning("hunt %s: moment %d couldn't be cut: %s", hunt_id, index, exc)
            kept.append({**_moment_view(moment), "job_id": None, "error": str(exc)[-300:]})
            continue
        made.append(job_id)
        kept.append({**_moment_view(moment), "job_id": job_id})
    message = f"found {len(found)} moments; making {len(made)} clips"
    if len(made) < len(found):
        message += f" ({len(found) - len(made)} couldn't be cut)"
    if judged:
        message += f"; {judged}"
    _set(Session, hunt_id, status="done", moments=kept, message=message)
    return made


def clipped_before(db, hunt: KickHunt) -> list[tuple[float, float]]:
    """The clips the same account's other hunts already made of this stream, so a new hunt
    of it finds other moments (seconds into the stream)."""
    if not hunt.stream_id:
        return []
    others = db.scalars(
        select(KickHunt).where(
            KickHunt.stream_id == hunt.stream_id, KickHunt.id != hunt.id,
            KickHunt.owner_id.is_(None) if hunt.owner_id is None
            else KickHunt.owner_id == hunt.owner_id,
        )
    ).all()  # fmt: skip
    return [
        (float(m["start"]), float(m["end"]))
        for other in others for m in (other.moments or [])
        if m.get("job_id") and m.get("start") is not None and m.get("end") is not None
    ]  # fmt: skip


def _moment_view(moment: moments.Moment) -> dict:
    return {**asdict(moment), "start": round(moment.start, 1), "end": round(moment.end, 1)}


def read_chat(client, channel_id: int, light: kick.Playlist, start: float, end: float, told):
    """The chat of the stretch: its lines (seconds into the stream, text) and the
    stretches read. A short stretch is read whole; a long one every `step` seconds."""
    if not channel_id:
        return [], []
    step = max(kick.CHAT_SPAN, (end - start) / CHAT_READS)
    times = [start + i * step for i in range(math.ceil((end - start) / step))]
    done = 0

    def one(t: float):
        try:
            got = client.chat(channel_id, light.at(t))
        except kick.KickError as exc:
            log.info("chat at %s unread: %s", clock(t), exc)
            return None
        lines = [(light.time_of(line.at), line.text) for line in got]
        # A full page ends before its five seconds do: only up to its last line was read.
        upto = t + kick.CHAT_SPAN
        if len(got) >= 25 and lines:
            upto = min(upto, lines[-1][0] + 1)
        return lines, (t, upto)

    lines: list[tuple[float, str]] = []
    read: list[tuple[float, float]] = []
    with ThreadPoolExecutor(READERS) as pool:
        for answer in pool.map(one, times):
            done += 1
            if done % 40 == 0:
                told(f"reading the chat ({done} of {len(times)})")
            if answer is not None:
                lines += answer[0]
                read.append(answer[1])
    return lines, read


def regions_of(found: list[moments.Moment], start: float, end: float) -> list[tuple]:
    """The stretches around likely moments, read again in full (joined where they meet)."""
    spans = sorted((max(start, m.peak - CLOSER[0]), min(end, m.peak + CLOSER[1])) for m in found)
    joined: list[tuple[float, float]] = []
    for a, b in spans:
        if joined and a <= joined[-1][1]:
            joined[-1] = (joined[-1][0], max(b, joined[-1][1]))
        else:
            joined.append((a, b))
    return joined


def listen_over(light: kick.Playlist, start: float, end: float, regions: list[tuple]):
    """The loudness of the stretch, one value a second: all of it up to LOUD_MOST long,
    else only `regions` (NaN elsewhere)."""
    if end - start <= LOUD_MOST:
        return listen(light, start, end)
    if not regions:
        return None
    found = np.full(math.ceil(end - start), np.nan)
    for a, b in regions:
        heard = listen(light, a, b)
        at = int(a - start)
        found[at : at + heard.size] = heard[: max(0, found.size - at)]
    return found


def _local_playlist(pieces: list[kick.Piece], folder: Path) -> Path:
    """The pieces as a playlist of their own, for ffmpeg to read from Kick."""
    longest = math.ceil(max(p.length for p in pieces))
    rows = ["#EXTM3U", "#EXT-X-VERSION:3", f"#EXT-X-TARGETDURATION:{longest}",
            "#EXT-X-PLAYLIST-TYPE:VOD", "#EXT-X-MEDIA-SEQUENCE:0"]  # fmt: skip
    for p in pieces:
        rows += [f"#EXTINF:{p.length:.3f},", p.url]
    rows.append("#EXT-X-ENDLIST")
    path = folder / "pieces.m3u8"
    path.write_text("\n".join(rows) + "\n", encoding="utf-8")
    return path


def listen(light: kick.Playlist, start: float, end: float, rate: int = 8000) -> np.ndarray:
    """The stretch's loudness, one value (dB) a second from `start`."""
    pieces = light.between(start, end)
    if not pieces:
        return np.zeros(0)
    with tempfile.TemporaryDirectory() as folder:
        playlist = _local_playlist(pieces, Path(folder))
        cmd = [
            get_settings().ffmpeg_bin, "-nostdin", "-v", "error",
            "-protocol_whitelist", HLS_PROTOCOLS, "-i", str(playlist),
            "-vn", "-ac", "1", "-ar", str(rate), "-f", "f32le", "-",
        ]  # fmt: skip
        result = subprocess.run(cmd, capture_output=True, timeout=1800)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode(errors="replace")[-400:])
    samples = np.frombuffer(result.stdout, dtype=np.float32)
    skip = int((start - pieces[0].start) * rate)
    samples = samples[max(0, skip) : max(0, skip) + int((end - start) * rate)]
    seconds = samples.size // rate
    if seconds == 0:
        return np.zeros(0)
    power = np.mean(samples[: seconds * rate].reshape(seconds, rate) ** 2, axis=1)
    return 10 * np.log10(power + 1e-10)


def cut(
    full: kick.Playlist,
    start: float,
    end: float,
    dest: Path,
    flash: tuple[float, float] | None = None,
) -> None:
    """`start`-`end` of the stream in full quality, encoded again so it begins and ends
    on the second asked (a copy would begin at the key frame before). With `flash` (a
    cold open), that stretch of the stream plays first, then the clip."""
    spans = [flash, (start, end)] if flash else [(start, end)]
    found = [full.between(a, b) for a, b in spans]
    if not all(found):
        raise RuntimeError("the stream's video has nothing there")
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_suffix(".part.mp4")
    with tempfile.TemporaryDirectory() as folder:
        inputs: list[str] = []
        for n, ((a, b), pieces) in enumerate(zip(spans, found, strict=True)):
            sub = Path(folder) / str(n)
            sub.mkdir()
            playlist = _local_playlist(pieces, sub)
            inputs += [
                "-protocol_whitelist", HLS_PROTOCOLS, "-ss", f"{max(0.0, a - pieces[0].start):.3f}",
                "-t", f"{b - a:.3f}", "-i", str(playlist),
            ]  # fmt: skip
        if flash:
            # The two parts (one quality of one stream) joined, picture and sound, with a
            # whoosh over the cut back, as clip pages mark a cold open.
            hooks.whoosh(Path(folder) / "whoosh.wav")
            inputs += ["-i", str(Path(folder) / "whoosh.wav")]
            at = int(max(0.0, flash[1] - flash[0] - hooks.WHOOSH_BEFORE) * 1000)
            maps = [
                "-filter_complex",
                "[0:v:0]setpts=PTS-STARTPTS[v0];[0:a:0]asetpts=PTS-STARTPTS[a0];"
                "[1:v:0]setpts=PTS-STARTPTS[v1];[1:a:0]asetpts=PTS-STARTPTS[a1];"
                "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][joined];"
                f"[2:a]adelay={at}|{at}[w];"
                "[joined][w]amix=inputs=2:duration=first:normalize=0[a]",
                "-map", "[v]", "-map", "[a]",
            ]  # fmt: skip
        else:
            maps = ["-map", "0:v:0", "-map", "0:a:0?"]
        cmd = [
            get_settings().ffmpeg_bin, "-hide_banner", "-nostdin", "-v", "error", "-y",
            *inputs, *maps,
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart", str(part),
        ]  # fmt: skip
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=900)
    if result.returncode != 0 or not part.is_file() or part.stat().st_size == 0:
        part.unlink(missing_ok=True)
        raise RuntimeError(f"the moment couldn't be cut: {(result.stderr or '')[-400:]}")
    part.replace(dest)


def preview(light: kick.Playlist, start: float, end: float, dest: Path) -> float:
    """The pieces covering `start`-`end` copied as they are (a light quality, to be listened
    to and looked at, see hooks.py); where in the stream the copy begins."""
    pieces = light.between(start, end)
    if not pieces:
        raise RuntimeError("the stream's video has nothing there")
    with tempfile.TemporaryDirectory() as folder:
        playlist = _local_playlist(pieces, Path(folder))
        cmd = [
            get_settings().ffmpeg_bin, "-hide_banner", "-nostdin", "-v", "error", "-y",
            "-protocol_whitelist", HLS_PROTOCOLS, "-i", str(playlist),
            "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-f", "mpegts", str(dest),
        ]  # fmt: skip
        result = subprocess.run(cmd, capture_output=True, text=True, errors="replace", timeout=300)
    if result.returncode != 0 or not dest.is_file():
        raise RuntimeError(f"couldn't fetch the stream there: {(result.stderr or '')[-300:]}")
    return pieces[0].start


def notes_for(moment: moments.Moment) -> str:
    """What the title and text writers are told about the clip."""
    flash = hooks.flash_of(moment)
    at = moment.peak - moment.start + (flash[1] - flash[0] if flash else 0.0)
    parts = [f"A clip of a moment from a Kick live stream; it happens around {clock(at)} into "
             "this video."]  # fmt: skip
    said = [w for w in moment.why if w.startswith('"')]
    if said:
        parts.append("The chat reacted with " + ", ".join(said) + ".")
    if moment.hook:
        parts.append(hooks.notes_line(moment.hook))
    return " ".join(p for p in parts if p)


def plain_title(title: str) -> str:
    """A stream's title without its emoji and symbol runs ("🥥60 FPS GTA 6🥥LIVE" is
    "60 FPS GTA 6 · LIVE")."""
    parts = re.split(r"[^\w\s'&:!?.,#+()/-]+", title)
    return " · ".join(" ".join(p.split()) for p in parts if p.strip())


def about(name: str, stream_title: str, moment: moments.Moment) -> str:
    """The clip's description, for its post caption: whose stream, its title (which names
    the game: a vision model may not know a new one) and what the chat said."""
    text = f"A moment from {name}'s Kick live stream"
    title = plain_title(stream_title)
    text += f' titled "{title}".' if title else "."
    said = [w for w in moment.why if w.startswith('"')]
    if said:
        text += " The chat reacted with " + ", ".join(said) + "."
    if (moment.hook or {}).get("what"):
        text += f" What happens: {moment.hook['what'].rstrip('.')}."
    return text


# Each clip's layout by what it shows (owner: "jinme game play nhi hai unko bhi gameplay
# waale facecam ki tarah edit kr de rha hai"): a streamer's camera over gameplay (a video
# game, or a casino or slots game's screen) is cut into the facecam band; anything else (real
# life, all camera, a video call, a website, a video being watched) is shown whole.
FACECAM_PRESET = "kick-facecam-9x16"
IRL_PRESET = "kick-clips"
# Frames looked at, across the clip: most must be gameplay (a clip may turn from a video call
# to slots near its end, and is mostly the call).
GAME_FRAMES = 4
GAME_PROMPT = (
    "These {frames} frames, in order, are from one livestream clip; a streamer's camera may sit "
    "in a corner or along an edge over the main picture. For each frame, look at the main "
    "picture, not the camera: is it a video game being played, or a casino, slots or betting "
    "game's screen? In games, give one true or false per frame, in order: true for either, "
    "false for real life, a video call, a website or chat, a video or film being watched, a "
    "desktop, or a picture that is all camera. In shows, say in a few words what the main "
    "picture is."
)
GAME_SCHEMA = {
    "type": "object",
    "properties": {
        "games": {"type": "array", "items": {"type": "boolean"}},
        "shows": {"type": "string"},
    },
    "required": ["games", "shows"],
}


def layout_presets(have: dict, chosen: str, irl: str = "") -> tuple[str | None, str | None]:
    """A hunt's two layouts among the account's presets `have`: the facecam one (the chosen
    preset when it cuts a camera panel, else kick-facecam-9x16) and the whole-picture one
    (`irl` when given, the chosen preset when it doesn't cut a camera, else kick-clips);
    None where the account has none."""

    def facecam(name: str) -> bool:
        return name in have and have[name].facecam.enabled

    camera = chosen if facecam(chosen) else (FACECAM_PRESET if facecam(FACECAM_PRESET) else None)
    if irl and irl in have and not facecam(irl):
        whole = irl
    elif chosen in have and not facecam(chosen):
        whole = chosen
    else:
        whole = IRL_PRESET if IRL_PRESET in have and not facecam(IRL_PRESET) else None
    return camera, whole


def shows_gameplay(video: Path, settings: Settings, router=None) -> tuple[bool, str]:
    """Whether a clip is gameplay under a streamer's camera, and why: the camera finder
    sees a camera box (the clips of one hunt sharing the surest, see facecam.in_stream),
    and the vision model says the main picture is a video game or a casino game. Anything
    that can't be told is not: the whole picture never looks wrong."""
    from agentic_editor import ffmpeg, slots
    from agentic_editor.analysis import facecam
    from agentic_editor.backends import get_backends
    from agentic_editor.review import sample_frames
    from agentic_editor.router import ModelRouter

    try:
        info = ffmpeg.probe(video)
        camera = facecam.in_stream(
            video, 0.0, info.duration, (info.width, info.height), settings.models_dir,
            settings.ffmpeg_bin, fellow_moments(video),
        )  # fmt: skip
    except Exception as exc:
        log.info("couldn't look for a camera in %s: %s", video, exc)
        return False, "the camera couldn't be looked for"
    if camera is None or camera.box is None:
        return False, "no camera box over the picture"
    try:
        with tempfile.TemporaryDirectory() as tmp:
            frames = sample_frames(video, info.duration, Path(tmp), GAME_FRAMES)
            router = router or ModelRouter(settings, get_backends())
            with slots.behind(), router.vision() as vision:
                answer = vision.chat_json(
                    [{"role": "user", "content": GAME_PROMPT.format(frames=len(frames))}],
                    GAME_SCHEMA,
                    images=frames,
                    temperature=0,
                )
    except Exception as exc:
        log.info("couldn't tell whether %s is gameplay: %s", video, exc)
        return False, "the picture couldn't be looked at"
    shows = " ".join(str(answer.get("shows") or "").split())[:120]
    said = [g is True for g in (answer.get("games") or [])][: len(frames)]
    gameplay = len(said) == len(frames) and sum(said) * 2 > len(frames)
    return gameplay, f"{shows} ({sum(said)} of {len(frames)} frames gameplay)"


def clip_preset(video: Path, chosen: str, irl: str, user, settings: Settings) -> str:
    """The preset a hunt's clip is made with: the facecam layout for gameplay under a
    camera, the whole picture for anything else (see layout_presets); the chosen preset
    when the account hasn't both."""
    from agentic_editor import accounts
    from agentic_editor.presets import load_presets

    try:
        have = load_presets(accounts.presets_dir(user, settings.presets_dir))
    except Exception as exc:  # a broken preset file elsewhere mustn't stop the clip
        log.warning("couldn't read the presets for a clip's layout: %s", exc)
        return chosen
    camera, whole = layout_presets(have, chosen, irl)
    if not camera or not whole or camera == whole:
        return chosen
    game, shows = shows_gameplay(video, settings)
    log.info("clip %s: %s -> %s", video.parent.name, shows, camera if game else whole)
    return camera if game else whole


def _kept_window(folder: Path) -> list | None:
    """The stretch of stream a moment's kept video was cut from (see make_job); None when
    it doesn't say (cut before it was noted: taken as it is)."""
    try:
        return json.loads((folder / "source.json").read_text(encoding="utf-8")).get("window")
    except (OSError, ValueError):
        return None


def make_job(
    hunt_id: str,
    index: int,
    moment: moments.Moment,
    full: kick.Playlist,
    Session: sessionmaker,
    settings: Settings,
) -> str:
    """The moment's clip cut from the stream and kept as a source, and its Job (queued by
    the caller): kept whole, the preset dresses it."""
    with Session() as db:
        hunt = db.get(KickHunt, hunt_id)
        slug, uuid, name = hunt.slug, hunt.uuid, hunt.username or hunt.slug
        stream_title, preset, owner = hunt.title, hunt.preset, hunt.owner_id
        layout, irl = hunt.layout, hunt.irl_preset
        user = db.get(User, owner) if owner else None
    key = key_of(hunt_id, index)
    folder = settings.media_dir / "sources" / key
    dest = folder / "source.mp4"
    flash = hooks.flash_of(moment)
    window = [round(moment.start, 2), round(moment.end, 2), *(flash or ())]
    kept = _kept_window(folder) if dest.is_file() else None
    if kept is not None and kept != window:
        dest.unlink()  # a hunt tried again chose another moment for this place
    if not dest.is_file():
        try:
            cut(full, moment.start, moment.end, dest, flash)
        except RuntimeError:
            if not flash:
                raise
            log.info("hunt %s: moment %d cut without its cold open", hunt_id, index)
            cut(full, moment.start, moment.end, dest)
            moment.hook.pop("flash", None)
            window = window[:2]
    link = (
        kick.MOMENT_LINK.format(slug=slug, uuid=uuid, seconds=int(moment.start))
        if uuid
        else (f"https://kick.com/{slug}/")
    )
    title = f"{name} · {stream_title[:60]} · {clock(moment.peak)}".strip()
    meta = {
        "path": str(dest), "title": title, "uploader": name, "url": link,
        "description": about(name, stream_title, moment), "window": window,
    }  # fmt: skip
    (folder / "source.json").write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    if layout != "fixed":
        preset = clip_preset(dest, preset, irl, user, settings)
    with Session() as db:
        job = Job(
            source=SOURCE + key, title=title, presets=[preset], notes=notes_for(moment),
            trim=False, owner_id=owner,
        )  # fmt: skip
        db.add(job)
        db.commit()
        return job.id


def new_hunt(
    db,
    owner_id: str | None,
    slug: str,
    username: str,
    channel_id: int,
    stream: kick.Stream,
    start: float,
    end: float,
    count: int,
    preset: str,
    live: bool = False,
    folder: str = "",
    streamer_id: str | None = None,
    layout: str = "auto",
    irl_preset: str = "",
    auto: bool = False,
) -> KickHunt:
    found = KickHunt(
        owner_id=owner_id, streamer_id=streamer_id, slug=slug, username=username,
        channel_id=channel_id, stream_id=stream.id, uuid=stream.uuid, title=stream.title,
        started=stream.started.astimezone(UTC).replace(tzinfo=None), source=stream.source,
        start=start, end=end, count=count, preset=preset, live=live, folder=folder,
        moments=[], layout=layout, irl_preset=irl_preset, auto=auto,
    )  # fmt: skip
    db.add(found)
    db.flush()
    return found


def check_live(Session: sessionmaker, client=None, queue=None, now: datetime | None = None):
    """Each followed streamer that is live gets its next stretch hunted once it is
    LIVE_STRETCH long; when a stream ends, what's left of it. One stretch at a time per
    streamer. Returns the hunts started."""
    from agentic_editor import jobs

    queue = queue or jobs.hunt
    own_client = client is None
    client = client or kick.Client()
    started: list[str] = []
    try:
        with Session() as db:
            for streamer in db.scalars(select(KickStreamer).where(KickStreamer.live)).all():
                owner = db.get(User, streamer.owner_id) if streamer.owner_id else None
                if owner is None or not features.enabled(owner, FEATURE, db):
                    continue
                busy = db.scalar(
                    select(KickHunt.id).where(
                        KickHunt.streamer_id == streamer.id,
                        KickHunt.status.in_(("queued", *BUSY)),
                    )
                )
                if busy:
                    continue
                try:
                    hunt_id = _check_one(db, streamer, client)
                except kick.KickError as exc:
                    streamer.last_result = f"couldn't look: {exc}"
                    hunt_id = None
                streamer.last_checked = (now or datetime.now(UTC)).replace(tzinfo=None)
                db.commit()
                if hunt_id:
                    started.append(hunt_id)
    finally:
        if own_client:
            client.close()
    for hunt_id in started:
        log.info("kick: hunting live stretch %s", hunt_id)
        queue(hunt_id)
    return started


def _check_one(db, streamer: KickStreamer, client) -> str | None:
    channel = client.channel(streamer.slug)
    streamer.username = channel.username or streamer.username
    streamer.channel_id = channel.id
    streamer.avatar = channel.avatar or streamer.avatar
    streamer.is_live = channel.live is not None
    stream = channel.live
    if stream is None:
        return _stream_over(db, streamer, client)
    if not stream.source:
        streamer.last_result = (
            "live, but their stream videos (VODs) are off on Kick: nothing to clip from"
            if not channel.vods
            else "live; Kick hasn't made the stream's video yet"
        )
        return None
    if streamer.stream_id and streamer.stream_id != stream.id:
        # A new stream began before the last one's end was clipped: that first.
        return _stream_over(db, streamer, client)
    have = client.playlist(kick.smallest(client.variants(stream.source))).duration
    if streamer.stream_id != stream.id:
        # A new stream (or one under way when following began).
        streamer.stream_id = stream.id
        streamer.done_until = max(0.0, have - LIVE_LAG - LIVE_FIRST)
    ready = have - LIVE_LAG
    if ready - streamer.done_until < LIVE_STRETCH:
        wait = LIVE_STRETCH - (ready - streamer.done_until)
        streamer.last_result = f"live; next stretch in about {math.ceil(wait / 60)} min"
        return None
    return _stretch(db, streamer, channel.username, stream, streamer.done_until, ready, client)


def website_id(client, uuid: str) -> str:
    """The id Kick's website opens a stream's video by (its lists give an older one that
    the website no longer opens); `uuid` itself when Kick won't say."""
    if not uuid:
        return uuid
    try:
        return client.video(uuid)[1].uuid or uuid
    except kick.KickError:
        return uuid


def _stretch(db, streamer, username, stream, start: float, end: float, client) -> str:
    count = max(1, math.ceil(streamer.per_hour * (end - start) / 3600))
    stream.uuid = website_id(client, stream.uuid)
    hunt = new_hunt(
        db, streamer.owner_id, streamer.slug, username or streamer.slug, streamer.channel_id,
        stream, start, end, count, streamer.preset, live=True, folder=streamer.folder,
        streamer_id=streamer.id, irl_preset=streamer.irl_preset,
    )  # fmt: skip
    streamer.done_until = end
    streamer.last_result = f"clipping {clock(start)}-{clock(end)} of the stream"
    return hunt.id


def _stream_over(db, streamer: KickStreamer, client) -> str | None:
    """The stream clipped live has ended: what's left of it is hunted, once."""
    if not streamer.stream_id:
        streamer.last_result = "offline"
        return None
    stream = next((s for s in client.streams(streamer.slug) if s.id == streamer.stream_id), None)
    hunt_id = None
    if stream is not None and stream.source:
        total = client.playlist(kick.smallest(client.variants(stream.source))).duration
        if total - streamer.done_until >= LIVE_LAST:
            hunt_id = _stretch(
                db, streamer, streamer.username, stream, streamer.done_until, total, client
            )
    streamer.stream_id, streamer.done_until = None, 0.0
    if hunt_id is None:
        streamer.last_result = "offline"
    return hunt_id
