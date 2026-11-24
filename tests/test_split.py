import io
import itertools
import shutil
import subprocess

import pytest
from PIL import Image

from agentic_editor.edl import EDL, Clip
from agentic_editor.presets import Preset, Split
from agentic_editor.render import preview, split
from agentic_editor.render.video import Layers, build_graph
from agentic_editor.settings import get_settings
from agentic_editor.transcript import Word

needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
FRAME = (1080, 1920)


def split_preset(**split_fields) -> Preset:
    return Preset(
        name="s", label="s", min_duration=10, max_duration=60,
        split={"enabled": True, **split_fields},
        framing={"mode": "smart", "picture_y": 120, "picture_center": True},
        audio={"source": "original"},
    )  # fmt: skip


def words(*spans: tuple[float, float]) -> list[Word]:
    return [Word(text=f"w{i}", start=a, end=b) for i, (a, b) in enumerate(spans)]


def test_a_split_preset_shows_the_picture_whole_in_the_top_panel_with_vocals():
    preset = split_preset()
    assert preset.framing.mode == "none"
    assert preset.framing.picture_rows == (440, 1180) == preset.split.rows
    assert (preset.framing.picture_center, preset.framing.picture_y) == (False, 0)
    assert preset.audio.source == "vocals"
    # Off, nothing about the preset changes.
    plain = Preset(name="p", label="p", min_duration=10, max_duration=60, split={"top": 0})
    assert plain.framing.mode == "smart" and plain.audio.source == "original"


def test_two_panels_must_fit_the_frame():
    with pytest.raises(ValueError, match="run off"):
        split_preset(top=500, height=740)


def test_the_sound_changes_in_the_widest_pause_near_the_share_asked():
    sung = words((0, 9.5), (10.0, 12.0), (13.5, 20.0), (20.2, 40))
    # 35% of 40 s is 14 s: the 1.5 s pause at 12-13.5 is wider than the 0.5 s one at 9.5-10.
    assert split.switch_time(40.0, Split(enabled=True), sung) == pytest.approx(12.75)
    # Nothing to land in: the share as asked.
    assert split.switch_time(40.0, Split(enabled=True), words((0, 40))) == pytest.approx(14.0)
    # A pause past SNAP seconds doesn't count.
    far = words((0, 9.0), (11.0, 40))
    assert split.switch_time(40.0, Split(enabled=True), far) == pytest.approx(14.0)
    # Each part keeps a little: never at the very start or end.
    assert split.switch_time(3.0, Split(enabled=True, switch=0.1), []) == pytest.approx(1.0)


def test_the_sound_changes_in_the_singers_silence_before_a_gap_whisper_heard():
    sung = words((0, 9.5), (10.0, 12.0), (13.5, 20.0), (20.2, 40))
    on = Split(enabled=True)  # 35% of 40 s: 14 s
    # A real silence of the voice near 14 s wins over the words' widest gap, at its
    # nearest point a little in from its edge.
    assert split.switch_time(40.0, on, sung, [(15.0, 15.4)]) == pytest.approx(15.1)
    # A silence that holds 14 s: right there.
    assert split.switch_time(40.0, on, sung, [(13.0, 16.0)]) == pytest.approx(14.0)
    # A long silence whose start is near, though its middle is past the snap.
    assert split.switch_time(40.0, on, sung, [(15.6, 17.8)]) == pytest.approx(15.7)
    # One of 0.3 s or more before a shorter one nearer; one out of reach doesn't count.
    quiet = [(13.6, 13.75), (15.0, 15.6), (19.0, 21.0)]
    assert split.switch_time(40.0, on, sung, quiet) == pytest.approx(15.1)
    # None near: the words' gap, as before.
    assert split.switch_time(40.0, on, sung, [(30, 31)]) == pytest.approx(12.75)
    # A silence counts whatever whisper's word timings say: the nearest.
    inside = [(13.8, 14.2), (15.5, 15.9)]
    assert split.switch_time(40.0, on, words((0, 14.6), (16.0, 40)), inside) == (
        pytest.approx(14.0)
    )
    # A breath no word's timing spans before one inside a word, at a word's end in it.
    assert split.switch_time(40.0, on, words((0, 14.6), (16.0, 40)), [], inside) == (
        pytest.approx(15.6)
    )
    assert split.switch_time(40.0, on, words((0, 14.6), (14.6, 40)), [], [(14.56, 14.64)]) == (
        pytest.approx(14.6)
    )


def test_with_no_silence_near_the_share_a_breath_near_it_then_one_further_away():
    on = Split(enabled=True)  # 35% of 40 s: 14 s
    held = words((0, 40))  # one long held note, as whisper hears it
    # A breath within the snap before a silence far off: the share matters.
    assert split.switch_time(40.0, on, [], [(21.0, 22.0)], [(15.0, 15.5)]) == pytest.approx(15.1)
    # Nothing near: a silence anywhere from 20% to 60% of the reel, the nearest.
    assert split.switch_time(40.0, on, [], [(21.0, 22.0), (30.0, 31.0)]) == pytest.approx(21.1)
    # A breath further away when there is no silence at all.
    assert split.switch_time(40.0, on, [], [], [(9.0, 9.4)]) == pytest.approx(9.3)
    # Past 60% (or before 20%) doesn't count: the words', else the share as asked.
    assert split.switch_time(40.0, on, held, [(30.0, 31.0)], [(2.0, 3.0)]) == pytest.approx(14.0)
    # A silence reaching into the window counts from its edge inside it.
    assert split.switch_time(40.0, on, [], [(22.0, 26.0)]) == pytest.approx(22.1)


@needs_ffmpeg
def test_the_singers_breaths_are_found_besides_their_silences(tmp_path):
    voice = tmp_path / "vocals.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-af", "volume=enable='between(t,3,3.4)':volume=0.06,"
         "volume=enable='between(t,6,6.5)':volume=0", str(voice)],
        check=True,
    )  # fmt: skip
    pauses, breaths = split.quiet_spans(voice, 0.0, 10.0)
    assert [round(a) for a, _ in pauses] == [6]  # the silence
    assert [round(a) for a, _ in breaths] == [3, 6]  # 24 dB down: a breath, and the silence


@needs_ffmpeg
def test_the_singers_silences_are_found_in_the_isolated_voice(tmp_path):
    voice = tmp_path / "vocals.wav"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=10",
         "-af", "volume=enable='between(t,4,4.5)+between(t,7,7.05)':volume=0", str(voice)],
        check=True,
    )  # fmt: skip
    # From 2 s on: the 0.5 s silence at 4 s; the 50 ms one at 7 s is too short to count.
    found = split.voice_pauses(voice, 2.0, 10.0)
    assert len(found) == 1
    assert found[0][0] == pytest.approx(2.0, abs=0.03)
    assert found[0][1] == pytest.approx(2.5, abs=0.03)


def test_the_panels_play_one_after_the_other_holding_their_frames():
    parts = split.graph_parts("base", "backdrop", Split(enabled=True), FRAME, 12.5, 40.0)
    joined = ";".join(parts)
    assert "[splita]crop=1080:740:0:440,trim=end=12.500" in joined
    assert "tpad=stop_mode=clone:stop_duration=27.500[panela]" in joined
    assert "[splitb]crop=1080:740:0:440,trim=start=12.500" in joined
    assert "tpad=start_mode=clone:start_duration=12.500[panelb]" in joined
    assert parts[-2] == "[backdrop][panela]overlay=0:440:shortest=1[splitone]"
    assert parts[-1] == "[splitone][panelb]overlay=0:1180:shortest=1[split]"


def test_the_graph_lays_both_panels_on_the_background_and_never_zooms():
    preset = split_preset().model_copy(update={"zoom_punch_ins": True})
    edl = EDL(clips=[Clip(start=5, end=45)], crop_mode="none")
    layers = Layers(split_at=14.0, split_labels=3, pulses=[1.0, 2.0])
    graph, out = build_graph(edl, preset, (1920, 1080), layers)
    assert "color=c=0x000000:s=1080x1920:r=30[backdrop]" in graph
    assert "[splitone][panelb]overlay=0:1180" in graph
    assert "[3:v]overlay=0:0[labelled]" in graph
    assert "zoomed" not in graph and out == "out"
    # A background picture goes under the panels instead of the colour.
    layers = Layers(split_at=14.0, background_image=2)
    graph, _ = build_graph(edl, preset, (1920, 1080), layers)
    assert "[2:v]setsar=1,format=rgba[backdrop]" in graph and "color=c=" not in graph


def test_labels_sit_in_each_panels_top_left_corner():
    image = split.labels_image(Split(enabled=True), FRAME, get_settings().fonts_dir)
    alpha = image.getchannel("A")
    first = alpha.crop((0, 440, 1080, 1180)).getbbox()
    second = alpha.crop((0, 1180, 1080, 1920)).getbbox()
    for box in (first, second):  # where the letters' ink starts, give or take a glyph's edge
        assert 106 <= box[0] <= 110 and 42 <= box[1] <= 46
    assert alpha.crop((0, 0, 1080, 440)).getbbox() is None
    # An empty label draws nothing.
    image = split.labels_image(
        Split(enabled=True, second_label=" "), FRAME, get_settings().fonts_dir
    )
    assert image.getchannel("A").crop((0, 1180, 1080, 1920)).getbbox() is None


def test_the_preview_shows_both_panels_and_their_labels():
    preset = split_preset(first_label="", second_label="")
    preset = preset.model_copy(
        update={"captions": preset.captions.model_copy(update={"enabled": False})}
    )
    jpg, notes = preview.render_checked(preset, get_settings(), "4:3")
    image = Image.open(io.BytesIO(jpg)).convert("RGB")
    # The stand-in's same rows in both panels, the background between the panels' tops.
    top = image.crop((0, 440, 1080, 1180))
    bottom = image.crop((0, 1180, 1080, 1920))
    assert sum(abs(a - b) for a, b in zip(top.tobytes(), bottom.tobytes(), strict=True)) < (
        len(top.tobytes()) * 4
    )
    labelled = preview.render_checked(
        preset.model_copy(update={"split": split_preset().split}), get_settings(), "4:3"
    )[0]
    labelled_image = Image.open(io.BytesIO(labelled)).convert("RGB")
    assert labelled_image.getpixel((140, 440 + 60)) != image.getpixel((140, 440 + 60))
    assert notes == []


def tone(path, frequency: float, seconds: float) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"sine=frequency={frequency}:duration={seconds}:sample_rate=48000",
         "-af", "volume=0.3", "-ac", "2", str(path)],
        check=True,
    )  # fmt: skip


@needs_ffmpeg
def test_the_soundtrack_is_the_whole_sound_then_the_vocals_as_loud(tmp_path):
    mix, vocals = tmp_path / "mix.wav", tmp_path / "vocals.wav"
    tone(mix, 110, 30)
    tone(vocals, 880, 30)
    edl = EDL(clips=[Clip(start=4, end=24)])
    preset = split_preset()
    track = split.soundtrack(edl, preset, mix, vocals, 7.0, tmp_path)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0",
         str(track)], capture_output=True, text=True, check=True,
    )  # fmt: skip
    assert float(probe.stdout) == pytest.approx(20.0, abs=0.05)

    def dominant(start: float) -> float:
        raw = subprocess.run(
            ["ffmpeg", "-v", "error", "-ss", str(start), "-t", "1", "-i", str(track),
             "-ac", "1", "-f", "s16le", "-"], capture_output=True, check=True,
        ).stdout  # fmt: skip
        samples = [
            int.from_bytes(raw[i : i + 2], "little", signed=True) for i in range(0, len(raw), 2)
        ]
        crossings = sum(1 for a, b in itertools.pairwise(samples) if a < 0 <= b)
        return crossings / 1.0

    assert dominant(2.0) == pytest.approx(110, rel=0.05)  # the mix before the change
    assert dominant(12.0) == pytest.approx(880, rel=0.05)  # the vocals after


def test_text_over_the_second_panel_is_text_over_the_video():
    preset = split_preset().model_copy(
        update={"text_blocks": [{"text": "Follow", "size": 50, "anchor": "bottom", "offset": 300}]}
    )
    preset = Preset.model_validate(preset.model_dump())
    _, notes = preview.render_checked(preset, get_settings(), "4:3")
    assert any(n.startswith("Text 1 runs over the video") for n in notes)
