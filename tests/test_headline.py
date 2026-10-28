from contextlib import contextmanager

import pytest

from agentic_editor import download, headline
from agentic_editor.presets import Preset, load_presets
from agentic_editor.render import text
from agentic_editor.settings import ROOT

FONT = text.Face([ROOT / "fonts" / "Poppins-SemiBold.ttf"])


def preset(**headline_settings) -> Preset:
    return Preset(
        name="h",
        label="h",
        min_duration=4,
        max_duration=8,
        text_blocks=[
            {
                "text": "{headline}",
                "font": "Poppins-SemiBold",
                "size": 50,
                "anchor": "top",
                "offset": 300,
                "max_width": 820,
            },
            {"text": "FIXED TAGLINE", "size": 50, "anchor": "bottom", "offset": 500},
        ],
        headline=headline_settings,
    )


class Writer:
    """A planner that answers with the headlines given, one per question, naming the song
    given (none for an empty headline)."""

    def __init__(self, *answers, song="Stronger"):
        self.answers = list(answers)
        self.song = song
        self.asked = []

    def chat_json(self, messages, schema, **kwargs):
        self.asked.append(messages)
        answer = self.answers.pop(0)
        return {"song": self.song if answer else "", "artist": "", "headline": answer}


def test_the_notes_can_give_the_headline_and_keep_the_rest_as_instruction():
    given, rest = headline.from_notes(
        "Titular: Así fue como se creó “Stronger” de Daft Punk\nstart where the beat drops"
    )
    assert given == "Así fue como se creó “Stronger” de Daft Punk"
    assert rest == "start where the beat drops"
    assert headline.from_notes("headline: Así grabó Queen “Bohemian Rhapsody”") == (
        "Así grabó Queen “Bohemian Rhapsody”",
        None,
    )
    assert headline.from_notes("keep it under 30 seconds") == (None, "keep it under 30 seconds")
    assert headline.from_notes(None) == (None, None)


def test_only_presets_with_the_placeholder_want_a_headline():
    assert headline.wanted(preset())
    assert not headline.wanted(load_presets(ROOT / "presets")["vocal-music-it-9x16"])


def test_a_long_headline_is_wrapped_into_even_lines_that_fit():
    line = "ASÍ SONARÍA “SHAPE OF YOU” DE ED SHEERAN EN VERSIÓN SWING JAZZ"
    wrapped = headline.lines(line, FONT, 50, 820).split("\n")
    # As the page itself broke it: the quoted name kept whole, the lines as even as that
    # allows.
    assert wrapped == ["ASÍ SONARÍA “SHAPE OF YOU”", "DE ED SHEERAN EN", "VERSIÓN SWING JAZZ"]
    assert all(FONT.length(row, 50) <= 820 for row in wrapped)
    assert headline.lines("ASÍ GRABÓ QUEEN", FONT, 50, 820) == "ASÍ GRABÓ QUEEN"


def test_filling_puts_the_headline_in_capitals_and_leaves_other_blocks_alone():
    filled = headline.fill(
        preset(uppercase=True),
        "Así sonaría “Shape of You” de Ed Sheeran en versión swing jazz",
        lambda name, *texts: FONT,
    )
    assert filled.text_blocks[0].text.replace("\n", " ") == (
        "ASÍ SONARÍA “SHAPE OF YOU” DE ED SHEERAN EN VERSIÓN SWING JAZZ"
    )
    assert filled.text_blocks[1].text == "FIXED TAGLINE"


def test_the_planner_writes_it_from_the_title_and_description():
    source = download.Source(
        path=ROOT, title="Video by someone", uploader="someone", description="Shape of You, swing"
    )
    llm = Writer("Shape of You en swing", song="Shape of You")
    assert headline.write(llm, source, preset(hint="In Spanish")) == "“Shape of You” en swing"
    prompt = llm.asked[0][0]["content"]
    assert "Shape of You, swing" in prompt and "In Spanish" in prompt


def test_a_headline_too_long_is_asked_again_shorter():
    llm = Writer("x" * 80, "short one")
    source = download.Source(path=ROOT, title="Stronger - Daft Punk")
    assert headline.write(llm, source, preset(max_chars=40)) == "short one"
    assert "at most 40" in llm.asked[1][-1]["content"]


def test_without_a_song_to_name_it_asks_for_the_headline_in_the_notes():
    source = download.Source(path=ROOT, title="IMG_4379")
    with pytest.raises(headline.NoHeadline, match="headline: "):
        headline.write(Writer(""), source, preset())


class Router:
    def __init__(self, llm):
        self.llm = llm
        self.opened = 0

    @contextmanager
    def planner(self):
        self.opened += 1
        yield self.llm


def run_headlined(tmp_path, llm, given=None, presets=None):
    from agentic_editor import pipeline

    presets = presets or [preset(uppercase=True)]
    settings = type("S", (), {"fonts_dir": ROOT / "fonts", "models_dir": tmp_path})
    decisions = {p.name: ([], []) for p in presets}
    failures = []
    router = Router(llm)
    source = download.Source(path=tmp_path, title="Stronger - Daft Punk")
    done = pipeline.headlined(
        router, source, presets, given, decisions, lambda what, exc: failures.append(what),
        settings, lambda *args: None,
    )  # fmt: skip
    return done, decisions, failures, router


def test_the_notes_headline_is_used_without_asking_the_planner(tmp_path):
    done, decisions, failures, router = run_headlined(
        tmp_path, Writer(), given="Así fue como se creó “Stronger” de Daft Punk"
    )
    assert done[0].text_blocks[0].text.replace("\n", " ") == (
        "ASÍ FUE COMO SE CREÓ “STRONGER” DE DAFT PUNK"
    )
    assert decisions["h"][0] == [
        "headline from your notes: Así fue como se creó “Stronger” de Daft Punk"
    ]
    assert router.opened == 0 and not failures


def test_the_planner_writes_the_headline_when_the_notes_dont(tmp_path):
    done, decisions, failures, router = run_headlined(
        tmp_path, Writer("Así grabó Daft Punk 'Stronger'")
    )
    assert not failures
    assert done[0].text_blocks[0].text.replace("\n", " ") == "ASÍ GRABÓ DAFT PUNK “STRONGER”"
    assert decisions["h"][0] == ["headline written from the title: Así grabó Daft Punk “Stronger”"]
    assert router.opened == 1


def test_a_preset_without_a_headline_is_dropped_with_the_reason(tmp_path):
    plain = Preset(name="p", label="p", min_duration=4, max_duration=8)
    done, _, failures, _ = run_headlined(tmp_path, Writer(""), presets=[preset(), plain])
    assert [p.name for p in done] == ["p"]
    assert failures == ["h"]


@pytest.mark.parametrize(
    ("written", "tidied"),
    [
        (
            'Así grabó Daft Punk "Stronger" en sesión de estudio.',
            "Así grabó Daft Punk “Stronger” en sesión de estudio",
        ),
        (
            'Así empezó Shakira antes de "Pies Descalzos"',
            "Así empezó Shakira antes de “Pies Descalzos”",
        ),
        ('"Así grabó Queen su mejor canción"', "Así grabó Queen su mejor canción"),
        (
            "Así fue como se creó “Stronger” de Daft Punk",
            "Así fue como se creó “Stronger” de Daft Punk",
        ),
        ('Un "solo" quote', "Un “solo” quote"),
    ],
)
def test_the_written_headline_is_tidied(written, tidied):
    assert headline.tidy(written) == tidied


def test_the_song_is_put_in_quotes_however_the_model_wrote_it():
    assert headline.quote("Así grabó Daft Punk 'Stronger' en 2001", "Stronger") == (
        "Así grabó Daft Punk “Stronger” en 2001"
    )
    assert headline.quote("Así sonaría Shape of You de Ed Sheeran", "Shape of You") == (
        "Así sonaría “Shape of You” de Ed Sheeran"
    )
    assert headline.quote("Así grabó Queen “Bohemian Rhapsody”", "Bohemian Rhapsody") == (
        "Así grabó Queen “Bohemian Rhapsody”"
    )
    assert headline.quote("Así empezó Shakira", "Pies Descalzos") == "Así empezó Shakira"
