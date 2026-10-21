from agentic_editor.benchmark import Result, realtime, report


def test_realtime_factor():
    assert realtime(240, 24) == "10.0x realtime"
    assert realtime(10, 0) == "instant"


def test_report_aligns_stages():
    text = report(
        [Result("transcription", 23.4, "10.3x realtime"), Result("render", 31.0, "0.6x")],
        "Breathe",
        241.2,
    )
    lines = text.splitlines()
    assert lines[0] == "Breathe (241s)"
    assert lines[1].index("23.4s") == lines[2].index("31.0s")
