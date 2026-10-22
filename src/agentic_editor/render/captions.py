"""Word-timed captions as an ASS subtitle file for libass."""

from agentic_editor.edl import Cue
from agentic_editor.presets import CaptionStyle
from agentic_editor.subtitles import Placement

_ALIGNMENT = {"bottom": 2, "middle": 5, "top": 8}


def ass_color(hex_color: str, alpha: int = 0) -> str:
    r, g, b = hex_color[1:3], hex_color[3:5], hex_color[5:7]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def ass_time(seconds: float) -> str:
    cs = max(0, round(seconds * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _clean(text: str, upper: bool) -> str:
    text = text.replace("{", "(").replace("}", ")").replace("\\", "")
    text = text.upper() if upper else text
    return text.replace("\n", "\\N")  # a line break fit_lines put in


def build_ass(
    cues: list[Cue],
    style: CaptionStyle,
    frame: tuple[int, int],
    font_family: str,
    *,
    position: str | None = None,
    highlight_color: str | None = None,
    placements: list[Placement] | None = None,
) -> str:
    """ASS events for the cues. Cues with word timings get the style's animation; whole
    lines (translated, written) fade in and out. `placements` moves single cues, e.g. off
    a face; otherwise every cue sits at the style's position."""
    width, height = frame
    position = position or style.position
    highlight = ass_color(highlight_color or style.highlight_color)
    base = ass_color(style.color)
    margin = style.margin if position != "middle" else 0

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {width}\nPlayResY: {height}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Caption,{font_family},{style.size},{base},{base},"
        f"{ass_color(style.outline_color)},{ass_color('#000000', 0x80)},0,0,0,0,100,100,0,0,"
        f"1,{style.outline:g},0,{_ALIGNMENT[position]},60,60,{margin},1\n\n"
        "[Events]\n"
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )

    events = []
    for k, cue in enumerate(cues):
        at = placements[k] if placements else None
        # An explicit alignment and margin only when a cue has to move.
        lead = f"{{\\an{at.alignment}}}" if at and at.alignment != _ALIGNMENT[position] else ""
        margin_v = at.margin if at else 0
        line = cue.words
        if not line:
            text = _clean(cue.text, style.uppercase)
            events.append((cue.start, cue.end, margin_v, f"{lead}{{\\fad(120,120)}}{text}"))
            continue
        texts = [_clean(w.text, style.uppercase) for w in line]
        line_end = line[-1].end
        if style.animation == "none":
            events.append((line[0].start, line_end, margin_v, lead + " ".join(texts)))
        elif style.animation == "karaoke":
            # \kf sweeps each word from SecondaryColour to PrimaryColour over its duration.
            starts = [w.start for w in line[1:]] + [line_end]
            tagged = " ".join(
                f"{{\\kf{max(1, round((nxt - w.start) * 100))}}}{t}"
                for w, nxt, t in zip(line, starts, texts, strict=True)
            )
            colours = f"{{\\1c{highlight}\\2c{base}}}"
            events.append((line[0].start, line_end, margin_v, f"{lead}{colours}{tagged}"))
        else:
            pop = r"\fscx112\fscy112" if style.animation == "pop" else ""
            for i, word in enumerate(line):
                end = line[i + 1].start if i + 1 < len(line) else line_end
                parts = [
                    f"{{\\1c{highlight}{pop}}}{t}{{\\r}}" if j == i else t
                    for j, t in enumerate(texts)
                ]
                events.append((word.start, end, margin_v, lead + " ".join(parts)))

    body = "".join(
        f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,{margin_v},,{text}\n"
        for start, end, margin_v, text in events
        if end > start
    )
    return header + body
