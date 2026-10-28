from contextlib import contextmanager
from pathlib import Path

import pytest

from agentic_editor import download, hooks, pipeline, trim
from agentic_editor.cuts import clean_edl
from agentic_editor.edl import EDL, Clip
from agentic_editor.ffmpeg import MediaInfo
from agentic_editor.llm import LLMError
from agentic_editor.planner import build_lines
from agentic_editor.presets import Preset
from agentic_editor.render.audio import Loudness
from agentic_editor.transcript import Word
from tests.conftest import words_from

WORDS = words_from("a.@0-0.5 b.@1-1.5 c.@3-3.5 d.@5-5.5 e.@7-7.5 f.@9-9.5")


class Down:
    model = "down"

    def chat_json(self, messages, schema, **kwargs):
        raise LLMError("timeout")


def test_a_failed_translation_is_a_doubt(make_analysis):
    preset = Preset(
        name="t",
        label="t",
        min_duration=4,
        max_duration=8,
        captions={"mode": "translated", "language": "Spanish"},
    )
    edl = pipeline.translated(
        Down(), EDL(clips=[Clip(start=0, end=5)]), make_analysis(WORDS, 10), preset, None
    )
    assert edl.doubts == [edl.decisions[-1]] and edl.doubts[0].startswith("translation failed")


def test_a_reel_left_short_is_a_doubt_once(make_analysis):
    preset = Preset(name="t", label="t", min_duration=30, max_duration=60)
    edl = trim.apply(EDL(clips=[Clip(start=0, end=9.5)]), make_analysis(WORDS, 10), preset)
    edl = trim.apply(edl, make_analysis(WORDS, 10), preset)
    assert len(edl.doubts) == 1 and "left shorter" in edl.doubts[0]


def sentence(start, text):
    return [
        Word(text=w, start=start + i * 0.4, end=start + i * 0.4 + 0.3)
        for i, w in enumerate(text.split())
    ]


class Replies:
    """Canned replies in order; an LLMError in the list is raised instead."""

    model = "fake"

    def __init__(self, *replies):
        self.replies = list(replies)

    def chat_json(self, messages, schema, **kwargs):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def talk(make_analysis, loud_from: float = 60.0):
    words = [w for k in range(25) for w in sentence(k * 4, f"this is sentence number {k} here.")]
    energy = [-30.0 if i * 0.5 < loud_from else -24.0 for i in range(200)]
    return make_analysis(words, 100.0, energy)


def planned_as(monkeypatch, *edls):
    monkeypatch.setattr(pipeline, "plan_variants", lambda *args, **kwargs: list(edls))


def test_the_opening_is_chosen_inside_the_window_the_reel_keeps(make_analysis, monkeypatch):
    analysis = talk(make_analysis)
    preset = Preset(name="t", label="t", min_duration=15, max_duration=30)
    planned_as(monkeypatch, EDL(clips=[Clip(start=0.0, end=99.0)]))
    ((edl, _),) = pipeline.plan(
        Replies({"option": 2, "reason": "a question"}), analysis, preset, None
    )
    cleaned = clean_edl(EDL(clips=[Clip(start=0.0, end=99.0)]), analysis.transcript.words, [])
    trimmed = trim.apply(cleaned, analysis, preset)
    choices = hooks.options(trimmed, build_lines(analysis), preset)
    assert edl.clips[0].start == round(choices[2].start - trim.PREROLL, 3)
    assert edl.decisions[-1].startswith("opened") and "a question" in edl.decisions[-1]


def test_versions_that_trim_down_to_the_same_stretch_are_made_once(make_analysis, monkeypatch):
    analysis = talk(make_analysis)
    analysis = analysis.model_copy(
        update={"energy": [-18.0 if 40 <= i * 0.5 < 62 else -30.0 for i in range(200)]}
    )
    preset = Preset(name="t", label="t", min_duration=15, max_duration=20, hook={"variants": 2})
    planned_as(
        monkeypatch,
        EDL(clips=[Clip(start=0.0, end=62.0)]),
        EDL(clips=[Clip(start=40.0, end=99.0)], variant=1),
    )
    options = {"option": 0, "reason": "x"}
    assert len(pipeline.plan(Replies(options, options), analysis, preset, None)) == 1


def test_punch_ins_are_picked_from_what_the_reel_keeps(make_analysis, monkeypatch):
    analysis = talk(make_analysis)
    preset = Preset(name="t", label="t", min_duration=15, max_duration=30, zoom_punch_ins=True)
    planned_as(monkeypatch, EDL(clips=[Clip(start=0.0, end=99.0)]))
    # "2" is said only before the window, "20" only inside it.
    llm = Replies({"option": 0, "reason": "x"}, {"words": ["2", "20"]})
    ((edl, _),) = pipeline.plan(llm, analysis, preset, None)
    clip = edl.clips[0]
    assert len(edl.zooms) == 1 and clip.start <= edl.zooms[0].time < clip.end


def test_a_failed_punch_in_pick_keeps_the_plan(make_analysis):
    class Model:
        model = "fake"

        def chat_json(self, messages, schema, **kwargs):
            asked = set(schema.get("properties", {}))
            if "candidates" in asked:
                span = {"start_line": 0, "end_line": 6, "hook": 8, "completeness": 8, "peak": 8}
                return {"candidates": [span | {"reason": "x"}]}
            if "candidate" in asked:
                return {"candidate": 0, "reason": "x", "hook": "Wait", "title": "T"}
            if "option" in asked:
                return {"option": 0, "reason": "x"}
            raise LLMError("broken JSON")  # the punch-in words

    preset = Preset(name="t", label="t", min_duration=15, max_duration=30, zoom_punch_ins=True)
    ((edl, planner),) = pipeline.plan(Model(), talk(make_analysis), preset, None)
    assert planner == "fake" and edl.hook == "Wait" and edl.zooms == []


class Offline:
    """A ModelRouter whose models are never reached; `seen` records what ran while the
    vision model held the GPU."""

    def __init__(self, *args):
        self.vision_open = False

    @contextmanager
    def planner(self):
        yield Down()

    @contextmanager
    def vision(self):
        self.vision_open = True
        yield Down()
        self.vision_open = False


def edit_offline(monkeypatch, tmp_path, make_analysis, presets, broken=(), calls=None):
    """pipeline.edit with analysis, planning and rendering faked; rendering `broken`
    versions raises. `calls` collects (what, whether the vision model was open)."""
    calls = [] if calls is None else calls
    router = Offline()

    def produce(edl, preset, analysis, video, dest, *args):
        calls.append(("render", sorted(args[-1])))
        if dest.stem in broken:
            raise RuntimeError(f"encoder crashed on {dest.stem}")
        dest.write_bytes(b"mp4")
        return edl, 1, []

    def detect(video, start, end, models_dir):
        calls.append(("faces", router.vision_open))
        return []

    def silences(analysis, threshold, shortest):
        calls.append(("silences", threshold))
        return analysis

    analysis = make_analysis(WORDS, 10)
    monkeypatch.setattr(pipeline, "ModelRouter", lambda *args: router)
    monkeypatch.setattr(pipeline, "analyze", lambda *args: analysis)
    monkeypatch.setattr(pipeline, "with_silences", silences)
    monkeypatch.setattr(pipeline, "_reachable", lambda client: False)
    monkeypatch.setattr(pipeline, "produce", produce)
    monkeypatch.setattr(pipeline.faces, "detect", detect)
    monkeypatch.setattr(pipeline.compose, "picture_area", lambda *args: (0, 0, 1920, 1080))
    settings = type("S", (), {"media_dir": tmp_path / "media", "models_dir": tmp_path})
    source = download.Source(path=tmp_path / "source.mp4", title="Talk")
    work = tmp_path / "cache" / "abc"
    return pipeline.edit(source, work, presets, settings, None, None, lambda *args: None, "run1")


def test_one_failed_version_keeps_the_others(make_analysis, monkeypatch, tmp_path):
    presets = [
        Preset(name=n, label=n, min_duration=4, max_duration=8, captions={"enabled": False})
        for n in ("a", "b", "c")
    ]
    result = edit_offline(monkeypatch, tmp_path, make_analysis, presets, broken={"b"})
    assert [o.preset for o in result.outputs] == ["a", "c"]
    assert result.failures == ["b: RuntimeError: encoder crashed on b"]


def test_a_job_whose_every_version_failed_still_fails(make_analysis, monkeypatch, tmp_path):
    presets = [Preset(name="a", label="a", min_duration=4, max_duration=8)]
    with pytest.raises(RuntimeError, match="encoder crashed on a"):
        edit_offline(monkeypatch, tmp_path, make_analysis, presets, broken={"a"})


def test_silences_are_found_once_per_way_of_counting_them(make_analysis, monkeypatch, tmp_path):
    presets = [
        Preset(name="a", label="a", min_duration=4, max_duration=8, hook={"variants": 2}),
        Preset(name="b", label="b", min_duration=4, max_duration=8),
        Preset(name="c", label="c", min_duration=4, max_duration=8, silence={"threshold_db": -40}),
    ]
    calls = []
    edit_offline(monkeypatch, tmp_path, make_analysis, presets, calls=calls)
    assert [c for c in calls if c[0] == "silences"] == [("silences", -35.0), ("silences", -40)]


def test_faces_are_found_before_the_vision_model_takes_the_gpu(
    make_analysis, monkeypatch, tmp_path
):
    presets = [Preset(name="a", label="a", min_duration=4, max_duration=8)]
    calls = []
    edit_offline(monkeypatch, tmp_path, make_analysis, presets, calls=calls)
    faces = [open_ for what, open_ in calls if what == "faces"]
    renders = [found for what, found in calls if what == "render"]
    assert faces == [False] and len(renders) == 1 and len(renders[0]) == 1


def test_a_render_is_measured_while_the_gpu_is_given_back(make_analysis, monkeypatch, tmp_path):
    encoding, seen = [], []

    @contextmanager
    def encodes(*models, progress=None):
        encoding.append(True)
        yield
        encoding.pop()

    def probe(path):
        seen.append(("probe", bool(encoding)))
        return MediaInfo(5.0, 1080, 1920, 30.0, True, True)

    def loudness(path):
        seen.append(("loudness", bool(encoding)))
        return Loudness(-14.0, -3.0)

    monkeypatch.setattr(pipeline.slots, "encoding", encodes)
    monkeypatch.setattr(pipeline.compose, "render", lambda *args: args[4].write_bytes(b"mp4"))
    monkeypatch.setattr(pipeline.ffmpeg, "probe", probe)
    monkeypatch.setattr(pipeline, "measure_loudness", loudness)
    preset = Preset(
        name="t", label="t", min_duration=4, max_duration=8, captions={"enabled": False}
    )
    edl = EDL(clips=[Clip(start=0, end=5)], crop_mode="none")
    _, renders, issues = pipeline.produce(
        edl, preset, make_analysis(WORDS, 10), tmp_path / "source.mp4", tmp_path / "out.mp4",
        tmp_path / "work", None, None, None, lambda *args: None,
    )  # fmt: skip
    assert (renders, issues) == (1, [])
    assert seen == [("probe", True), ("loudness", True)]


def test_a_loudness_fix_keeps_the_picture_and_lays_new_sound_under_it(
    make_analysis, monkeypatch, tmp_path
):
    renders, commands, levels = [], [], [Loudness(-18.0, -6.0), Loudness(-14.2, -3.0)]

    def render(*args):
        renders.append(args[1].audio.loudness_lufs)
        args[4].write_bytes(b"mp4")

    def run(args):
        commands.append(args)
        Path(args[-1]).write_bytes(b"remuxed")

    monkeypatch.setattr(pipeline.compose, "render", render)
    monkeypatch.setattr(
        pipeline.ffmpeg, "probe", lambda p: MediaInfo(5.0, 1080, 1920, 30, True, True)
    )
    monkeypatch.setattr(pipeline, "measure_loudness", lambda path: levels.pop(0))
    monkeypatch.setattr(pipeline.ffmpeg, "run", run)
    monkeypatch.setattr(
        pipeline.audio, "render_soundtrack", lambda edl, preset, *args: args[-1] / "soundtrack.wav"
    )
    preset = Preset(
        name="t", label="t", min_duration=4, max_duration=8, captions={"enabled": False}
    )
    edl = EDL(clips=[Clip(start=0, end=5)], crop_mode="none")
    dest = tmp_path / "out.mp4"
    _, count, issues = pipeline.produce(
        edl, preset, make_analysis(WORDS, 10), tmp_path / "source.mp4", dest,
        tmp_path / "work", None, None, None, lambda *args: None,
    )  # fmt: skip
    assert (count, issues, renders) == (2, [], [-14.0])
    (remux,) = commands
    assert remux[remux.index("-c:v") + 1] == "copy" and dest.read_bytes() == b"remuxed"


def test_a_video_that_cant_be_analysed_fails_with_the_reason(make_analysis, monkeypatch, tmp_path):
    def no_audio(*args):
        raise ValueError("clip.mp4 has no audio track")

    presets = [Preset(name="a", label="a", min_duration=4, max_duration=8)]
    monkeypatch.setattr(pipeline, "ModelRouter", lambda *args: Offline())
    monkeypatch.setattr(pipeline, "analyze", no_audio)
    settings = type("S", (), {"media_dir": tmp_path / "media", "models_dir": tmp_path})
    source = download.Source(path=tmp_path / "clip.mp4", title="Clip")
    with pytest.raises(ValueError, match="has no audio track"):
        pipeline.edit(
            source, tmp_path / "cache", presets, settings, None, None, lambda *a: None, "r"
        )


def test_without_the_vision_model_only_overlay_sized_boxes_are_blurred(tmp_path):
    from agentic_editor.analysis.watermarks import Watermark

    handle = Watermark(x=1700, y=40, width=180, height=60, spans=[(0, 9)], windows=9)
    shirt = Watermark(x=800, y=500, width=600, height=500, spans=[(0, 9)], windows=9)
    kept = pipeline.confirmed_watermarks(
        [handle, shirt], None, tmp_path / "v.mp4", None, tmp_path, lambda *a: None, (1920, 1080)
    )
    assert kept == [handle]


def test_a_failed_vision_answer_is_asked_again_next_time(monkeypatch, tmp_path, make_analysis):
    import json

    from agentic_editor.analysis import subjects

    answers = [[None, []], [[]]]
    asked = []

    def find(vision, video, times, *args):
        asked.append(list(times))
        return answers.pop(0)

    monkeypatch.setattr(subjects, "find", find)
    vision = type("V", (), {"model": "m:4b"})()
    analysis = make_analysis(WORDS, 10)
    settings = type("S", (), {"ffmpeg_bin": "ffmpeg"})()
    pipeline.subject_samples(vision, tmp_path / "v.mp4", [1.0, 2.0], analysis, settings, tmp_path)
    assert list(json.loads((tmp_path / "subjects-m_4b.json").read_text())) == ["2.000"]
    pipeline.subject_samples(vision, tmp_path / "v.mp4", [1.0, 2.0], analysis, settings, tmp_path)
    assert asked == [[1.0, 2.0], [1.0]]
