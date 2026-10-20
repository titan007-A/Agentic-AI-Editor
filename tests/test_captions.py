from agentic_editor.presets import CaptionStyle
from agentic_editor.render.captions import ass_color, ass_time, build_ass, group_lines
from tests.conftest import words_from

WORDS = words_from("one@0.0-0.3 two@0.35-0.6 three@0.65-1.0 four@1.05-1.3 five@3.0-3.4")


def dialogue(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def test_ass_color_is_bgr_with_alpha():
    assert ass_color("#FFE000") == "&H0000E0FF"
    assert ass_color("#102030", alpha=0x80) == "&H80302010"


def test_ass_time_format():
    assert ass_time(3725.456) == "1:02:05.46"


def test_lines_break_on_word_count_and_pauses():
    lines = group_lines(WORDS, per_line=3)
    assert [[w.text for w in line] for line in lines] == [
        ["one", "two", "three"],
        ["four"],
        ["five"],
    ]


def test_highlight_emits_one_event_per_word_with_current_word_colored():
    ass = build_ass(WORDS, CaptionStyle(words_per_line=3), (1080, 1920), "Anton")
    events = dialogue(ass)
    assert len(events) == len(WORDS)
    assert events[1].endswith(r"ONE {\1c&H0000E0FF}TWO{\r} THREE")
    assert "0:00:00.35,0:00:00.65" in events[1]


def test_karaoke_uses_one_event_per_line():
    style = CaptionStyle(words_per_line=3, animation="karaoke", uppercase=False)
    events = dialogue(build_ass(WORDS, style, (1080, 1920), "Anton"))
    assert len(events) == 3
    assert r"{\kf35}one" in events[0]


def test_overrides_change_position_and_grouping():
    ass = build_ass(WORDS, CaptionStyle(), (1080, 1920), "Anton", position="top", words_per_line=1)
    style_line = next(line for line in ass.splitlines() if line.startswith("Style:"))
    assert style_line.split(",")[18] == "8"
    assert len(dialogue(ass)) == len(WORDS)


def test_braces_in_words_cannot_inject_tags():
    words = words_from("{\\b1}hi@0-1")
    assert "{\\b1}" not in build_ass(words, CaptionStyle(), (1080, 1920), "Anton")
