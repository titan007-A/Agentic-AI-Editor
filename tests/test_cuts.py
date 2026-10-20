from agentic_editor.cuts import (
    clean_edl,
    extend_to_silence,
    snap_end,
    snap_start,
    split_on_pauses,
)
from agentic_editor.edl import EDL, Clip
from tests.conftest import words_from

WORDS = words_from("so@1.0-1.3 here@1.4-1.8 is@1.9-2.0 the@2.1-2.2 thing@2.3-2.8 next@5.0-5.4")


def test_start_inside_word_moves_back_to_its_onset():
    assert snap_start(1.1, WORDS) == 1.0 - 0.12


def test_start_lead_in_never_reaches_previous_word():
    assert snap_start(1.6, WORDS) == (1.3 + 1.4) / 2
    assert snap_start(1.85, WORDS) == (1.8 + 1.9) / 2


def test_start_in_long_gap_moves_to_next_word():
    assert snap_start(3.9, WORDS) == 5.0 - 0.12


def test_start_far_from_speech_is_left_alone():
    assert snap_start(3.0, WORDS) == 3.0


def test_end_inside_word_extends_to_word_end():
    assert snap_end(2.5, WORDS) == 2.8 + 0.30


def test_end_tail_stops_halfway_to_next_word():
    assert snap_end(1.35, WORDS) == (1.3 + 1.4) / 2


def test_end_extends_to_where_audio_goes_quiet():
    assert extend_to_silence(3.1, [(4.0, 5.0)]) == 4.05
    assert extend_to_silence(3.1, [(8.0, 9.0)]) == 3.1
    assert extend_to_silence(4.5, [(4.0, 5.0)]) == 4.5


def test_split_on_pauses_removes_long_gap():
    pieces = split_on_pauses(Clip(start=0.9, end=5.6), WORDS, max_pause=1.0)
    assert [(p.start, p.end) for p in pieces] == [(0.9, 2.8 + 0.30), (5.0 - 0.12, 5.6)]


def test_clean_edl_snaps_every_clip():
    edl = EDL(clips=[Clip(start=1.6, end=2.5)])
    cleaned = clean_edl(edl, WORDS, silences=[], max_pause=None)
    assert (cleaned.clips[0].start, cleaned.clips[0].end) == (1.35, 3.1)
