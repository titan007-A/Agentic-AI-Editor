"""Runs the render: soundtrack, overlays, captions and the final encode."""

import logging
import math
import re
import time
from collections.abc import Callable
from pathlib import Path

from agentic_editor import ffmpeg, framing, subtitles
from agentic_editor.analysis import layout, onscreen
from agentic_editor.analysis.faces import Sample
from agentic_editor.analysis.subjects import Subject
from agentic_editor.analysis.watermarks import Watermark
from agentic_editor.analyze import Analysis
from agentic_editor.backends import Backends, encoder_args
from agentic_editor.edl import EDL, Clip, Cue
from agentic_editor.planner import voiced_regions
from agentic_editor.presets import CaptionStyle, Preset
from agentic_editor.render import audio, captions, fonts, grade, text
from agentic_editor.render.video import (
    Layers,
    build_graph,
    crop_size,
    picture_rect,
    video_band,
)
from agentic_editor.settings import ROOT, Settings, get_settings

log = logging.getLogger(__name__)
_CROP = re.compile(r"crop=(\d+):(\d+):(\d+):(\d+)")
BANNER_GAP = 24  # px between a banner and anything else drawn on the frame
LAYOUT_FRAMES = 24  # frames the picture's place in a padded layout is read from
LAYOUT_WIDTH = 540  # px they are read at; half a 1080 frame, so edges land within 2 px


def asset_path(name: str) -> Path:
    path = Path(name).expanduser()
    path = path if path.is_absolute() else ROOT / path
    if not path.exists():
        raise FileNotFoundError(f"preset asset not found: {path}")
    return path


def detect_black_bars(video: Path, start: float) -> tuple[int, int, int, int] | None:
    result = ffmpeg.run(["-ss", f"{start:.3f}", "-t", "5", "-i", str(video),
                         "-vf", "cropdetect=24:2:0", "-f", "null", "-"])  # fmt: skip
    matches = _CROP.findall(result.stderr)
    return tuple(int(v) for v in matches[-1]) if matches else None


def padded_picture(
    video: Path, start: float, end: float, size: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """Where the picture sits in a padded layout, such as a repost with its caption on
    the padding above, as x, y, width, height; None when the frame isn't padded."""
    small = (LAYOUT_WIDTH, round(LAYOUT_WIDTH * size[1] / size[0] / 2) * 2)
    frames = layout.sample(
        video, end - start, LAYOUT_FRAMES, small, get_settings().ffmpeg_bin, start=start
    )
    colour = layout.background(frames)
    if len(frames) == 0 or colour is None:
        return None
    x0, y0, x1, y1 = layout.picture_box(frames, colour)
    # Rounded inwards, so no row of the padding is left along an edge.
    left, top = math.ceil(x0 * size[0] / 2) * 2, math.ceil(y0 * size[1] / 2) * 2
    right, bottom = math.floor(x1 * size[0] / 2) * 2, math.floor(y1 * size[1] / 2) * 2
    if (right - left) * (bottom - top) < 0.1 * size[0] * size[1]:
        return None
    return left, top, right - left, bottom - top


def moving_part(
    video: Path, start: float, end: float, size: tuple[int, int], picture: tuple[int, int, int, int]
) -> framing.Moving | None:
    """The source rows of `picture` that move, when part of it is a still image (a photo
    above a clip): the faces to frame are there, not in the photo. None when all of it
    moves."""
    small = (LAYOUT_WIDTH, round(LAYOUT_WIDTH * size[1] / size[0] / 2) * 2)
    frames = layout.sample(
        video, end - start, LAYOUT_FRAMES, small, get_settings().ffmpeg_bin, start=start
    )
    scale = small[1] / size[1]
    top, bottom = round(picture[1] * scale), round((picture[1] + picture[3]) * scale)
    found = layout.moving_rows(frames, top, bottom)
    if found is None:
        return None
    return framing.Moving(
        math.floor(found[0] / scale),
        min(math.ceil(found[1] / scale), size[1]),
        layout.plainer_side(frames, found, top, bottom),
    )


ACTION_FPS = 6  # frames a second the action is looked for in
ACTION_PAD = 0.06  # of the picture, added around the action so a falling arm isn't grazed


def action_subjects(
    video: Path,
    clip_start: float,
    clip_end: float,
    cuts: list[float],
    size: tuple[int, int],
    picture: tuple[int, int, int, int],
) -> list[tuple[float, float, Subject]]:
    """For each camera shot (start, end), where its action happens, as a must-keep box in
    source pixels: the framing then keeps the whole fail in the crop, not just a face."""
    small = (LAYOUT_WIDTH, round(LAYOUT_WIDTH * size[1] / size[0] / 2) * 2)
    scale = small[0] / size[0]
    px, py, pw, ph = picture
    left, top = round(px * scale), round(py * scale)
    right, bottom = round((px + pw) * scale), round((py + ph) * scale)
    found = []
    for start, end in framing.shot_bounds(Clip(start=clip_start, end=clip_end), cuts):
        count = max(2, round((end - start) * ACTION_FPS))
        frames = layout.sample(video, end - start, count, small, get_settings().ffmpeg_bin,
                               start=start)  # fmt: skip
        box = layout.action_box(frames[:, top:bottom, left:right])
        if box is None:
            continue
        x0, y0, x1, y1 = box
        pad_x, pad_y = ACTION_PAD * pw, ACTION_PAD * ph
        found.append((start, end, Subject(
            kind="other", label="action", keep=True,
            x0=max(px, math.floor((left + x0) / scale - pad_x)),
            y0=max(py, math.floor((top + y0) / scale - pad_y)),
            x1=min(px + pw, math.ceil((left + x1) / scale + pad_x)),
            y1=min(py + ph, math.ceil((top + y1) / scale + pad_y)),
        )))  # fmt: skip
    return found


def picture_area(
    video: Path, start: float, end: float, size: tuple[int, int], probes: int = 4
) -> tuple[int, int, int, int]:
    """The part of the frame that holds picture, as x, y, width, height.

    A frame padded with one colour, text on the padding or not, is read for where its
    picture moves. Otherwise bars are measured at a few points and the widest picture
    kept, so a dark scene isn't taken for a bar."""
    padded = padded_picture(video, start, end, size)
    if padded is not None:
        return padded
    found = []
    for i in range(probes):
        at = start + (end - start) * i / probes
        bars = detect_black_bars(video, at)
        if bars:
            w, h, x, y = bars
            found.append((x, y, x + w, y + h))
    if not found:
        return (0, 0, *size)
    x0, y0 = min(f[0] for f in found), min(f[1] for f in found)
    x1, y1 = max(f[2] for f in found), max(f[3] for f in found)
    return (x0, y0, (x1 - x0) // 2 * 2, (y1 - y0) // 2 * 2)


def baked_bars(
    video: Path, start: float, end: float, size: tuple[int, int]
) -> tuple[int, int, int, int] | None:
    """The crop, as width, height, x, y, that takes off the black bars baked into the
    source, or None when there are none. Measured like picture_area, at several points."""
    x, y, width, height = picture_area(video, start, end, size)
    if width <= 0 or height <= 0 or (width >= size[0] - 1 and height >= size[1] - 1):
        return None
    return width, height, x, y


def on_picture(
    marks: list[Watermark], picture: tuple[int, int, int, int] | None
) -> list[Watermark]:
    """The watermarks inside the picture that is kept, cut to its edges. Text on a repost's
    padding is found like any overlay, and so is the picture's own straight edge; cropping
    the picture out already removes the one, and a blur on the other only smudges the
    picture. A real mark sits on the picture."""
    if picture is None:
        return marks
    left, top, width, height = picture
    kept = []
    for mark in marks:
        x0, y0 = max(mark.x, left), max(mark.y, top)
        x1 = min(mark.x + mark.width, left + width)
        y1 = min(mark.y + mark.height, top + height)
        if x1 <= x0 or y1 <= y0 or (x1 - x0) * (y1 - y0) < 0.9 * mark.width * mark.height:
            continue
        x0, y0 = math.ceil(x0 / 2) * 2, math.ceil(y0 / 2) * 2
        kept.append(
            mark.model_copy(
                update={"x": x0, "y": y0, "width": (x1 - x0) // 2 * 2, "height": (y1 - y0) // 2 * 2}
            )
        )
    return kept


def text_face(settings: Settings, language: str | None) -> Callable[..., text.Face]:
    """Faces for text in this language: a bundled font, with a Noto font for any script
    the bundled one can't draw."""
    font_folder = settings.models_dir / "fonts"

    def face(name: str, *texts: str) -> text.Face:
        primary = text.font_file(settings.fonts_dir, name)
        return text.Face(fonts.chain(primary, texts, font_folder, language))

    return face


def text_language(preset: Preset, analysis: Analysis) -> str | None:
    translated = preset.captions.mode == "translated" and preset.captions.language
    return preset.captions.language if translated else analysis.transcript.language


def text_gaps(preset: Preset) -> list[int | None]:
    return [
        preset.framing.text_gap_above if b.anchor == "top" else preset.framing.text_gap_below
        for b in preset.text_blocks
    ]


def picture_band(preset: Preset, face: Callable[..., text.Face]) -> tuple[int, int] | None:
    """Rows of the frame a picture shown whole goes in: between the preset's text blocks,
    each left where the preset puts it with its gap to the picture. None when no block
    limits the picture."""
    frame = preset.size
    top, bottom = 0, frame[1]
    for block, gap in zip(preset.text_blocks, text_gaps(preset), strict=True):
        if gap is None:
            continue
        ink_top, ink_bottom = text.place(block, face(block.font, block.text), frame, None, gap).band
        if block.anchor == "top":
            top = max(top, math.ceil(ink_bottom + gap))
        else:
            bottom = min(bottom, math.floor(ink_top - gap))
    if (top, bottom) == (0, frame[1]) or bottom - top < frame[1] // 4:
        return None
    return math.ceil(top / 2) * 2, bottom // 2 * 2


def band_rows(preset: Preset, band: tuple[int, int], picture_width: int) -> int:
    """Source rows of a picture `picture_width` wide that fill `band` at the frame's width."""
    shown = preset.size[0] * preset.framing.picture_width
    return round((band[1] - band[0]) * picture_width / shown)


def timeline_words(edl: EDL, analysis: Analysis):
    words = []
    offset = 0.0
    for clip in edl.clips:
        for word in analysis.transcript.between(clip.start, clip.end):
            words.append(
                word.model_copy(
                    update={
                        "start": offset + max(word.start, clip.start) - clip.start,
                        "end": offset + min(word.end, clip.end) - clip.start,
                    }
                )
            )
        offset += clip.duration
    return words


def caption_cues(
    edl: EDL, analysis: Analysis, style: CaptionStyle, fits: Callable[[str], bool] | None = None
) -> list[Cue]:
    """The captions for this reel in the style's mode. Translated and written captions
    come from the models before rendering; without them, fall back to the speech."""
    words = timeline_words(edl, analysis)
    if style.mode in ("translated", "visual") and edl.cues:
        return edl.cues
    if style.mode in ("translated", "visual"):
        log.warning("no %s captions were made; showing the speech instead", style.mode)
    if style.mode == "lyrics":
        breaths = None
        if analysis.audio_source == "vocals" and analysis.silences:
            clip = edl.clips[0]
            breaths = [
                (max(a, clip.start) - clip.start, min(b, clip.end) - clip.start)
                for a, b in voiced_regions(analysis)
                if b > clip.start and a < clip.end
            ]
        return subtitles.lyric_cues(words, breaths=breaths, fits=fits)
    per_line = edl.caption_overrides.words_per_line or style.words_per_line
    return subtitles.speech_cues(words, per_line, fits)


def caption_scales(
    cues: list[Cue], measure: Callable[[str], float], room: float, pop: bool
) -> list[float]:
    """How much to shrink each caption so that none runs off the frame: one holding a
    word too long for the frame's width is drawn smaller rather than cut off."""
    scales = []
    for cue in cues:
        width = max(measure(line) for line in cue.text.split("\n"))
        if pop and cue.words:
            width += 0.12 * max(measure(w.text) for w in cue.words)  # the spoken word grows
        scales.append(min(1.0, room / max(1.0, width)))
    return scales


def keep_clear(
    obstacles: list[tuple[float, float, float, float]],
) -> list[tuple[float, list[tuple[float, float]]]]:
    """Rows captions must stay off, every half second, for overlays shown from start to
    end: each band widened by a banner's gap, so a caption never sits flush on a box."""
    return [
        (start + k * 0.5, [(top - BANNER_GAP, bottom + BANNER_GAP)])
        for start, end, top, bottom in obstacles
        for k in range(int((end - start) / 0.5) + 1)
    ]


def banner_top(
    position: str,
    offset: int,
    height: int,
    frame_height: int,
    rows: list[tuple[float, list[tuple[float, float]]]],
    until: float | None = None,
    taken: list[tuple[float, float]] = (),
) -> int:
    """Top edge for a banner: its usual position if faces stay clear of it, else the other
    end of the frame, else stacked beside a banner already there. `taken` are the bands of
    banners placed before this one, which it must not cover."""
    gap = BANNER_GAP
    near = offset if position == "top" else frame_height - offset - height
    far = frame_height - offset - height if position == "top" else offset
    options = [near, far]
    for top, bottom in taken:
        options += [round(bottom + gap), round(top - gap - height)]
    options = [o for o in options if 0 <= o <= frame_height - height]
    if not options:
        options = [min(max(near, 0), max(0, frame_height - height))]
    seen = [bands for t, bands in rows if until is None or t <= until]

    def cost(top: int) -> tuple[float, int]:
        band = (top, top + height)
        faces = sum(subtitles.overlap(band, bands) > 0 for bands in seen) / max(1, len(seen))
        blocked = subtitles.overlap(band, list(taken)) > 0
        # Covering another banner is never acceptable; a face only for a small share of time.
        return (blocked, faces > 0.3, faces), options.index(top)

    return min(options, key=cost)


def clear_rows(
    edl: EDL,
    analysis: Analysis,
    preset: Preset,
    content: tuple[int, int, int, int] | None,
    faces: list[Sample],
    texts: list[tuple[float, list[onscreen.Box]]] = (),
    band: tuple[int, int] | None = None,
) -> list[tuple[float, list[tuple[float, float]]]]:
    """The rows of the output frame to keep captions and banners off, at reel times:
    faces, and text burnt into the picture while it is on screen."""
    source = (analysis.width, analysis.height)
    picture = content or (0, 0, *source)
    shots = edl.shots
    window = crop_size(source, preset.size)[0]
    frame = preset.size
    if edl.crop_mode == "none":
        # A picture shown whole: its windows are drawn as wide as the picture is let be.
        frame = (round(frame[0] * preset.framing.picture_width), frame[1])
        window = picture[2]
    clip = edl.clips[0]
    return framing.screen_rows(
        faces, clip, shots, picture, source, frame, window, band
    ) + framing.box_rows(list(texts), clip, shots, picture, source, frame, window, band)


def render(
    edl: EDL,
    preset: Preset,
    analysis: Analysis,
    video: Path,
    dest: Path,
    work_dir: Path,
    settings: Settings,
    backends: Backends,
    crop_commands: dict[int, Path] | None = None,
    faces: list[Sample] | None = None,
    texts: list[tuple[float, list[onscreen.Box]]] | None = None,
) -> Path:
    started = time.monotonic()
    work_dir.mkdir(parents=True, exist_ok=True)
    frame = preset.size
    music = asset_path(preset.music.file) if preset.music.file else None
    soundtrack = audio.render_soundtrack(edl, preset, analysis.audio_path, music, work_dir)

    inputs: list[str] = []
    for clip in edl.clips:
        inputs += ["-ss", f"{clip.start:.3f}", "-t", f"{clip.duration:.3f}", "-i", str(video)]
    layers = Layers()
    if edl.crop_mode == "smart":
        clip = edl.clips[0]
        layers.content = edl.picture or picture_area(
            video, clip.start, clip.end, (analysis.width, analysis.height)
        )
        layers.shot_commands = crop_commands or {}
    else:
        layers.crop_commands = crop_commands or {}

    def add_input(path: Path) -> int:
        inputs.extend(["-i", str(path)])
        return inputs.count("-i") - 1

    source = (analysis.width, analysis.height)
    # Bars baked into the source would show against a background taken from the footage.
    crop_bars = preset.framing.crop_black_bars or preset.framing.auto_background
    if crop_bars and edl.crop_mode == "none":
        clip = edl.clips[0]
        layers.black_bars = baked_bars(video, clip.start, clip.end, source)
        if layers.black_bars:
            source = layers.black_bars[:2]
    if preset.watermarks.blur:
        bars = layers.black_bars
        picture = (bars[2], bars[3], bars[0], bars[1]) if bars else layers.content
        layers.watermarks = on_picture(analysis.watermarks, picture)

    # Text a bundled font can't draw gets a Noto font for its script.
    font_folder = settings.models_dir / "fonts"
    language = text_language(preset, analysis)
    face = text_face(settings, language)

    # Banners keep off faces, the picture's own text and the preset's text blocks; captions
    # keep off those and banners.
    obstacles: list[tuple[float, float, float, float]] = []  # reel start, end, top, bottom
    if edl.crop_mode == "none":
        # The text where the preset puts it, the picture between at the frame's width.
        layers.picture_band = picture_band(preset, face)
    if preset.text_blocks:
        shown_size = source
        if edl.crop_mode == "none" and edl.shots:
            shown_size = (edl.shots[0].width, max(s.height or source[1] for s in edl.shots))
        band = video_band(
            edl.crop_mode, shown_size, frame, preset.framing.picture_width, layers.picture_band
        )
        placed = [
            text.place(b, face(b.font, b.text), frame, band, gap)
            for b, gap in zip(preset.text_blocks, text_gaps(preset), strict=True)
        ]
        obstacles += [(0.0, edl.duration, *block.band) for block in placed]
    else:
        placed = []

    bars = layers.black_bars
    shown = (bars[2], bars[3], bars[0], bars[1]) if bars else layers.content
    rows = (
        clear_rows(edl, analysis, preset, shown, faces or [], texts or [], layers.picture_band)
        if faces or texts
        else []
    )

    colour = preset.grade
    if colour.auto or preset.framing.auto_background:
        clip = edl.clips[0]
        bars = layers.content or (
            (layers.black_bars[2], layers.black_bars[3], *layers.black_bars[:2])
            if layers.black_bars
            else None
        )
        measured = grade.measure(video, clip.start, clip.end, settings.ffmpeg_bin, bars)
        if colour.auto:
            layers.correction = grade.correction(measured)
        if preset.framing.auto_background:
            text_colours = [b.color for b in preset.text_blocks] or [preset.captions.color]
            dark = all(grade.is_dark(c) for c in text_colours)
            layers.background = grade.background(measured, dark)
    corners = None
    if edl.crop_mode == "none" and preset.framing.corner_radius:
        shown_size = source
        if edl.shots:
            shown_size = (edl.shots[0].width, max(s.height or source[1] for s in edl.shots))
        rect = picture_rect(shown_size, frame, preset.framing.picture_width, layers.picture_band)
        fill = preset.framing.corner_color or layers.background or preset.framing.background
        corners = (rect, preset.framing.corner_radius, fill)
    if placed or corners:
        layers.text_overlay = add_input(text.draw(placed, frame, work_dir / "text.png", corners))

    if colour.look != "none" or colour.lut:
        lut = asset_path(colour.lut) if colour.lut else None
        layers.look = grade.look_filters(colour.look, lut)
        layers.look_strength = colour.strength

    sync = preset.beat_sync
    if sync.pulse and analysis.beats.beats:
        clip = edl.clips[0]
        times = analysis.beats.downbeats if sync.pulse_on == "bar" else analysis.beats.beats
        layers.pulses = [t - clip.start for t in times if clip.start <= t < clip.end]
        layers.pulse_scale = sync.pulse_scale

    def banner(
        words: str, font_name: str, size: int, color: str, box: str | None,
        position: str, offset: int, until: float, name: str, fixed: bool = False,
    ) -> int:  # fmt: skip
        font = face(font_name, words)
        wrapped = text.balance(words, font, size, frame[0] - 160)
        # Placed by the rows it really covers, box included, with `offset` still where the
        # first line starts.
        above, below = text.banner_extent(wrapped, font, size, box, frame[0])
        height = math.ceil(below - above)
        start = max(0, round(offset + above)) if position == "top" else offset
        taken = [(top, bottom) for _, _, top, bottom in obstacles]
        top = banner_top(position, start, height, frame[1], [] if fixed else rows, until, taken)
        obstacles.append((0.0, until, top, top + height))
        png = text.draw_banner(
            wrapped, font, size, color, box, frame, round(top - above), work_dir / name
        )
        return add_input(png)

    title = preset.title_overlay
    if title.enabled and edl.title:
        layers.title_duration = min(title.duration, edl.duration)
        layers.title_overlay = banner(
            edl.title, title.font, title.size, title.color, title.box_color,
            "top", title.offset, layers.title_duration, "title.png",
        )  # fmt: skip

    if preset.hook.enabled and preset.hook.overlay and edl.hook:
        offset = title.offset + (round(title.size * 1.6) if layers.title_overlay is not None else 0)
        layers.hook_duration = min(3.0, edl.duration)
        layers.hook_overlay = banner(
            edl.hook, title.font, round(title.size * 0.8), title.color, title.box_color,
            "top", offset, layers.hook_duration, "hook.png",
        )  # fmt: skip

    line = preset.one_liner
    if line.enabled and edl.one_liner:
        layers.line_duration = line.duration or 0.0
        layers.line_overlay = banner(
            edl.one_liner, line.font, line.size, line.color, line.box_color,
            line.position, line.offset, line.duration or edl.duration, "line.png", line.fixed,
        )  # fmt: skip

    if preset.captions.enabled:
        style = preset.captions
        overrides = edl.caption_overrides
        font = text.font_file(settings.fonts_dir, style.font)
        said = [w.text for w in timeline_words(edl, analysis)] + [c.text for c in edl.cues]
        said = [line.upper() if style.uppercase else line for line in said]
        chain = fonts.chain(font, said, font_folder, language)
        measure = text.caption_measure(chain, style.size, style.uppercase)
        room = frame[0] - 120 - 2 * style.outline
        cues = caption_cues(edl, analysis, style, lambda line: measure(line) <= room)
        if any(not c.words for c in cues):
            cues = subtitles.fit_lines(cues, measure, room)
        scales = caption_scales(cues, measure, room, style.animation == "pop")
        position = overrides.position or style.position
        placements = None
        if style.avoid_faces and (rows or obstacles):
            blocked = rows + keep_clear(obstacles)
            lines = max((c.text.count("\n") + 1 for c in cues), default=1)
            height = round(style.size * 1.25 * lines + 2 * style.outline)
            placements = subtitles.place(cues, position, style.margin, height, frame[1], blocked)
        family = text.font_family(font)
        ass = captions.build_ass(
            cues,
            style,
            frame,
            family,
            position=position,
            highlight_color=overrides.highlight_color,
            placements=placements,
            scales=scales,
            font_chain=chain,
        )
        layers.captions = work_dir / "captions.ass"
        layers.captions.write_text(ass, encoding="utf-8")
        layers.fonts_dir = settings.fonts_dir
        if len(chain) > 1:
            layers.fonts_dir = fonts.gather(
                [*text.bundled_fonts(settings.fonts_dir), *chain[1:]], work_dir / "fonts"
            )
        if style.export_srt:
            dest.with_suffix(".srt").write_text(subtitles.to_srt(cues), encoding="utf-8")

    if preset.logo.file:
        layers.logo = add_input(asset_path(preset.logo.file))

    audio_index = add_input(soundtrack)
    graph, out = build_graph(edl, preset, source, layers)
    maps = [f"[{out}]", f"{audio_index}:a"]
    if preset.intro or preset.outro:
        # Joined in the same graph, so the reel is encoded once.
        before = [bookend(asset_path(preset.intro), add_input)] if preset.intro else []
        after = [bookend(asset_path(preset.outro), add_input)] if preset.outro else []
        parts, video_out, audio_out = bookend_graph(
            frame, preset.fps, before, after, out, audio_index
        )
        graph = ";".join([graph, *parts])
        maps = [f"[{video_out}]", f"[{audio_out}]"]
    ffmpeg.run([
        "-y", *inputs, "-filter_complex", graph,
        "-map", maps[0], "-map", maps[1],
        *encoder_args(backends, preset.bitrate_mbps),
        "-r", str(preset.fps),
        "-c:a", "aac", "-b:a", "256k", "-ar", str(audio.SAMPLE_RATE),
        "-movflags", "+faststart", str(dest),
    ])  # fmt: skip
    log.info(
        "rendered %s with %s in %.1fs", dest.name, backends.encoder_name, time.monotonic() - started
    )
    return dest


def bookend(path: Path, add_input: Callable[[Path], int]) -> tuple[int, ffmpeg.MediaInfo]:
    info = ffmpeg.probe(path)
    if not info.has_video:
        raise ValueError(f"{path.name} has no picture to use as an intro or outro")
    return add_input(path), info


def bookend_graph(
    frame: tuple[int, int],
    fps: int,
    before: list[tuple[int, ffmpeg.MediaInfo]],
    after: list[tuple[int, ffmpeg.MediaInfo]],
    video: str,
    audio_input: int,
) -> tuple[list[str], str, str]:
    """Graph parts that put intro clips (inputs with their probed info) before the reel
    and outro clips after it, each fitted to the frame. A clip without sound gets silence
    for its length, so the soundtrack after it stays in step."""
    width, height = frame
    rate = f"sample_rates={audio.SAMPLE_RATE}:channel_layouts=stereo"
    parts, labels = [], []

    def piece(k: int, index: int, info: ffmpeg.MediaInfo) -> None:
        parts.append(
            f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[bv{k}]"
        )
        if info.has_audio:
            parts.append(f"[{index}:a]aformat={rate}[ba{k}]")
        else:
            parts.append(
                f"anullsrc=r={audio.SAMPLE_RATE}:cl=stereo,"
                f"atrim=duration={max(info.duration, 0.04):.3f}[ba{k}]"
            )
        labels.append(f"[bv{k}][ba{k}]")

    for k, (index, info) in enumerate(before):
        piece(k, index, info)
    parts.append(f"[{audio_input}:a]aformat={rate}[reel_a]")
    labels.append(f"[{video}][reel_a]")
    for k, (index, info) in enumerate(after, start=len(before)):
        piece(k, index, info)
    parts.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=1[joined_v][joined_a]")
    return parts, "joined_v", "joined_a"
