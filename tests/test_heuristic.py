from agentic_editor.heuristic import plan_by_energy
from agentic_editor.presets import Preset
from agentic_editor.transcript import Word

PRESET = Preset(name="t", label="t", min_duration=4, max_duration=8)


def phrase(start: float, count: int = 4) -> list[Word]:
    return [Word(text=f"w{i}", start=start + i * 0.5, end=start + i * 0.5 + 0.4)
            for i in range(count)]  # fmt: skip


def test_picks_the_loud_phrase_aligned_window(make_analysis):
    words = phrase(0) + phrase(5) + phrase(10) + phrase(15)
    energy = [-40.0] * 40
    energy[20:32] = [-10.0] * 12
    edl = plan_by_energy(make_analysis(words, 20.0, energy), PRESET)
    clip = edl.clips[0]
    assert clip.start == 10.0
    assert PRESET.min_duration <= clip.duration <= PRESET.max_duration
    assert clip.end in {w.end for w in words}


def test_short_source_uses_whatever_there_is(make_analysis):
    edl = plan_by_energy(make_analysis(phrase(0.5, 3), 3.0), PRESET)
    assert edl.clips[0].end <= 3.0
