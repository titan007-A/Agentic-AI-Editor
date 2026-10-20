from agentic_editor.ffmpeg import escape_filter_path, parse_probe


def probe_data(rotation=None, rate="30000/1001"):
    video = {"codec_type": "video", "width": 1920, "height": 1080, "avg_frame_rate": rate}
    if rotation is not None:
        video["side_data_list"] = [{"rotation": rotation}]
    return {"streams": [video, {"codec_type": "audio"}], "format": {"duration": "12.5"}}


def test_parse_probe_reads_basic_fields():
    info = parse_probe(probe_data())
    assert (info.width, info.height, info.duration) == (1920, 1080, 12.5)
    assert round(info.fps, 3) == 29.97
    assert info.has_audio and info.has_video


def test_rotated_phone_video_reports_display_size():
    info = parse_probe(probe_data(rotation=-90))
    assert (info.width, info.height) == (1080, 1920)


def test_falls_back_to_r_frame_rate():
    data = probe_data(rate="0/0")
    data["streams"][0]["r_frame_rate"] = "25/1"
    assert parse_probe(data).fps == 25


def test_audio_only_file():
    info = parse_probe({"streams": [{"codec_type": "audio"}], "format": {"duration": "3"}})
    assert not info.has_video and info.has_audio


def test_escape_filter_path():
    assert escape_filter_path("C:\\clips\\it's.ass") == r"C\:/clips/it\'s.ass"
