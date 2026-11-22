import json
import shutil
import subprocess

import numpy as np
import pytest
from PIL import Image, ImageDraw

from agentic_editor import download, pipeline, retext
from agentic_editor.analysis import ocr
from agentic_editor.analysis.ocr import Line
from agentic_editor.presets import Preset
from agentic_editor.render import compose, text
from agentic_editor.settings import get_settings

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
needs_ocr = pytest.mark.skipif(not ocr.available(), reason="needs the ml extras (OCR)")
SIZE = (720, 1280)
WORDS = ["N3on got EMOTIONAL when Jason who", "SACRIFICED himself for Lucia met his",
         "SON again for the first time in years"]  # fmt: skip


def font(size=26):
    return text.font_at(text.font_file(get_settings().fonts_dir, "Inter-SemiBold"), size)


def scene(stepped=False, hoodie=True):
    """A clips page's reel: a streamer in a white hoodie above, the game below, and a white
    caption box over the middle (or one box per line, the last one narrower). Returns the
    frame and the OCR lines of its box."""
    image = Image.new("RGB", SIZE, (60, 70, 90))
    draw = ImageDraw.Draw(image)
    for y in range(700, 1280, 6):  # the game: busy, not white
        draw.line([(0, y), (720, y)], fill=(40 + y % 90, 90, 60 + y % 70), width=3)
    if hoodie:  # white with shading, right against the box
        for y in range(330, 495):
            shade = 255 - (y * 7) % 60
            draw.line([(150, y), (570, y)], fill=(shade, shade, shade))
    lines, y = [], 500
    draw.rounded_rectangle((110, 495, 610, 605 if not stepped else 570), radius=12, fill="white")
    if stepped:
        draw.rounded_rectangle((250, 566, 470, 605), radius=12, fill="white")
    for i, words in enumerate(WORDS):
        if stepped and i == 2:
            words = "tutorial..."
        width = draw.textlength(words, font=font())
        x = 360 - width / 2
        draw.text((x, y), words, font=font(), fill=(20, 20, 20))
        lines.append(Line(x0=round(x), y0=y + 4, x1=round(x + width), y1=y + 30, text=words,
                          score=0.95))  # fmt: skip
        y += 34
    # White words outlined in black over the video above the box: not a caption box line.
    draw.text((200, 440), "13 years for this", font=font(), fill="white", stroke_width=3,
              stroke_fill="black")  # fmt: skip
    outlined = Line(x0=197, y0=441, x1=430, y1=474, text="13 years for this", score=0.9)
    return np.array(image), lines, outlined


def test_the_box_is_the_white_round_the_words_not_the_hoodie_beside_it():
    frame, lines, outlined = scene()
    block = retext.text_block(frame, [outlined, *lines])
    assert [ln.text for ln in block] == WORDS  # the outlined words over the video aren't in it
    left, top, right, bottom = retext.grow(frame, min(ln.x0 for ln in block),
                                           min(ln.y0 for ln in block),
                                           max(ln.x1 for ln in block),
                                           max(ln.y1 for ln in block))  # fmt: skip
    assert abs(left - 110) <= 8 and abs(right - 610) <= 8
    assert 488 <= top <= 500 and 598 <= bottom <= 612  # stopped at the hoodie's shading


def test_a_short_last_line_in_its_own_box_is_taken_in(monkeypatch):
    frame, lines, outlined = scene(stepped=True)
    block = retext.text_block(frame, [outlined, *lines[:2]])  # OCR missed the third line
    rect = retext.grow(frame, min(ln.x0 for ln in block), min(ln.y0 for ln in block),
                       max(ln.x1 for ln in block), max(ln.y1 for ln in block))  # fmt: skip
    assert rect[3] < 575  # the first two lines' box only
    monkeypatch.setattr(retext, "read_area", lambda f, r, s: [*lines, outlined])
    (_, top, _, bottom), words = retext.take_in_lines(frame, rect, block, get_settings())
    assert bottom >= 600 and top >= 488  # the third line's box below, not the words above
    assert words == [WORDS[0], WORDS[1], "tutorial..."]


class Writer:
    """The planner model: gives `answers` one after the other."""

    def __init__(self, *answers):
        self.answers = list(answers)
        self.asked = []

    def chat_json(self, messages, schema, temperature=0.3, **kw):
        self.asked.append(messages[-1]["content"])
        return {"text": self.answers.pop(0)}


OLD = " ".join(WORDS)


def test_the_caption_is_written_again_in_other_words():
    new = "N3on broke DOWN as Jason gave his life for Lucia and saw his SON again 😭"
    writer = Writer(OLD, new)
    assert retext.rewrite(writer, OLD) == new
    assert "kept nearly the same words" in writer.asked[1] and f'"{OLD}"' in writer.asked[0]
    too_long = "word " * 60
    assert retext.rewrite(Writer(too_long, new), OLD) == new
    # Three answers all too close: the least alike that still reads new; else it fails.
    close = OLD.replace("got", "was").replace("when", "as").replace("met", "saw")
    assert retext.SAME <= retext.alike(OLD, close) < retext.CLOSE
    assert retext.rewrite(Writer(OLD, close, OLD), OLD) == close
    with pytest.raises(ValueError, match="couldn't write the caption again"):
        retext.rewrite(Writer(OLD, OLD, OLD), OLD)
    hinted = Writer(new)
    retext.rewrite(hinted, OLD, "In Spanish.")
    assert hinted.asked[0].endswith("In Spanish.")


def test_the_new_box_covers_the_old_and_holds_the_new_words(tmp_path):
    settings = get_settings()
    box = retext.Box(x0=108, y0=493, x1=612, y1=607, start=0, end=10, fill="#FDFDFD", ink="#1E1E1E")
    face = compose.text_face(settings, None)("Inter-SemiBold", "x")
    words = "A new caption a good deal longer than the old one was, so it needs more lines 😭"
    png = retext.draw(words, box, 3, SIZE, face, tmp_path / "b.png")
    layer = np.asarray(Image.open(png).convert("RGBA"))
    covered = layer[box.y0 + 14 : box.y1 - 14, box.x0 + 14 : box.x1 - 14, 3]
    assert (covered == 255).all()  # every pixel of the old box under the new one
    lines, size, top, bottom = retext.layout_words("short 😭", face, box, 3, SIZE)
    assert (top, bottom) == (box.y0, box.y1) and len(lines) == 1  # no smaller than the old box
    assert size == round(box.height / 3.5 / retext.LINE)
    long = "word " * 40
    lines, size, top, bottom = retext.layout_words(long, face, box, 3, SIZE)
    assert bottom - top <= box.height * retext.GROW + 1 and top < box.y0


@needs_ffmpeg
def test_apply_draws_the_new_box_for_as_long_as_the_old_shows(tmp_path, monkeypatch):
    frame, lines, outlined = scene()
    still = tmp_path / "frame.png"
    Image.fromarray(frame).save(still)
    video = tmp_path / "reel.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-loop", "1", "-i", str(still), "-f", "lavfi",
                    "-i", "sine=f=440:d=3", "-t", "3", "-r", "30", "-pix_fmt", "yuv420p",
                    "-c:v", "libx264", "-c:a", "aac", "-shortest", str(video)],
                   check=True)  # fmt: skip
    monkeypatch.setattr(ocr, "read", lambda image, root, whole_frame=False: (
        [outlined, *lines] if image.size == SIZE else []))  # fmt: skip
    monkeypatch.setattr(retext, "read_area", lambda f, r, s: lines)
    settings = get_settings()
    preset = Preset(name="d", label="d", min_duration=1, max_duration=60, download_only=True,
                    retext={"enabled": True})  # fmt: skip
    new = "N3on broke DOWN as Jason gave his life for Lucia 😭"
    dest = tmp_path / "out.mp4"
    said = retext.apply(video, dest, preset, settings, ["-c:v", "libx264"], Writer(new), tmp_path)
    assert said[0].startswith('caption box written again: "N3on got EMOTIONAL') and new in said[0]
    saved = json.loads((tmp_path / "retext.json").read_text())
    assert saved["new"] == new and saved["box"]["start"] == 0 and saved["box"]["end"] >= 2.9
    out = tmp_path / "out.png"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-ss", "1.5", "-i", str(dest), "-frames:v",
                    "1", str(out)], check=True)  # fmt: skip
    after = np.asarray(Image.open(out).convert("RGB")).astype(int)
    before = frame.astype(int)
    inside = np.abs(after[500:600, 120:600] - before[500:600, 120:600]).mean()
    outside = np.abs(after[750:900] - before[750:900]).mean()
    assert inside > 3 * outside  # other words in the box; the rest as it was (as encoded)
    probe = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type",
                            "-of", "csv=p=0", str(dest)],
                           capture_output=True, text=True)  # fmt: skip
    assert probe.stdout.split() == ["video", "audio"]  # the sound kept


def test_a_video_without_a_box_comes_as_it_was(tmp_path, monkeypatch):
    monkeypatch.setattr(retext, "find_box", lambda *a: None)
    monkeypatch.setattr(retext.ffmpeg, "probe", lambda p: type("I", (), {
        "width": 720, "height": 1280, "duration": 3.0})())  # fmt: skip
    preset = Preset(name="d", label="d", min_duration=1, max_duration=60, download_only=True,
                    retext={"enabled": True})  # fmt: skip
    said = retext.apply(tmp_path / "v.mp4", tmp_path / "o.mp4", preset, get_settings(), [],
                        Writer(), tmp_path)  # fmt: skip
    assert said == ["no caption box found in the video: left as it was"]
    assert not (tmp_path / "o.mp4").exists()


@needs_ffmpeg
def test_a_download_only_preset_writes_the_box_again_or_says_why_not(tmp_path, monkeypatch):
    video = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
                    "color=c=gray:s=360x640:d=1:r=30", "-pix_fmt", "yuv420p", "-c:v", "libx264",
                    str(video)], check=True)  # fmt: skip
    source = download.Source(path=video, title="A reel")
    preset = Preset(name="dl", label="dl", min_duration=1, max_duration=60, download_only=True,
                    retext={"enabled": True})  # fmt: skip
    settings = get_settings()

    class Router:
        def __init__(self, *a):
            pass

        def planner(self):
            return self

        def __enter__(self):
            return Writer()

        def __exit__(self, *exc):
            pass

    monkeypatch.setattr(pipeline, "ModelRouter", Router)

    def rewrote(video, dest, preset, settings, encoder, llm, work):
        shutil.copyfile(video, dest)
        return ['caption box written again: "old" -> "new" (box 10x10 at 1,1, 0.0-1.0 s)']

    monkeypatch.setattr(retext, "apply", rewrote)
    from agentic_editor.backends import Backends

    backends = Backends("cpu", "cpu", "x264", "h264")
    out = pipeline.original(source, preset, tmp_path, lambda *a: None, settings, backends)
    assert any(d.startswith("caption box written again") for d in out.edl.decisions)
    assert not out.edl.doubts

    def broke(*a):
        raise ValueError("the model couldn't write the caption again (was empty)")

    monkeypatch.setattr(retext, "apply", broke)
    out = pipeline.original(source, preset, tmp_path, lambda *a: None, settings, backends)
    assert out.edl.doubts == [
        "caption box not written again: the model couldn't write the caption again (was empty)"
    ]
    assert out.path.is_file()  # the download still comes
    # Without retext nothing else changes.
    plain = preset.model_copy(
        update={"retext": preset.retext.model_copy(update={"enabled": False})}
    )
    out = pipeline.original(source, plain, tmp_path, lambda *a: None, settings, backends)
    assert out.edl.decisions == ["only downloaded: the original's own streams, copied"]


@needs_ocr
def test_ocr_finds_and_reads_a_real_caption_box(tmp_path):
    frame, _, _ = scene()
    block = retext.text_block(frame, ocr.read(Image.fromarray(frame), get_settings().models_dir,
                                              whole_frame=True))  # fmt: skip
    assert " ".join(ln.text for ln in block).startswith("N3on got EMOTIONAL")
    assert all("13 years" not in ln.text for ln in block)
