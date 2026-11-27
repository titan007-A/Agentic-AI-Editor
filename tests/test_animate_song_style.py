"""How a Song story's lyric is set, as the owner's reference sets it: a small block of two
rows, key words in a bold italic serif and the rest light, each word coming in from a soft
blur on its time and the whole line blurring away before the next; and now and then a word
meaning a movement whose letters act it out. Every lyric here is made up."""

import asyncio
import io
import re
import shutil

import numpy as np
import pytest
from PIL import Image

from agentic_editor.animate import render, routes, songwriter
from agentic_editor.animate.plan import Plan, Scene, Song, SongLine, SongWord, Visual, tidy
from agentic_editor.animate.timeline import (
    EFFECT_AFTER,
    EFFECT_LONGEST,
    EFFECT_SHORTEST,
    LINE_OUT,
    build_song,
    effect_time,
    line_out,
    player_data,
    second_row,
)
from agentic_editor.settings import ROOT
from agentic_editor.transcript import Word
from tests.test_animate_song import Writer


def _line(text: str, start: float, step: float = 0.4) -> SongLine:
    words = [
        SongWord(text=w, start=round(start + k * step, 3), end=round(start + (k + 1) * step, 3))
        for k, w in enumerate(text.split())
    ]
    return SongLine(text=text, said=text, start=start, end=words[-1].end, words=words)


LINES = [
    "We fall like rain on a paper town",
    "Hold the light for me",
    "The lanterns rise above the bay",
    "My hands still shake when you call",
    "And every window breaks apart",
    "Spin me up",
    "Tomorrow waits outside in muddy boots",
    "Your coat still hangs behind the door",
]


def english_song() -> Song:
    lines = [_line(text, 10.0 + 3.2 * k) for k, text in enumerate(LINES)]
    return Song(query="Paper Town", title="Paper Town", artist="Nobody", video="own",
                start=9.8, end=36.0, language="en", lyrics="lrclib", lines=lines)  # fmt: skip


def key(word: int, color: str = "white", effect: str | None = None) -> dict:
    return {"word": word, "color": color} | ({"effect": effect} if effect is not None else {})


def reply_for(song: Song, keys: dict[int, list[dict]], splits: dict[int, int]) -> dict:
    return {
        "lines": [
            {"n": n, "words": line.text.split(), "key": keys.get(n, []), "split": splits.get(n, 0)}
            for n, line in enumerate(song.lines, 1)
        ]
    }


ENGLISH_REPLY = reply_for(
    english_song(),
    {
        1: [key(1, "white", "fall"), key(7, "amber", "none")],
        2: [key(2, "amber", "rise")],  # right after a line that moves: not this one
        3: [key(1, "white"), key(2, "amber", "rise")],
        4: [key(1, "amber"), key(3, "white", "shake")],  # one line in four at most
        5: [key(2, "white", "scatter"), key(3, "red", "scatter")],
        6: [key(0, "white", "spin")],
        7: [key(0, "amber", "explode")],
        8: [key(1, "amber"), key(4, "white")],
    },
    {1: 4, 2: 3, 3: 2, 8: 4},
)


def test_the_writer_marks_a_movement_word_now_and_then_and_where_each_line_breaks():
    shown, notes = songwriter.shown(Writer(ENGLISH_REPLY), english_song())
    assert notes == []
    moving = [(n, w.text, w.effect) for n, line in enumerate(shown.lines, 1)
              for w in line.words if w.effect]  # fmt: skip
    # Kept: fall (line 1) and rise (line 3); line 2 comes right after a line that moved, 8
    # lines allow two, "explode" isn't an effect and "none" is none.
    assert moving == [(1, "fall", "fall"), (3, "rise", "rise")]
    town = shown.lines[0].words[7]
    assert (town.key, town.color, town.effect) == (True, "amber", "")
    # The writer's break is where a line's rows break; none given: set by width later.
    assert [line.row for line in shown.lines] == [4, 3, 2, 0, 0, 0, 0, 4]
    # A writer that gives one effect in each of many far-apart lines still gets only its
    # share: one line in four.
    many = english_song().model_copy(update={"lines": english_song().lines * 2})
    every = reply_for(many, {n: [key(1, "white", "shake")] for n in range(1, 17, 2)}, {})
    again, _ = songwriter.shown(Writer(every), many)
    assert [n for n, line in enumerate(again.lines, 1) if any(w.effect for w in line.words)] == [
        1, 3, 5, 7,
    ]  # fmt: skip


def test_a_spanish_line_keeps_its_accents_and_its_effect():
    song = Song(title="Ventana", artist="Nadie", start=0.0, end=8.0, language="es",
                lyrics="lrclib", lines=[_line("Mis manos tiemblan cuando llamas", 0.5),
                                        _line("La noche me encuentra sola", 4.0)])  # fmt: skip
    reply = reply_for(song, {1: [key(1, "amber"), key(2, "white", "shake")],
                             2: [key(1, "blue"), key(4, "blue")]}, {1: 3})  # fmt: skip
    shown, _ = songwriter.shown(Writer(reply), song)
    assert [(w.text, w.effect) for w in shown.lines[0].words if w.key] == [
        ("manos", ""), ("tiemblan", "shake"),
    ]  # fmt: skip
    assert shown.lines[0].row == 3 and shown.lines[1].row == 0
    # The prompt asks for effects and for where every line of three words or more breaks.
    asked = songwriter.script_prompt(song)[0]["content"]
    assert "effect" in asked and "two rows" in asked and "fall" in asked


def test_a_long_line_shown_as_two_lines_is_set_by_width_and_keeps_its_effect():
    long = "I walked the empty roads all night and still I could not find you"
    song = Song(title="x", language="en", start=0.0, end=9.0,
                lines=[_line(long, 0.5, 0.45)])  # fmt: skip
    reply = reply_for(song, {1: [key(1, "white", "fall"), key(4, "amber")]}, {1: 7})
    shown, _ = songwriter.shown(Writer(reply), song)
    assert [line.text for line in shown.lines] == [
        "I walked the empty roads all night", "and still I could not find you",
    ]  # fmt: skip
    assert [line.row for line in shown.lines] == [0, 0]
    assert shown.lines[0].words[1].effect == "fall"


def test_effects_are_only_the_known_ones_on_key_words_one_a_line():
    line = _line("We fall and rise and spin", 1.0)
    marks = [("", False), ("fall", True), ("rise", False), ("rise", True), ("", False),
             ("explode", True)]  # fmt: skip
    words = [w.model_copy(update={"effect": e, "key": k, "color": "white" if k else ""})
             for w, (e, k) in zip(line.words, marks, strict=True)]  # fmt: skip
    song = Song(title="x", start=0.0, end=6.0,
                lines=[line.model_copy(update={"words": words, "row": 9})])  # fmt: skip
    scene = Scene(narration=line.text, visual=Visual(kind="moment", art="a"))
    plan = tidy(Plan(title="x", style="song", song=song, scenes=[scene]))
    first = plan.song.lines[0]
    assert [w.effect for w in first.words] == ["", "fall", "", "", "", ""]
    assert first.row == 0  # past its words: set by width


def test_a_lines_rows_break_where_the_writer_said_else_nearest_the_same_width():
    def words(text: str, keys: dict[int, str]) -> list[dict]:
        return [{"text": w, "key": k in keys, "color": keys.get(k, "")}
                for k, w in enumerate(text.split())]  # fmt: skip

    assert second_row(words("Tonight", {0: "amber"})) == 0
    assert second_row(words("Spin me", {0: "white"})) == 0  # two words: one row
    drawer = words("I keep your letters in a drawer", {3: "white", 6: "amber"})
    assert second_row(drawer) == 4  # "I keep your letters / in a drawer"
    assert second_row(drawer, told=2) == 2
    assert second_row(drawer, told=7) == 4  # past its words: by width
    sola = words("La noche me encuentra sola", {1: "blue", 4: "blue"})
    assert second_row(sola) == 3  # "La noche me / encuentra sola"
    # A big coloured key word weighs as much as the small words around it.
    light = words("Hold the light for me", {2: "amber"})
    assert second_row(light) == 2


def song_plan(song: Song, reply: dict) -> Plan:
    shown, _ = songwriter.shown(Writer(reply), song)
    return tidy(Plan(title="Paper Town", style="song", song=shown, scenes=[
        Scene(narration=line.text, hold=0, visual=Visual(kind="moment", art="a"))
        for line in shown.lines
    ]))  # fmt: skip


def test_each_word_comes_on_its_time_its_letters_move_and_the_line_blurs_away_before_the_next():
    plan = song_plan(english_song(), ENGLISH_REPLY)
    data = player_data(plan, build_song(plan, ""))
    first, second, third = data["captions"][:3]
    # Rows where the writer broke them (lines 1-3, 8), else nearest the same width.
    assert [c["row"] for c in data["captions"]] == [4, 3, 2, 3, 3, 1, 2, 4]
    # A line blurs away over its last LINE_OUT; one sung to its end, in what is left once
    # its last word has come.
    assert second["out"] == pytest.approx(second["end"] - LINE_OUT)
    assert first["out"] == pytest.approx(first["words"][-1]["start"] + 0.2)
    fall = first["words"][1]
    assert fall["effect"] == "fall" and fall["key"] and fall["color"] == "white"
    assert fall["effectFrom"] == pytest.approx(fall["start"] + EFFECT_AFTER)
    # Over the word's sung time, never shorter than EFFECT_SHORTEST.
    assert fall["effectTo"] - fall["effectFrom"] == pytest.approx(EFFECT_SHORTEST)
    assert all(w["effect"] == "" and w["effectFrom"] is None for w in second["words"])
    rise = third["words"][2]
    # Rising ends back in place before its line goes.
    assert rise["effect"] == "rise" and rise["effectTo"] <= third["out"]
    assert {k for w in first["words"] for k in w} >= {"text", "start", "end", "key", "color"}


def test_an_effect_takes_the_words_sung_time_and_is_dropped_without_room_for_it():
    w = Word(text="falling", start=2.0, end=3.5)
    assert effect_time("fall", w, out=9.0, end=9.3) == (2.3, 3.8)
    long = Word(text="falling", start=2.0, end=6.0)
    assert effect_time("fall", long, out=9.0, end=9.3) == (2.3, 2.3 + EFFECT_LONGEST)
    # Settling effects finish before the line blurs away; leaving ones may run into it.
    assert effect_time("shake", w, out=3.4, end=3.8) == (2.3, 3.4)
    assert effect_time("scatter", w, out=3.4, end=3.8) == (2.3, 3.8)
    # Sung at the very end of its line: no time to move, it only comes in.
    assert effect_time("spin", w, out=2.7, end=3.0) is None
    assert effect_time("", w, out=9.0, end=9.3) is None
    # A line whose last word comes late blurs away in what time is left, never under 0.12 s.
    late = [Word(text="a", start=1.0, end=1.3), Word(text="b", start=4.75, end=4.95)]
    assert line_out(1.0, 5.0, late) == pytest.approx(4.88)
    assert line_out(1.0, 5.0, late[:1]) == pytest.approx(5.0 - LINE_OUT)


def test_every_font_the_player_loads_is_bundled_with_its_licence_and_served():
    js = (render.PLAYER / "player.js").read_text()
    listed = js[js.index("const FONTS = [") : js.index("];", js.index("const FONTS = ["))]
    names = re.findall(r'"([A-Za-z0-9]+-[A-Za-z]+)"', listed)
    assert {"PlayfairDisplay-BoldItalic", "Inter-Light"} <= set(names)
    for name in names:
        assert name in routes.FONT_NAMES and (ROOT / "fonts" / f"{name}.ttf").is_file(), name
        family = name.split("-")[0]
        assert (ROOT / "fonts" / f"OFL-{family}.txt").is_file(), family


# ---- drawn ----------------------------------------------------------------------------------


def _browser() -> bool:
    from tests.test_animate_render import _browser_works

    return _browser_works()


needs_browser = pytest.mark.skipif(
    shutil.which("ffmpeg") is None or not _browser(), reason="needs Chromium and ffmpeg"
)


def shots(data: dict, opts: dict, times: list[float]) -> tuple[list[np.ndarray], list[str]]:
    """The player's frames at `times`, from one page (as the renderer seeks)."""
    from playwright.async_api import async_playwright

    async def go() -> tuple[list[bytes], list[str]]:
        async with async_playwright() as p:
            browser = await p.chromium.launch(executable_path=render.chromium(),
                                              args=["--disable-lcd-text"])  # fmt: skip
            page = await browser.new_page(viewport={"width": 1080, "height": 1920})
            await page.goto((render.PLAYER / "player.html").as_uri())
            info = await page.evaluate("([d, o]) => window.Player.load(d, o)", [data, opts])
            out = []
            for t in times:
                await page.evaluate("t => window.Player.seek(t)", t)
                out.append(await page.screenshot(type="png"))
            await browser.close()
            return out, info.get("problems", [])

    pngs, problems = asyncio.run(go())
    frames = [np.asarray(Image.open(io.BytesIO(b)).convert("RGB"), dtype=float) for b in pngs]
    return frames, problems


def lit(frame: np.ndarray, floor: float = 150.0) -> np.ndarray:
    """Where text is drawn: bright over the night (there is no painted picture here)."""
    return frame.max(axis=2) > floor


def drawn_song(lines: list[tuple[str, float, dict[int, str], dict[int, str], int]]) -> dict:
    """Player data for made-up lines: (text, first word's time, key colours, effects, row)."""
    out = []
    for text, start, colours, effects, row in lines:
        line = _line(text, start, 0.45)
        words = [w.model_copy(update={"key": k in colours, "color": colours.get(k, ""),
                                      "effect": effects.get(k, "")})
                 for k, w in enumerate(line.words)]  # fmt: skip
        out.append(line.model_copy(update={"words": words, "row": row}))
    end = out[-1].words[-1].end + 1.5
    song = Song(title="Paper Town", start=0.0, end=end, language="en", lines=out)
    plan = tidy(Plan(title="Paper Town", style="song", song=song, scenes=[
        Scene(narration=line.text, hold=0, visual=Visual(kind="moment", art="a"))
        for line in out
    ]))  # fmt: skip
    return player_data(plan, build_song(plan, ""))


@needs_browser
def test_the_line_is_two_rows_its_words_unblur_on_their_time_and_it_blurs_away(tmp_path):
    data = drawn_song([
        ("We kept the lantern burning", 0.5, {3: "white", 4: "amber"}, {}, 3),
        ("Tonight", 3.6, {0: "amber"}, {}, 0),
    ])  # fmt: skip
    first, single = data["captions"]
    burning = first["words"][4]
    opts = render.options(ROOT / "fonts", ROOT / "models")
    times = [
        first["out"] - 0.05,
        burning["start"] + 0.02,
        single["words"][0]["start"],
        single["words"][0]["start"] + 0.7,
        first["end"] - 0.04,
    ]
    (whole, coming, blurred, sharp, going), problems = shots(data, opts, times)
    assert problems == []
    rows = np.flatnonzero(lit(whole).any(axis=1))
    assert rows.size and rows.min() > 220, "the lyric sits below Instagram's top bar"
    # Two rows: a gap of dark between them.
    gaps = np.flatnonzero(np.diff(rows) > 8)
    assert len(gaps) == 1, "the line isn't set as two rows"
    top = lit(whole)[rows.min() : rows[gaps[0]] + 1]
    bottom = lit(whole)[rows[gaps[0] + 1] : rows.max() + 1]
    centre = lambda part: np.flatnonzero(part.any(axis=0)).mean()  # noqa: E731
    # The second row (the amber word) a little right of the first, both round the axis.
    assert 20 < centre(bottom) - centre(top) < 110
    assert 400 < centre(top) < 600
    amber = (whole[..., 0] > 200) & (whole[..., 1] > 150) & (whole[..., 2] < 140)
    assert np.flatnonzero(amber.any(axis=1)).min() > rows[gaps[0]], "amber sits in row two"

    # A word comes in from a soft blur, not moving: blurred and faint at first, sharp later.
    def edge(frame: np.ndarray) -> float:
        g = frame.mean(axis=2)[250:800]
        return float(np.abs(np.diff(g, axis=1)).max())

    assert edge(blurred) < 0.6 * edge(sharp)
    assert lit(coming).sum() < lit(whole).sum()
    # The whole line blurs and fades away together just before the next.
    assert lit(going).sum() < 0.4 * lit(whole).sum()


@needs_browser
def test_a_falling_word_tumbles_out_of_its_line_and_the_video_is_the_preview(tmp_path):
    from tests.test_animate_render import frame_at

    data = drawn_song([
        ("Then we all fall", 0.4, {3: "white"}, {3: "fall"}, 3),
        ("Spin me round tonight", 4.4, {0: "white", 3: "amber"}, {0: "spin"}, 0),
    ])  # fmt: skip
    first = data["captions"][0]
    fall = first["words"][3]
    assert fall["effect"] == "fall"
    opts = render.options(ROOT / "fonts", ROOT / "models")
    mid = (fall["effectFrom"] + fall["effectTo"]) / 2
    times = [fall["effectFrom"] - 0.02, mid, fall["effectTo"] + 0.05]
    (before, during, after), problems = shots(data, opts, times)
    assert problems == []
    band = np.flatnonzero(lit(before).any(axis=1))
    low = band.max()
    # Halfway, letters are on their way down below the line; after, they are gone from it.
    assert lit(during)[low + 40 : 1500].sum() > 30, "no letters falling below the line"
    assert lit(during)[1500:].sum() == 0, "letters fell into Instagram's bottom area"
    assert lit(after)[band.min() : low + 1].sum() < 0.8 * lit(before)[band.min() : low + 1].sum()
    assert lit(after)[low + 40 :].sum() == 0
    # Seeded: the render draws the very frame the preview shows.
    data["duration"] = min(data["duration"], 3.6)
    video = tmp_path / "picture.mp4"
    assert render.frames_to_video(data, opts, video, fps=10) == []
    at = round(mid * 10) / 10
    (shown,), _ = shots(data, opts, [at])
    made = np.asarray(frame_at(video, at, tmp_path / "made.png"), dtype=float)
    diff = np.abs(shown - made).mean()
    assert diff < 4.0, f"the video differs from the preview by {diff:.1f}"


@needs_browser
def test_a_long_line_wraps_inside_the_safe_area(tmp_path):

    long = "I walked the empty boulevards all night remembering everything"
    data = drawn_song([(long, 0.3, {3: "white", 4: "blue", 7: "amber"}, {}, 0)])
    (c,) = data["captions"]
    opts = render.options(ROOT / "fonts", ROOT / "models")
    (whole,), problems = shots(data, opts, [c["words"][-1]["start"] + 0.5])
    assert problems == []
    text = lit(whole, 120)
    rows = np.flatnonzero(text.any(axis=1))
    cols = np.flatnonzero(text.any(axis=0))
    # Inside the frame's safe area: below the top bar, left of the buttons, well above the
    # caption at the bottom, and wrapped onto more than two lines rather than off the side.
    assert rows.min() > 220 and rows.max() < 800
    assert cols.min() > 40 and cols.max() < 1080 - 150
    lines = 1 + int((np.diff(rows) > 8).sum())
    assert lines >= 3
