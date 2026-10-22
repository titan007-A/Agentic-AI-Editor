from agentic_editor.edl import Cue
from agentic_editor.presets import CaptionStyle
from agentic_editor.render.captions import ass_color, ass_time, build_ass
from agentic_editor.subtitles import Placement, lyric_cues, place, speech_cues, to_srt
from tests.conftest import words_from

WORDS = words_from("one@0.0-0.3 two@0.35-0.6 three@0.65-1.0 four@1.05-1.3 five@3.0-3.4")
FRAME = (1080, 1920)


def dialogue(ass: str) -> list[str]:
    return [line for line in ass.splitlines() if line.startswith("Dialogue:")]


def test_ass_color_is_bgr_with_alpha():
    assert ass_color("#FFE000") == "&H0000E0FF"
    assert ass_color("#102030", alpha=0x80) == "&H80302010"


def test_ass_time_format():
    assert ass_time(3725.456) == "1:02:05.46"


def test_speech_lines_break_on_word_count_and_pauses():
    assert [c.text for c in speech_cues(WORDS, per_line=3)] == ["one two three", "four", "five"]


def test_lyric_lines_run_until_the_singer_breathes():
    sung = words_from("hold@0-0.5 me@0.6-0.9 now@1.0-1.8 touch@3.0-3.4 me@3.5-3.8")
    assert [c.text for c in lyric_cues(sung)] == ["hold me now", "touch me"]


def test_highlight_emits_one_event_per_word_with_current_word_colored():
    ass = build_ass(speech_cues(WORDS, 3), CaptionStyle(), FRAME, "Anton")
    events = dialogue(ass)
    assert len(events) == len(WORDS)
    assert events[1].endswith(r"ONE {\1c&H0000E0FF}TWO{\r} THREE")
    assert "0:00:00.35,0:00:00.65" in events[1]


def test_karaoke_uses_one_event_per_line():
    style = CaptionStyle(animation="karaoke", uppercase=False)
    events = dialogue(build_ass(speech_cues(WORDS, 3), style, FRAME, "Anton"))
    assert len(events) == 3
    assert r"{\kf35}one" in events[0]


def test_whole_line_captions_fade_in_and_out():
    cues = [Cue(start=1.0, end=3.0, text="Hola a todos")]
    (event,) = dialogue(build_ass(cues, CaptionStyle(uppercase=False), FRAME, "Anton"))
    assert event.endswith(r"{\fad(120,120)}Hola a todos")


def test_a_moved_cue_carries_its_own_alignment_and_margin():
    cues = speech_cues(WORDS[:1], 3)
    ass = build_ass(cues, CaptionStyle(), FRAME, "Anton", placements=[Placement(8, 220)])
    (event,) = dialogue(ass)
    assert ",0,0,220,," in event and r"{\an8}" in event


def test_position_override_changes_the_style_alignment():
    ass = build_ass(speech_cues(WORDS, 1), CaptionStyle(), FRAME, "Anton", position="top")
    style_line = next(line for line in ass.splitlines() if line.startswith("Style:"))
    assert style_line.split(",")[18] == "8"


def test_braces_in_words_cannot_inject_tags():
    words = words_from("{\\b1}hi@0-1")
    assert "{\\b1}" not in build_ass(speech_cues(words, 3), CaptionStyle(), FRAME, "Anton")


def test_srt_numbers_cues_with_millisecond_times():
    srt = to_srt([Cue(start=1.5, end=3.25, text="hello"), Cue(start=61, end=62, text="again")])
    assert (
        srt
        == "1\n00:00:01,500 --> 00:00:03,250\nhello\n\n2\n00:01:01,000 --> 00:01:02,000\nagain\n"
    )


def test_a_caption_moves_off_a_face_and_back_when_clear():
    cues = [Cue(start=0, end=2, text="a"), Cue(start=3, end=5, text="b")]
    # A face covering the lower middle of the frame during the first cue only.
    faces = [(1.0, [(1250.0, 1500.0)])]
    first, second = place(cues, "bottom", 520, 110, 1920, faces)
    top, bottom = first.band(110, 1920)
    assert bottom < 1250 - 24 or top > 1500 + 24
    assert second == Placement(2, 520)


def test_without_faces_every_caption_stays_where_the_style_puts_it():
    cues = speech_cues(WORDS, 3)
    assert set(place(cues, "middle", 520, 110, 1920, [])) == {Placement(5, 0)}


def test_lyric_lines_follow_the_singers_breaths():
    sung = words_from(
        "I@0.1-0.2 am@0.3-0.4 the@0.5-0.6 one@0.7-0.9 in@1.0-1.1 your@1.2-1.3 life@1.4-1.8 "
        "that@1.9-2.0 can't@2.1-2.3 see@2.4-2.6 you@2.7-3.0 And@4.0-4.2 you@4.3-4.6"
    )
    cues = lyric_cues(sung, max_words=8, breaths=[(0.0, 3.2), (3.8, 5.0)])
    assert [c.text for c in cues] == ["I am the one in", "your life that can't see you", "And you"]
    assert all(len(c.text.split()) >= 2 for c in cues)


def test_scraps_of_sparse_singing_are_joined_into_phrases():
    sung = words_from(
        "Maybe@1.9-2.4 Save@3.0-3.7 me@4.8-5.5 I@11.7-11.9 am@12.0-12.3 the@12.4-12.5 "
        "one@12.6-13.0 that@13.8-14.0 can't@14.1-14.5 see@14.6-15.0 you@15.1-15.6 And@16.0-16.4 "
        "you@18.0-18.5"
    )
    breaths = [(1.8, 3.8), (4.7, 5.6), (11.6, 17.0), (17.9, 18.6)]
    texts = [c.text for c in lyric_cues(sung, max_words=8, breaths=breaths)]
    assert texts == ["Maybe Save me", "I am the one that can't see you", "And you"]


def test_a_moved_caption_stays_put_until_the_speaker_pauses():
    cues = [
        Cue(start=0, end=1, text="a"),
        Cue(start=1.1, end=2, text="b"),
        Cue(start=2.1, end=3, text="c"),
        Cue(start=4.0, end=5, text="d"),
    ]
    # Something sits on the usual bottom position only while the first line shows.
    blocked = [(0.5, [(1300.0, 1500.0)])]
    spots = place(cues, "bottom", 520, 110, 1920, blocked)
    assert spots[0] != Placement(2, 520)
    assert spots[1] == spots[2] == spots[0]  # still speaking: no jumping back
    assert spots[3] == Placement(2, 520)  # after the pause, back to its usual place
