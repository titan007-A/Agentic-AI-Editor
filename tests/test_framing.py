from agentic_editor.analysis.faces import Face
from agentic_editor.analysis.subjects import Subject
from agentic_editor.edl import Clip, Shot
from agentic_editor.framing import (
    View,
    absorb_short,
    check_times,
    crop_width,
    crossing,
    follow_path,
    frame_shots,
    shot_bounds,
    view_times,
    widen_to,
)

FRAME = (1080, 1920)
SOURCE = 1920
FULL = (0, 0, 1920, 1080)
CLIP = Clip(start=10, end=30)


def face_at(t: float, x_px: float, width_px: float = 120):
    return (t, [Face(cx=x_px / SOURCE, cy=0.4, width=width_px / SOURCE, height=0.2, score=0.9)])


def keep(x0: int, x1: int, kind="text") -> Subject:
    return Subject(kind=kind, label=kind, x0=x0, y0=800, x1=x1, y1=900, keep=True)


def frame(samples, faces=(), cuts=(), content=FULL, clip=CLIP):
    return frame_shots(clip, list(cuts), samples, list(faces), content, SOURCE, FRAME)


def test_crop_width_is_a_full_height_9_16_window():
    assert crop_width(FULL, FRAME) == 606


def test_a_head_that_fits_gets_a_fixed_crop_around_it():
    shots = frame([(10.5, [])], faces=[face_at(10.5, 1200)])
    assert len(shots) == 1 and not shots[0].follow and shots[0].width == 606
    assert shots[0].x <= 1200 - 96 and shots[0].x + 606 >= 1200 + 96


def test_a_mic_beside_the_head_is_kept_in_the_same_crop():
    shots = frame([(10.5, [keep(1300, 1450, "microphone")])], faces=[face_at(10.5, 1150)])
    assert shots[0].width == 606
    assert shots[0].x <= 1150 - 96 and shots[0].x + 606 >= 1450


def test_two_people_far_apart_are_both_shown_instead_of_cut():
    samples = [(10.5, [keep(150, 350, "head"), keep(1550, 1750, "head")])]
    (shot,) = frame(samples)
    assert (shot.x, shot.width) == (0, 1920)
    assert "whole picture" in shot.reason


def test_a_banner_sliding_in_only_widens_the_part_of_the_shot_it_is_on():
    times = [10.5 + 4 * i for i in range(10)]
    samples = [(t, [keep(300, 1500)] if t in (14.5, 18.5) else []) for t in times]
    shots = frame(samples, [face_at(t, 1000) for t in times], clip=Clip(start=10, end=50))
    assert [s.width for s in shots] == [1248, 606]  # 1200 px of banner + 24 px margins
    # The wide framing covers the whole gap to the next sample, and the half second of
    # crop before it is folded in rather than flashed.
    assert (shots[0].start, shots[0].end) == (10, 22.5)
    assert shots[0].x <= 300 - 24 and shots[0].x + shots[0].width >= 1500


def test_a_subject_crossing_the_frame_is_followed_not_widened():
    faces = [face_at(t, 300 + (t - 10) * 70) for t in (10.5, 14.5, 18.5, 22.5)]
    shots = frame([(t, []) for t, _ in faces], faces=faces)
    assert len(shots) == 1 and shots[0].follow and shots[0].width == 606


def test_each_cut_is_framed_on_its_own():
    samples = [(10.5, []), (26.5, [keep(150, 350, "head"), keep(1550, 1750, "head")])]
    shots = frame(samples, faces=[face_at(10.5, 900)], cuts=[26.0])
    assert [(s.start, s.end) for s in shots] == [(10, 26.0), (26.0, 30)]
    assert shots[0].width == 606 and shots[1].width == 1920


def test_a_picture_already_vertical_is_shown_whole():
    (shot,) = frame_shots(CLIP, [], [], [], (0, 0, 1080, 1920), 1080, FRAME)
    assert (shot.x, shot.width) == (0, 1080)


def test_a_portrait_picture_in_a_square_frame_is_cropped_top_and_bottom_not_squashed():
    head = (10.5, [Face(cx=0.5, cy=0.3, width=0.2, height=0.12, score=0.9)])
    (shot,) = frame_shots(CLIP, [], [], [head], (0, 0, 1080, 1920), 1080, (1080, 1080), 1920)
    assert (shot.x, shot.width, shot.height) == (0, 1080, 1080)
    # The head (rows 380-714 with its hair) sits inside, with some room above it.
    assert shot.y <= 380 and shot.y + 1080 >= 714 and shot.y > 0


def test_a_taller_phone_picture_is_cropped_to_the_frame_height():
    (shot,) = frame_shots(CLIP, [], [], [], (0, 0, 1080, 2400), 1080, FRAME, 2400)
    assert (shot.width, shot.height, shot.y) == (1080, 1920, 240)


def test_heads_too_far_apart_vertically_show_the_whole_tall_picture():
    faces = [(10.5, [Face(cx=0.5, cy=y, width=0.2, height=0.1, score=0.9) for y in (0.1, 0.9)])]
    (shot,) = frame_shots(CLIP, [], [], faces, (0, 0, 1080, 1920), 1080, (1080, 1080), 1920)
    assert shot.height is None and "whole picture" in shot.reason


def test_black_bars_are_left_out_of_the_window():
    pillarboxed = (240, 0, 1440, 1080)
    (shot,) = frame([(10.5, [])], faces=[face_at(10.5, 300)], content=pillarboxed)
    assert shot.x >= 240


def test_cuts_too_close_together_are_not_reframed():
    assert shot_bounds(CLIP, [10.4, 15.0, 15.5, 29.8]) == [(10, 15.0), (15.0, 30)]


def test_long_shots_are_sampled_every_few_seconds():
    assert view_times(CLIP, [20.0]) == [10.5, 14.5, 18.5, 20.5, 24.5, 28.5]


def test_following_never_drops_a_must_keep_item():
    shot = Shot(start=10, end=20, x=500, width=606, follow=True)
    seen = [View(10.5, (900.0, 1100.0))]
    path = follow_path(shot, seen, [face_at(10.5, 300), face_at(11.0, 300)], FULL, SOURCE, 0.0)
    assert all(x <= 900 and x + 606 >= 1100 for _, x in path)
    assert path[0][0] == 0.5


def test_a_loose_vision_head_box_gives_way_to_the_face_detector():
    loose = Subject(kind="head", label="head", x0=630, y0=0, x1=1265, y1=1080, keep=True)
    (shot,) = frame([(10.5, [loose])], faces=[face_at(10.5, 832, 349)])
    assert shot.width == 606 and not shot.reason.startswith("too wide")


def test_a_head_the_detector_missed_still_counts():
    profile = Subject(kind="head", label="head", x0=1500, y0=100, x1=1700, y1=400, keep=True)
    (shot,) = frame([(10.5, [profile])], faces=[face_at(10.5, 300)])
    assert shot.width > 606


def test_a_safety_margin_alone_never_widens_a_shot():
    shots = frame([(10.5, [keep(700, 1290, "microphone")])])
    assert shots[0].width == 606


def test_widened_shots_share_one_width():
    times = [10.5 + 4 * i for i in range(10)]
    wide = {14.5: [keep(300, 1000)], 30.5: [keep(300, 1300)]}
    samples = [(t, wide.get(t, [])) for t in times]
    shots = frame(
        samples, [face_at(t, 900) for t in times], cuts=[30.0], clip=Clip(start=10, end=50)
    )
    widths = sorted({s.width for s in shots})
    assert widths == [606, 1048]


def test_a_mostly_wide_reel_is_framed_the_same_throughout():
    samples = [(10.5, [keep(200, 1400)]), (16.5, [keep(200, 1400)]), (24.5, [])]
    shots = frame(samples, faces=[face_at(24.5, 900)], cuts=[15.0, 23.0])
    assert {s.width for s in shots} == {1248}
    assert "same framing as the rest of the reel" in shots[-1].reason


def test_crossing_finds_only_what_an_edge_cuts_through():
    spans = [(100.0, 300.0), (450.0, 560.0), (700.0, 900.0), (1050.0, 1200.0)]
    assert crossing(spans, 500, 606) == [(450.0, 560.0), (1050.0, 1200.0)]
    assert crossing([(520.0, 1000.0)], 500, 606) == []


def test_recheck_moments_fall_between_the_samples_of_cropped_shots():
    shots = [Shot(start=10, end=20, x=0, width=606), Shot(start=20, end=21.5, x=0, width=606)]
    assert check_times(shots, [10.5, 14.5, 18.5]) == [(0, 12.5), (0, 16.5), (1, 20.75)]


def test_widening_after_a_recheck_takes_in_what_was_cut():
    shot = Shot(start=10, end=20, x=600, width=606)
    wider = widen_to(shot, (1150.0, 1300.0), FULL, 606)
    assert wider.x <= 600 and wider.x + wider.width >= 1300
    assert wider.width < 1920 and not wider.follow


def test_a_half_second_reframe_is_folded_into_its_neighbour():
    parts = [
        Shot(start=0, end=0.5, x=500, width=606),
        Shot(start=0.5, end=8.5, x=0, width=1920),
        Shot(start=8.5, end=40, x=520, width=606),
    ]
    merged = absorb_short(parts)
    assert [(s.start, s.end, s.width) for s in merged] == [(0, 8.5, 1920), (8.5, 40, 606)]


def test_faces_map_into_output_rows_through_the_framing():
    from agentic_editor.framing import screen_rows

    crop = Shot(start=10, end=20, x=600, width=606)
    wide = Shot(start=20, end=30, x=0, width=1920)
    face = Face(cx=0.47, cy=0.4, width=0.1, height=0.2, score=0.9)
    rows = screen_rows(
        [(12.0, [face]), (25.0, [face])], CLIP, [crop, wide], FULL, (1920, 1080), FRAME, 606
    )
    (t1, (cropped,)), (t2, (padded,)) = rows
    assert (t1, t2) == (2.0, 15.0)
    # Full-height crop: 1080 source rows fill 1920 output rows.
    assert round(cropped[0]) == round((0.4 - 0.17) * 1080 * 1920 / 1080)
    # Whole picture: scaled to 1080 wide and centred, so the face sits in the middle band.
    assert 656 < padded[0] < padded[1] < 1264


def test_a_face_the_detector_lost_for_a_moment_still_beats_a_loose_vision_box():
    loose = Subject(kind="head", label="head", x0=616, y0=0, x1=1242, y1=1080, keep=True)
    # No face sample near 10.5 s, but the same face 1.5 s later.
    (shot,) = frame([(10.5, [loose])], faces=[face_at(12.0, 900, 300)])
    assert shot.width == 606


def test_a_sliver_of_a_head_past_the_edge_is_not_a_cut():
    # A 560 px head span in a 606 px following crop that lags by 20 px.
    assert crossing([(500.0, 1060.0)], 520, 606) == []
    assert crossing([(500.0, 1060.0)], 600, 606) == [(500.0, 1060.0)]


def _text(y0: int, y1: int) -> Subject:
    return Subject(kind="text", label="lyrics", x0=100, y0=y0, x1=980, y1=y1, keep=False)


def test_a_full_picture_loses_its_top_and_bottom_around_the_face():
    from agentic_editor.framing import band_shots

    clip = Clip(start=0, end=10)
    singer = [(5.0, [Face(cx=0.5, cy=0.3, width=0.3, height=0.1, score=0.9)])]  # rows 413-768
    shots = band_shots(clip, [], [], singer, (0, 0, 1080, 1920), 870, 1920)
    assert [(s.x, s.width, s.height) for s in shots] == [(0, 1080, 870)]
    # Room above the head, and the whole head in.
    assert shots[0].y <= 413 - 100 and shots[0].y + 870 >= 768


def test_lyrics_along_the_bottom_of_the_picture_are_cut_off():
    from agentic_editor.framing import band_shots

    clip = Clip(start=0, end=10)
    singer = [(5.0, [Face(cx=0.5, cy=0.5, width=0.2, height=0.15, score=0.9)])]
    # A 1080x866 repost picture from row 526, its lyrics at rows 1290-1340.
    samples = [(5.0, [_text(1290, 1340)])]
    picture = (0, 526, 1080, 866)
    shots = band_shots(clip, [], samples, singer, picture, 900, 1920)
    assert shots[0].y + shots[0].height <= 1290 - 0.025 * 866
    assert shots[0].width == 1080


def test_text_across_a_face_is_left_rather_than_cutting_the_face():
    from agentic_editor.framing import band_shots

    clip = Clip(start=0, end=10)
    singer = [(5.0, [Face(cx=0.5, cy=0.9, width=0.2, height=0.05, score=0.9)])]  # rows 1646-1814
    samples = [(5.0, [_text(1700, 1760)])]
    shots = band_shots(clip, [], samples, singer, (0, 0, 1080, 1920), 1920, 1920)
    assert shots[0].height == 1920  # nothing to take off: the text is on the face


def test_a_missed_cut_splits_the_window_where_the_face_moves():
    from agentic_editor.framing import band_shots

    clip = Clip(start=0, end=10)
    # One "shot" (the cut between two close-ups wasn't found): a face high, then low.
    face = [
        (t, [Face(cx=0.5, cy=0.25 if t < 5 else 0.75, width=0.4, height=0.2, score=0.9)])
        for t in (0.5, 1.5, 2.5, 3.5, 4.5, 5.5, 6.5, 7.5, 8.5, 9.5)
    ]
    shots = band_shots(clip, [], [], face, (0, 0, 1080, 1920), 900, 1920)
    assert len(shots) == 2 and abs(shots[0].end - 5.0) < 1e-6
    for shot, cy in zip(shots, (0.25, 0.75), strict=True):
        face_top, face_bottom = (cy - 0.11) * 1920, (cy + 0.11) * 1920
        assert shot.y <= face_top and shot.y + shot.height >= face_bottom


def test_a_close_up_too_big_with_its_hair_keeps_the_face_whole():
    from agentic_editor.framing import band_shots

    clip = Clip(start=0, end=10)
    # Hair to chin is 1.45 face heights: 1114 rows, more than the band's 900.
    big = [(5.0, [Face(cx=0.5, cy=0.45, width=0.6, height=0.4, score=0.9)])]
    shot = band_shots(clip, [], [], big, (0, 0, 1080, 1920), 900, 1920)[0]
    eyes_to_chin = ((0.45 - 0.2) * 1920, (0.45 + 0.2) * 1920)
    assert shot.y <= eyes_to_chin[0] and shot.y + shot.height >= eyes_to_chin[1]


def test_a_face_that_only_wobbles_keeps_one_steady_window():
    from agentic_editor.framing import band_shots

    clip = Clip(start=0, end=10)
    wobble = [0.45, 0.47, 0.43, 0.46, 0.44, 0.77, 0.45, 0.48, 0.44, 0.46]  # one stray sighting
    face = [
        (0.5 + i, [Face(cx=0.5, cy=cy, width=0.5, height=0.42, score=0.9)])
        for i, cy in enumerate(wobble)
    ]
    shots = band_shots(clip, [], [], face, (0, 0, 1080, 1920), 982, 1920)
    assert len(shots) == 1
    middle = shots[0].y + 491
    assert abs(middle - 0.455 * 1920) < 0.02 * 1920


def _singer(times, cy, height=0.1):
    return [(t, [Face(cx=0.5, cy=cy(t), width=0.2, height=height, score=0.9)]) for t in times]


def test_the_window_stays_put_across_cuts_when_the_face_is_roughly_in_place():
    from agentic_editor.framing import band_shots

    clip = Clip(start=0, end=12)
    # Three camera angles, the face a little higher or lower in each.
    rows = {0: 0.40, 1: 0.43, 2: 0.38}
    face = _singer([0.25 + i * 0.5 for i in range(24)], lambda t: rows[int(t // 4)])
    shots = band_shots(clip, [4.0, 8.0], [], face, (0, 0, 1080, 1920), 900, 1920)
    assert len(shots) == 1 and (shots[0].start, shots[0].end) == (0, 12)
    # The face in the window's upper third.
    assert abs((0.40 * 1920 - shots[0].y) / 900 - 1 / 3) < 0.05


def test_a_moment_too_brief_to_matter_doesnt_move_the_window():
    from agentic_editor.framing import band_shots

    clip = Clip(start=0, end=12)
    # A one-second split screen at 6-7 s puts the face low; the cut there was missed.
    face = _singer([0.25 + i * 0.25 for i in range(47)], lambda t: 0.85 if 6 <= t < 7 else 0.35)
    shots = band_shots(clip, [], [], face, (0, 0, 1080, 1920), 900, 1920)
    assert len(shots) == 1


def test_a_face_out_of_the_window_for_long_moves_it_at_the_cut_without_gliding():
    from agentic_editor.framing import band_shots

    clip = Clip(start=0, end=12)
    face = _singer([0.25 + i * 0.25 for i in range(47)], lambda t: 0.30 if t < 8 else 0.80)
    shots = band_shots(clip, [8.0], [], face, (0, 0, 1080, 1920), 900, 1920)
    assert [(s.start, s.end) for s in shots] == [(0, 8.0), (8.0, 12)]
    assert not shots[1].glide
    top, bottom = (0.80 - 0.055) * 1920, (0.80 + 0.055) * 1920
    assert shots[1].y <= top and shots[1].y + 900 >= bottom


def test_a_move_where_the_source_has_no_cut_glides():
    from agentic_editor.framing import band_shots

    clip = Clip(start=0, end=12)
    face = _singer(
        [0.25 + i * 0.25 for i in range(47)], lambda t: 0.25 if t < 6 else 0.75, height=0.2
    )
    shots = band_shots(clip, [], [], face, (0, 0, 1080, 1920), 900, 1920)
    assert len(shots) == 2 and shots[1].glide
