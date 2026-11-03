from agentic_editor import research, songlist

PAGE = songlist.PAGE_BREAK


def _pdf(*pages: str) -> str:
    return f"\n{PAGE}\n".join(pages)


HEADER = "BEST SONGS | 1960-2019"
SPANISH = _pdf(
    f"{HEADER}\nPage 1\nHow this list was built\nWe ranked songs.\n1960-1969\nTop 100 songs\n"
    "  1.  Gracias a la vida (1966)  -  Violeta Parra\n"
    "Una celebración luminosa de la existencia.\n"
    "  2.  Alfonsina y el mar (1969)  -  Mercedes Sosa\n"
    "Una despedida delicada donde la voz parece caminar\nhacia el mar.",
    f"{HEADER}\nPage 2\n  3.  Somos novios (1968)  -  Armando Manzanero\n"
    "Romanticismo puro y elegante.\n1970-1979\nTop 100 songs\n"
    "  1.  Pedro Navaja (1978)  -  Rubén Blades & Willie Colón\nUna crónica callejera.\n"
    "  2.  Eres tú (1973)  -  Mocedades\nUna balada.",
    f"{HEADER}\nPage 3\n  3.  Gavilán o paloma (1977)  -  José José\nUn clásico.\n"
    "600 songs. Six decades.\nCompiled for discovery.",
)


def test_a_numbered_list_is_read_whole_with_its_sections():
    entries = research.listed(SPANISH)
    assert [(e.section, e.number, e.song, e.singer, e.year) for e in entries] == [
        ("1960-1969", 1, "Gracias a la vida", "Violeta Parra", "1966"),
        ("1960-1969", 2, "Alfonsina y el mar", "Mercedes Sosa", "1969"),
        ("1960-1969", 3, "Somos novios", "Armando Manzanero", "1968"),
        ("1970-1979", 1, "Pedro Navaja", "Rubén Blades & Willie Colón", "1978"),
        ("1970-1979", 2, "Eres tú", "Mocedades", "1973"),
        ("1970-1979", 3, "Gavilán o paloma", "José José", "1977"),
    ]
    # A sentence wrapped onto two lines is one line again; the running header, page
    # numbers, the next section's heading and the PDF's closing words belong to no song.
    assert entries[1].lines == ["Una despedida delicada donde la voz parece caminar hacia el mar."]
    assert entries[2].lines == ["Romanticismo puro y elegante."]
    assert entries[-1].lines == ["Un clásico."]
    assert songlist.sections(entries) == [
        {"name": "1960-1969", "count": 3, "first": 1, "last": 3},
        {"name": "1970-1979", "count": 3, "first": 1, "last": 3},
    ]


def test_the_songs_wanted_get_their_line_their_year_and_their_sections_preset():
    entries = research.listed(SPANISH)
    picked = songlist.chosen(entries, ["1970-1979"], 2, 3, False, "top", {"1970-1979": "p70"})
    assert [(e["song"], e["number"], e["preset"], e["year"]) for e in picked] == [
        ("Eres tú", 2, "p70", "1973"),
        ("Gavilán o paloma", 3, "p70", "1977"),
    ]
    assert picked[0]["top"] == "Una balada." and picked[0]["bottom"] == ""
    below = songlist.chosen(entries, None, 1, None, False, "bottom", {})
    assert len(below) == 6 and below[0]["bottom"].startswith("Una celebración")
    assert all(e["top"] == "" and e["preset"] == "" for e in below)
    nowhere = songlist.chosen(entries, None, 1, 1, False, "none", {})
    assert [(e["top"], e["bottom"]) for e in nowhere] == [("", ""), ("", "")]


def test_a_singer_first_list_can_be_swapped_and_labelled_lines_go_where_they_say():
    text = "\n".join(
        [f"{n}) Singer {n} – Song {n}\nTop text: Line {n} above\nBottom text: Line {n} below"
         for n in range(1, 7)]
    )  # fmt: skip
    entries = research.listed(text)
    assert [(e.song, e.year) for e in entries][:1] == [("Singer 1", "")]
    (first,) = songlist.chosen(entries, None, 1, 1, True, "top", {})
    assert (first["song"], first["singer"]) == ("Song 1", "Singer 1")
    assert (first["top"], first["bottom"]) == ("Line 1 above", "Line 1 below")


def test_a_pdf_that_isnt_a_numbered_list_is_left_to_the_model():
    assert research.listed("Song: Felicità\nTop text: something\nBottom text: other") == []
