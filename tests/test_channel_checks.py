"""The channel autopilot's checks (channel/checks.py) and the song matching that keeps it
from posting a song twice (research.Posted)."""

import json
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from agentic_editor import caption, research
from agentic_editor.channel import checks
from agentic_editor.presets import Preset
from agentic_editor.render import split as split_render
from agentic_editor.transcript import Word

ROOT = Path(__file__).resolve().parents[1]
FONTS = ROOT / "fonts"
needs_ffmpeg = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")


def vs_preset(**fields) -> Preset:
    """The vocals-vs-instruments preset as the owner's account has it."""
    base = {
        "name": "vocals-vs-instruments-9x16", "label": "Instruments vs vocals",
        "min_duration": 30, "max_duration": 75,
        "split": {"enabled": True, "switch": 0.35, "top": 440, "height": 740,
                  "first_label": "100% instruments", "second_label": "0% instruments"},
        "post_caption": {"enabled": True, "language": "en"},
    }  # fmt: skip
    return Preset.model_validate(base | fields)


# ---- songs posted before --------------------------------------------------------------

CAPTIONS = [
    "🎵 Self Control — Laura Branigan - 1984\n\nIt was her biggest hit in Europe.",
    "Michael Jackson - Billie Jean",
    "Silver Springs_en — Fleetwood Mac - 1977",
    "Step back into the vibrant era of 1985 with Modern Talking's 'Cheri Cheri Lady.' A "
    "classic of 80s dance-pop.",
    "Cyndi_Lauper’s_live_performances_of_“Girls_Just_Want_to_Have_Fun” — Rob",
    "Céline Dion - My Heart Will Go On (Live)",
    "Lonely — Akon",
]


@pytest.mark.parametrize(
    ("song", "artist"),
    [
        ("Self Control", "Laura Branigan"),
        ("Billie Jean", "Michael Jackson"),
        ("Billie Jean (Single Version)", "Michael Jackson"),
        ("Silver Springs", "Fleetwood Mac"),
        ("Cheri Cheri Lady", "Modern Talking"),
        ("Girls Just Want to Have Fun", "Cyndi Lauper"),
        ("My Heart Will Go On", "Celine Dion"),  # no accent
        ("Lonely", "Akon"),
    ],
)
def test_a_song_the_page_posted_in_any_caption_style_counts_as_posted(song, artist):
    assert research.Posted([], CAPTIONS).has(song, artist)


@pytest.mark.parametrize(
    ("song", "artist"),
    [
        ("Lonely", "Lana Del Rey"),  # a one-word title by another singer
        ("Wuthering Heights", "Kate Bush"),
        ("Control", "Janet Jackson"),  # a word of "Self Control"
        ("", "Laura Branigan"),
    ],
)
def test_other_songs_are_not_posted(song, artist):
    assert not research.Posted([], CAPTIONS).has(song, artist)


def test_a_song_waiting_or_posted_by_the_autopilot_counts_whoever_names_it():
    posted = research.Posted([("Gimme! Gimme! Gimme! (A Man After Midnight)", "ABBA")], [])
    assert posted.has("Gimme! Gimme! Gimme!", "ABBA")
    assert posted.has("Gimme Gimme Gimme - Remastered 2001", "")  # no artist: unsure, so yes
    assert posted.has("Gimme! Gimme! Gimme!", "Abba feat. Someone")
    assert not posted.has("Gimme! Gimme! Gimme!", "Cher")
    posted.add("Africa", "Toto")
    assert posted.has("Africa", "TOTO")


def test_the_model_is_told_the_songs_posted_each_once():
    posted = research.Posted([("Africa", "Toto")], ["🎵 Africa — Toto - 1982", CAPTIONS[0]])
    names = posted.names(10)
    assert names[0] == "Africa - Toto"
    assert any("Self Control" in n for n in names)


# ---- before the edit ------------------------------------------------------------------


@pytest.mark.parametrize(
    "title",
    [
        "Freddie Mercury isolated vocals",
        "Whitney Houston - I Will Always Love You (Acapella)",
        "Vocal vs Instrument: Bohemian Rhapsody",
        "Self Control (Instrumental)",
        "Wuthering Heights - slowed + reverb",
        "Jolene cover by a fan",
        "Billie Jean (Lyrics)",
    ],
)
def test_someones_edit_of_the_song_isnt_taken(title):
    check = checks.original(title)
    assert not check.ok and check.detail.startswith("it is an edit of the song")


def test_the_songs_own_video_or_a_performance_is_taken():
    assert checks.original("Kate Bush - Wuthering Heights - Official Music Video").ok
    assert checks.original("Ai Se Eu Te Pego - Michel Teló (Live)").ok
    assert not checks.original("Video by someone", "Self Control 🎤 vocals only\n#80s").ok


def test_a_video_must_be_long_enough_for_the_reel_and_not_a_concert():
    preset = vs_preset()
    assert checks.length(41, preset).ok
    assert not checks.length(35, preset).ok  # 30 s reel + 10 s to choose from
    assert not checks.length(900, preset).ok
    assert not checks.length(None, preset).ok


def test_a_song_posted_before_is_skipped_by_either_name():
    posted = research.Posted([], CAPTIONS)
    assert not checks.not_posted("Self Control", "Laura Branigan", posted, "Shazam").ok
    assert checks.not_posted("Wuthering Heights", "Kate Bush", posted, "Shazam").ok
    unnamed = checks.not_posted("", "", posted, "the research")
    assert unnamed.ok and "by its sound" in unnamed.detail


def test_shazam_must_hear_one_song_at_two_points_at_least():
    song = ("Self Control", "Laura Branigan")
    check, found = checks.one_song([song, song, None])
    assert check.ok and found == song
    check, found = checks.one_song([song, None, None])
    assert not check.ok and found is None and "1 of 3" in check.detail
    check, found = checks.one_song([song, ("Gloria", "Laura Branigan"), song])
    assert not check.ok and "different songs" in check.detail
    assert not checks.one_song([None, None, None])[0].ok
    # A take's note doesn't make another song.
    assert checks.one_song([song, ("Self Control (Remastered)", "Laura Branigan")])[0].ok


def test_the_post_must_name_the_song_shazam_heard_and_its_singer():
    heard = ("Self Control", "Laura Branigan")
    ok = checks.named(heard, "Laura Branigan - Self Control (Official Music Video)", "", "")
    assert ok.ok
    by_caption = checks.named(heard, "Official Music Video", "Self Control by Laura Branigan", "")
    assert by_caption.ok
    wrong = checks.named(heard, "Gloria (Official Video)", "Laura Branigan", "Laura Branigan")
    assert not wrong.ok and "“Self Control”" in wrong.detail
    no_singer = checks.named(heard, "Self Control (live cover)", "", "Some Singer")
    assert not no_singer.ok and "Laura Branigan" in no_singer.detail
    # One of several singers is enough; accents and case don't matter.
    duet = checks.named(("Shallow", "Lady Gaga & Bradley Cooper"), "SHALLOW - Lady Gaga", "", "")
    assert duet.ok


@needs_ffmpeg
def test_shazam_listens_at_three_points_and_its_answers_are_kept(tmp_path):
    video = tmp_path / "song.wav"
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i",
                    "sine=frequency=440:duration=60", str(video)], check=True)  # fmt: skip
    asked = []

    def ask(clip):
        asked.append(clip)
        return {"title": "Self Control (Remastered)", "subtitle": "Laura Branigan"}

    heard = checks.hear(video, tmp_path, 60.0, ask=ask, look=lambda song, artist: None)
    assert len(asked) == 3
    assert heard == [("Self Control", "Laura Branigan")] * 3
    again = checks.hear(video, tmp_path, 60.0, ask=lambda clip: pytest.fail("asked again"))
    assert again == heard
    assert json.loads((tmp_path / "channel-heard.json").read_text())


def test_vocals_that_dont_separate_or_have_nothing_to_take_away_are_skipped(tmp_path):
    assert not checks.vocals(None, False, "vocals don't match the source").ok
    assert "voice alone" in checks.vocals(tmp_path / "v.wav", True).detail


def sung(rate: int, pattern: list[bool], seconds: float = 0.5) -> np.ndarray:
    """A voice: a tone in each `seconds` marked True, near silence otherwise."""
    size = int(rate * seconds)
    t = np.arange(size) / rate
    on = 0.3 * np.sin(2 * np.pi * 440 * t)
    off = 0.0005 * np.sin(2 * np.pi * 440 * t)
    return np.concatenate([on if p else off for p in pattern]).astype(np.float32)


def test_the_singer_must_be_heard_in_enough_of_the_song(tmp_path, monkeypatch):
    rate = 16000
    voices = {
        "much": sung(rate, [True] * 6 + [False] * 4),
        "little": sung(rate, [True] + [False] * 9),
    }
    monkeypatch.setattr(checks.ffmpeg, "read_audio", lambda path, r: voices[path.stem])
    assert checks.vocals(Path("much.wav"), False).ok
    little = checks.vocals(Path("little.wav"), False)
    assert not little.ok and "10%" in little.detail


# ---- after the edit -------------------------------------------------------------------


def test_the_edit_must_have_no_doubts_and_its_caption_nothing_to_check():
    assert checks.confidence(1.0, [], []).ok
    assert not checks.confidence(0.8, ["8s without voice at 3s"], []).ok
    assert not checks.confidence(1.0, [], ["loudness: -18 LUFS"]).ok
    assert checks.caption("🎵 Song — Artist\n\nA fact.", "done", []).ok
    assert not checks.caption("", "failed", ["nothing found"]).ok
    noted = checks.caption("text", "done", ["Wikipedia has no article on it. Check it."])
    assert not noted.ok and "Wikipedia" in noted.detail
    preset = vs_preset()
    assert checks.reel_length(45.0, preset).ok
    assert not checks.reel_length(20.0, preset).ok


def motion(top: list[float], bottom: list[float], **peaks) -> checks.Panels:
    n = len(top)
    return checks.Panels(
        top=np.array(top), bottom=np.array(bottom),
        top_peak=np.array(peaks.get("top_peak", [180.0] * n)),
        bottom_peak=np.array(peaks.get("bottom_peak", [180.0] * n)),
        top_spread=np.full(n, 30.0), bottom_spread=np.full(n, 30.0),
    )  # fmt: skip


def test_the_panels_play_one_after_the_other_and_the_change_is_found():
    playing, held = [3.0] * 30, [0.05] * 30
    p = motion([0.0, *playing, *held], [0.0, *held, *playing])
    at = checks.change_seen(p)
    assert at == 31
    check = checks.panels(p, at)
    assert check.ok, check.detail


def test_a_panel_that_doesnt_hold_or_a_frozen_picture_is_a_doubt():
    playing, held = [3.0] * 30, [0.05] * 30
    both = motion([0.0, *playing, *playing], [0.0, *held, *playing])
    assert "doesn't hold" in checks.panels(both, 31).detail
    frozen = [3.0] * 10 + [0.05] * 15 + [3.0] * 5  # 1.5 s still in the playing panel
    p = motion([0.0, *frozen, *held], [0.0, *held, *playing])
    assert "stands still for 1.5 s" in checks.panels(p, 31).detail


def test_black_while_playing_or_a_blank_held_picture_is_a_doubt():
    playing, held = [3.0] * 30, [0.05] * 30
    black = [180.0] * 10 + [18.0] * 5 + [180.0] * 46
    p = motion([0.0, *playing, *held], [0.0, *held, *playing], top_peak=black)
    assert "0.5 s of black" in checks.panels(p, 31).detail
    # A dark scene with something bright in it (a figure on navy) isn't black.
    night = [180.0] * 10 + [160.0] * 20 + [180.0] * 31
    p = motion([0.0, *playing, *held], [0.0, *held, *playing], top_peak=night)
    assert checks.panels(p, 31).ok
    blank = [180.0] * 31 + [12.0] + [180.0] * 29
    p = motion([0.0, *playing, *held], [0.0, *held, *playing], bottom_peak=blank)
    assert "bottom panel holds a blank picture" in checks.panels(p, 31).detail


def test_the_change_must_fall_between_words_where_the_voice_is_low():
    rate = 16000
    voice = sung(rate, [True] * 8 + [False] + [True] * 11)  # a pause at 4.0-4.5 s
    words = [Word(text="love", start=3.2, end=3.95), Word(text="me", start=4.55, end=5.2)]
    good = checks.switch(4.3, 4.25, words, voice, rate)
    assert good.ok and "between “love” and “me”" in good.detail
    apart = checks.switch(5.0, 4.25, words, voice, rate)
    assert not apart.ok and "picture changes at 5.00" in apart.detail
    mid_word = checks.switch(3.6, 3.6, words, voice, rate)
    assert not mid_word.ok and "mid-word: “love”" in mid_word.detail
    # The words say a pause, but the voice holds a note there.
    held = checks.switch(2.0, 2.0, words, voice, rate)
    assert not held.ok and "mid-note" in held.detail


def test_the_singer_must_be_heard_in_most_of_the_vocals_part():
    rate = 16000
    whole = sung(rate, [True] * 10 + [True] * 4 + [False] * 6)
    assert checks.voice(whole[: 10 * rate // 2], whole, rate).ok
    check = checks.voice(whole[10 * rate // 2 :], whole, rate)
    assert not check.ok and "40%" in check.detail


def test_instruments_left_in_the_vocals_part_keep_playing_between_phrases():
    rate = 16000
    clean = sung(rate, [True, True, False] * 8)  # the singer and silence between phrases
    t = np.arange(len(clean)) / rate
    band = 0.05 * np.sin(2 * np.pi * 70 * t) + 0.02 * np.sin(2 * np.pi * 900 * t)
    check = checks.leak(clean, rate, mix=(clean + 6 * band).astype(np.float32))
    assert check.ok, check.detail
    assert "the instruments part" in check.detail
    # A bass line under the voice: never silent between phrases, and low.
    leaky = checks.leak((clean + band).astype(np.float32), rate)
    assert not leaky.ok and "needs 30" in leaky.detail
    # A voice that never stops isn't taken as clean.
    assert not checks.leak(sung(rate, [True] * 24), rate).ok


def split_reel(
    tmp_path: Path, preset: Preset, at: float, duration: float, labels=True, leaky=False
):
    """A real split reel as the render lays it out: a moving picture played in the top
    panel until `at`, then in the bottom one, each holding its frame while the other plays;
    a low hum with noise (the band) then a tone with silences between its phrases (the
    voice; `leaky`: with the band's hum under it), each at -14 LUFS."""
    frame = preset.size
    layer = split_render.labels_layer(preset.split, frame, FONTS, tmp_path / "labels.png")
    parts = split_render.graph_parts("0:v", "1:v", preset.split, frame, at, duration)
    over = "[split][2:v]overlay=0:0[out]" if labels else "[split]null[out]"
    loud = "loudnorm=I=-14:TP=-1.5:LRA=11"
    audio = (
        f"[3:a]atrim=end={at},asetpts=PTS-STARTPTS,{loud}[a1];"
        "[4:a]volume='if(lt(mod(t,0.9),0.6),1,0)':eval=frame[sung];"
        + (
            "[5:a][sung]amix=inputs=2:weights=0.2 1:normalize=0[voice];"
            if leaky
            else "[sung]anull[voice];"
        )
        + f"[voice]atrim=start={at},asetpts=PTS-STARTPTS,{loud}[a2];"
        "[a1][a2]concat=n=2:v=0:a=1,aresample=48000[aout]"
    )
    dest = tmp_path / "reel.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc2=size={frame[0]}x{frame[1]}:rate=30:duration={duration}",
         "-f", "lavfi", "-i", f"color=c=0x101030:size={frame[0]}x{frame[1]}:rate=30:d={duration}",
         "-loop", "1", "-i", str(layer),
         "-f", "lavfi", "-i", f"anoisesrc=d={duration}:a=0.2,lowpass=f=300",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
         "-f", "lavfi", "-i", f"sine=frequency=70:duration={duration}",
         "-filter_complex", ";".join([*parts, over, audio]),
         "-map", "[out]", "-map", "[aout]", "-t", str(duration),
         "-c:v", "libx264", "-preset", "ultrafast", "-crf", "23", "-pix_fmt", "yuv420p",
         "-c:a", "aac", str(dest)],
        check=True,
    )  # fmt: skip
    return dest


@needs_ffmpeg
def test_a_real_split_reel_passes_its_picture_label_and_loudness_checks(tmp_path):
    preset = vs_preset()
    reel = split_reel(tmp_path, preset, at=3.5, duration=10.0)
    panels = checks.panels_of(checks.read_frames(reel, preset.size), preset)
    at = checks.change_seen(panels)
    assert at / checks.FPS == pytest.approx(3.5, abs=0.15)
    assert checks.panels(panels, at).ok
    frame = checks.grab(reel, 1.5, preset.size)
    label = checks.labels(frame, preset, FONTS)
    assert label.ok, label.detail
    first = checks.cut(reel, 0, 3.45, tmp_path / "a.wav")
    second = checks.cut(reel, 3.55, 6.4, tmp_path / "b.wav")
    loud = checks.loudness(reel, first, second, preset)
    assert loud.ok, loud.detail


@needs_ffmpeg
def test_a_reel_without_its_labels_is_a_doubt(tmp_path):
    preset = vs_preset()
    reel = split_reel(tmp_path, preset, at=3.5, duration=6.0, labels=False)
    label = checks.labels(checks.grab(reel, 1.0, preset.size), preset, FONTS)
    assert not label.ok and "“100% instruments”" in label.detail


# ---- the caption's first line ---------------------------------------------------------


def test_a_songs_caption_gets_the_pages_first_line_without_a_year_it_cant_find():
    template = "🎵 {song} — {artist} - {year}"
    assert caption.first_line(template, "Self Control", "Laura Branigan", "1984") == (
        "🎵 Self Control — Laura Branigan - 1984"
    )
    assert caption.first_line(template, "Self Control", "Laura Branigan", None) == (
        "🎵 Self Control — Laura Branigan"
    )
    assert caption.first_line("{artist} ({year})", "X", "Y", None) == "Y"
