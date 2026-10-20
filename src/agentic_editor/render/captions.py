"""Word-timed captions as an ASS subtitle file for libass."""

from agentic_editor.presets import CaptionStyle
from agentic_editor.transcript import Word

LINE_BREAK_PAUSE = 0.6
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


def group_lines(words: list[Word], per_line: int) -> list[list[Word]]:
    lines: list[list[Word]] = []
    for word in words:
        current = lines[-1] if lines else None
        if current and len(current) < per_line and word.start - current[-1].end < LINE_BREAK_PAUSE:
            current.append(word)
        else:
            lines.append([word])
    return lines


def _clean(text: str, upper: bool) -> str:
    text = text.replace("{", "(").replace("}", ")").replace("\\", "")
    return text.upper() if upper else text


def build_ass(
    words: list[Word],
    style: CaptionStyle,
    frame: tuple[int, int],
    font_family: str,
    *,
    position: str | None = None,
    words_per_line: int | None = None,
    highlight_color: str | None = None,
) -> str:
    width, height = frame
    position = position or style.position
    per_line = words_per_line or style.words_per_line
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
    for line in group_lines(words, per_line):
        texts = [_clean(w.text, style.uppercase) for w in line]
        line_end = line[-1].end
        if style.animation == "none":
            events.append((line[0].start, line_end, " ".join(texts)))
        elif style.animation == "karaoke":
            # \kf sweeps each word from SecondaryColour to PrimaryColour over its duration.
            starts = [w.start for w in line[1:]] + [line_end]
            tagged = " ".join(
                f"{{\\kf{max(1, round((nxt - w.start) * 100))}}}{t}"
                for w, nxt, t in zip(line, starts, texts, strict=True)
            )
            events.append((line[0].start, line_end, f"{{\\1c{highlight}\\2c{base}}}{tagged}"))
        else:
            pop = r"\fscx112\fscy112" if style.animation == "pop" else ""
            for i, word in enumerate(line):
                end = line[i + 1].start if i + 1 < len(line) else line_end
                parts = [
                    f"{{\\1c{highlight}{pop}}}{t}{{\\r}}" if j == i else t
                    for j, t in enumerate(texts)
                ]
                events.append((word.start, end, " ".join(parts)))

    body = "".join(
        f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{text}\n"
        for start, end, text in events
        if end > start
    )
    return header + body
