from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from agentic_editor import style
from agentic_editor.analysis import layout
from agentic_editor.style import Measured, Sighting
from agentic_editor.transcript import Transcript, Word

FONTS = Path(__file__).resolve().parents[1] / "fonts"
ANTON = FONTS / "Anton-Regular.ttf"
BOLD = FONTS / "OpenSans-ExtraBold.ttf"
REGULAR = FONTS / "OpenSans-Regular.ttf"
FRAME = (1080, 1920)


def drawn(lines: list[str], font: Path, size: int, top: int, back="black", **extra):
    """A 1080x1920 frame with the lines drawn in white, centred, from `top`."""
    image = Image.new("RGB", FRAME, back)
    canvas = ImageDraw.Draw(image)
    face = ImageFont.truetype(str(font), size)
    ascent, descent = face.getmetrics()
    for i, line in enumerate(lines):
        x = (FRAME[0] - face.getlength(line)) / 2
        canvas.text((x, top + i * (ascent + descent)), line, font=face, fill="white", **extra)
    return np.asarray(image)


def measured_from(frame: np.ndarray, lines: list[str]) -> Measured:
    mask = layout.ink(frame, (255, 255, 255))
    boxes = layout.lines(mask)
    fill = float(np.mean([mask[t:b, a:z].mean() for t, b, a, z in boxes]))
    return Measured(lines, boxes, (255, 255, 255), fill)


def sighting(text: str, at: float, y0: float, y1: float, x0=0.2, x1=0.8, colour="white"):
    return Sighting(at, text, (x0, y0, x1, y1), colour)


def test_reels_get_the_nearest_standard_format():
    assert style.target_size(720, 1280) == ("9:16", (1080, 1920))
    assert style.target_size(1080, 1350) == ("4:5", (1080, 1350))
    assert style.target_size(1000, 1000) == ("1:1", (1080, 1080))
    assert style.target_size(1080, 1500) == ("1080:1500", (1080, 1500))


def test_the_opening_seconds_get_frames_of_their_own():
    times = style.read_times(30)
    assert times[:2] == [0.5, 2.5] and len(times) == 6 and times[-1] < 30
    assert all(t > style.OPENING for t in times[2:])
    assert len(style.read_times(6)) == 4


def test_frames_are_read_while_words_are_on_screen():
    sung = [Word(text="you", start=11.0, end=11.4), Word(text="shadow", start=40.0, end=41.0)]
    times = style.read_times(60, sung)
    assert 11.2 in times and 40.5 in times
    assert len(times) == 6


def test_boxes_on_the_reading_square_map_back_onto_the_frame():
    # A 9:16 frame fills the middle 56.25% of the square's width.
    x0, y0, x1, y1 = style.from_square([500, 250, 781, 500], (1080, 1920))
    assert abs(x0 - 0.5) < 1e-6 and abs(x1 - 1.0) < 1e-3 and (y0, y1) == (0.25, 0.5)
    assert style.from_square([0, 0, 100, 100], (1080, 1920))[0] == 0.0  # off the frame
    _, y0, _, y1 = style.from_square([0, 500, 1000, 600], (1920, 1080))
    assert abs(y0 - 0.5) < 1e-6 and abs(y1 - (0.6 - 0.21875) / 0.5625) < 1e-6


def test_symbols_the_fonts_cannot_draw_are_dropped():
    assert style.clean("🎵 La Mejor Música\n  Día 28 ") == "La Mejor Música\nDía 28"


def test_lines_read_as_separate_entries_become_one_block():
    items = [
        sighting("dell'artista, senza strumenti", 1, 0.085, 0.145),
        sighting("Qui pubblichiamo la voce", 1, 0.015, 0.085),
        sighting("COMMISSION", 1, 0.84, 0.86, colour="yellow"),
    ]
    merged = style.join_lines(items)
    assert [m.text for m in merged] == [
        "Qui pubblichiamo la voce\ndell'artista, senza strumenti",
        "COMMISSION",
    ]
    assert merged[0].box[1] == 0.015 and merged[0].box[3] == 0.145


def test_each_text_on_the_reel_gets_its_part():
    times = style.read_times(30)
    picture = (0.1, 0.3, 0.9, 0.7)
    seen = [sighting("Follow for more raw vocals", t, 0.2, 0.24) for t in times]
    lyrics = ["and you", "to listen", "to me whine", "whenever", "x"]
    seen += [sighting(words, t, 0.75, 0.78) for t, words in zip(times[1:], lyrics, strict=True)]
    seen += [sighting("Day 28 of the series", t, 0.05 + i * 0.04, 0.08 + i * 0.04)
             for i, t in enumerate(times[:2])]  # fmt: skip
    seen += [sighting("@raw.vocals", t, 0.31, 0.325, 0.7, 0.88) for t in times]
    seen += [sighting("Michael Kende", t, 0.5, 0.53) for t in times]
    found = {g[0].text: role for role, g in style.roles(style.group(seen), times, picture, True)}
    assert found == {
        "Day 28 of the series": "title",
        "Follow for more raw vocals": "fixed",
        "@raw.vocals": "handle",
        "Michael Kende": "source",  # inside a letterboxed picture: the video's own text
        "and you": "captions",
    }
    full = {g[0].text: r for r, g in style.roles(style.group(seen), times, picture, False)}
    assert full["Michael Kende"] == "fixed"


def test_sparse_lyrics_still_count_as_captions():
    times = style.read_times(54)
    seen = [
        sighting("YOU ARE THE SHADOW", 0.5, 0.70, 0.73),
        sighting("You are the shadow", 0.5, 0.10, 0.13),
        sighting("You are the shadow", 2.5, 0.15, 0.18),  # the same title, boxed lower
        sighting("THE ONE THAT WON'T", 35.8, 0.71, 0.74),
    ]
    found = [(role, [s.at for s in g]) for role, g in style.roles(style.group(seen), times,
                                                                  (0, 0, 1, 1), False)]  # fmt: skip
    assert sorted(found) == [("captions", [0.5, 35.8]), ("title", [0.5, 2.5])]


def test_the_typical_reading_is_the_one_most_agree_on():
    assert style._typical([636, 560, 562, 700]) == 561  # two captions moved off a face
    assert style._typical([113, 96, 93, 259]) == 94.5
    assert style._typical([40]) == 40


def test_one_bad_reading_does_not_decide_the_font():
    lines = ["Follow us for more iconic raw vocals"]
    good = measured_from(drawn(lines, BOLD, 38, 1450), lines)
    squashed = Measured(lines, [(1452, 1500, 189, 893)], (255, 255, 255), 0.3)
    m, fit = style.consensus([good, squashed, good], [ANTON, BOLD, REGULAR])
    assert fit.font == BOLD and abs(fit.size - 38) <= 1 and m is good


def test_words_are_rebroken_to_match_the_lines_measured():
    lines = style.split_like(["Si amas las canciones antes del 2000"], [500, 380], BOLD)
    assert lines == ["Si amas las canciones", "antes del 2000"]
    assert style.split_like(["one two"], [100, 100, 100], BOLD) is None


def test_the_font_and_size_are_found_from_the_drawn_text():
    lines = ["Follow us for more", "iconic raw vocals"]
    fit = style.fit_font(measured_from(drawn(lines, BOLD, 58, 300), lines), [ANTON, BOLD, REGULAR])
    assert fit.font == BOLD and abs(fit.size - 58) <= 2 and abs(fit.spacing - 1.0) < 0.06
    fit = style.fit_font(measured_from(drawn(lines, REGULAR, 58, 300), lines), [BOLD, REGULAR])
    assert fit.font == REGULAR and abs(fit.size - 58) <= 2
    words = ["THE BIG INTERNET"]
    fit = style.fit_font(measured_from(drawn(words, ANTON, 90, 1400), words), [ANTON, BOLD])
    assert fit.font == ANTON and abs(fit.size - 90) <= 2


def test_a_loose_box_still_finds_the_text_and_its_outline():
    lines = ["Escucha la voz real"]
    frame = drawn(lines, BOLD, 60, 1500, back=(90, 90, 90), stroke_width=5, stroke_fill="black")
    loose = sighting(lines[0], 1, 0.80, 0.83)  # the text is really at 0.78-0.81
    m = style.measure(frame, loose)
    assert m is not None and m.colour == (255, 255, 255)
    assert len(m.boxes) == 1 and abs(m.boxes[0][0] - 1515) < 12
    colour, width = m.outline
    assert colour == (0, 0, 0) and 3 <= width <= 6


def test_a_highlighted_word_is_not_taken_for_the_caption_colour():
    image = Image.new("RGB", FRAME, "black")
    canvas = ImageDraw.Draw(image)
    face = ImageFont.truetype(str(ANTON), 80)
    x = (FRAME[0] - face.getlength("YOU ARE THE SHADOW")) / 2
    canvas.text((x, 1640), "YOU", font=face, fill=(255, 224, 0))
    canvas.text((x + face.getlength("YOU "), 1640), "ARE THE SHADOW", font=face, fill="white")
    seen = Sighting(1, "YOU ARE THE SHADOW", (0.22, 0.86, 0.78, 0.89), "yellow", "white")
    m = style.measure(np.asarray(image), seen)
    assert m.colour == (255, 255, 255) and m.other == (255, 224, 0)
    assert m.boxes[0][2] < x + 5 and m.boxes[0][3] > FRAME[0] - x - 5  # all of it


def test_the_picture_is_ignored_when_looking_for_text_on_the_padding():
    lines = ["Si amas las canciones"]
    frame = drawn(lines, BOLD, 52, 380).copy()
    frame[540:1380, 140:940] = 255  # a bright picture just below the tagline
    m = style.measure(frame, sighting(lines[0], 1, 0.20, 0.26), picture=(0.13, 0.28, 0.87, 0.72))
    assert len(m.boxes) == 1 and m.bottom < 540


def test_captions_are_speech_lyrics_translated_or_written():
    said = ["well", "I'm", "an", "economist", "and", "in", "the", "late", "90s"]
    heard = Transcript(words=[Word(text=t, start=i, end=i + 0.5) for i, t in enumerate(said)])
    assert style.caption_mode(["WELL, I'M AN", "ECONOMIST"], heard, False, "English") == (
        "speech",
        None,
    )
    assert style.caption_mode(["and in the"], heard, True, "English")[0] == "lyrics"
    assert style.caption_mode(["Bueno, soy economista"], heard, False, "Spanish") == (
        "translated",
        "Spanish",
    )
    assert style.caption_mode(["A quiet street"], Transcript(), False, "English")[0] == "visual"
