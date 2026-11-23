"""The World map style: the map's data, the writer's tour, timing, captions and the studio."""

import hashlib
import json
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from agentic_editor.animate import images, maps, mapwriter, planner, sound, studio, voice
from agentic_editor.animate.plan import Plan, map_problems, problems, tidy
from agentic_editor.animate.timeline import build, player_data
from agentic_editor.db import Animation
from agentic_editor.settings import Settings
from tests.test_animate import Router, Writer, fake_speak, spoken, studio_env, voices_for

# ---- a small world -------------------------------------------------------------------------


def square(x0, y0, x1, y1):
    return [[x0, y0], [x1, y0], [x1, y1], [x0, y1], [x0, y0]]


def feature(code, name, geometry, label, **props):
    return {
        "type": "Feature",
        "properties": {
            "ADM0_A3": code,
            "NAME": name,
            "ADMIN": name,
            "LABEL_X": label[0],
            "LABEL_Y": label[1],
            "LABELRANK": 2,
            **props,
        },
        "geometry": geometry,
    }


WORLD = {
    "type": "FeatureCollection",
    "features": [
        # The mainland, an island just off it, and a big far-off territory.
        feature(
            "USA",
            "United States of America",
            {
                "type": "MultiPolygon",
                "coordinates": [
                    [square(-125, 25, -67, 49)],
                    [square(-66.5, 40, -64, 42)],
                    [square(-170, 55, -140, 70)],
                ],
            },
            (-97, 39),
            ABBREV="U.S.A.",
            NAME_LONG="United States",
        ),
        feature(
            "GBR",
            "United Kingdom",
            {"type": "Polygon", "coordinates": [square(-6, 50, 2, 58)]},
            (-2, 54),
            ABBREV="U.K.",
        ),
        feature(
            "NLD",
            "Netherlands",
            {"type": "Polygon", "coordinates": [square(3.5, 51, 7, 53.5)]},
            (5.5, 52.2),
        ),
        feature(
            "FRA",
            "France",
            {"type": "Polygon", "coordinates": [square(-4, 43, 7.5, 51)]},
            (2.5, 46.5),
        ),
        feature(
            "JPN",
            "Japan",
            {"type": "Polygon", "coordinates": [square(130, 31, 142, 41)]},
            (138, 36),
        ),
        feature(
            "ITA",
            "Italy",
            {"type": "Polygon", "coordinates": [square(7, 37, 18, 46)]},
            (12.5, 42.5),
        ),
    ],
}


@pytest.fixture
def world(tmp_path) -> Path:
    """A models folder with the small world's table (no tiles)."""
    models = tmp_path / "models"
    source = tmp_path / "world.geojson"
    source.write_text(json.dumps(WORLD))
    maps.table_path(models).parent.mkdir(parents=True)
    maps.build_table(source, maps.table_path(models))
    return models


def test_names_people_say_find_their_country(world):
    table = maps.table(world)
    assert maps.find("The United States", table) == "USA"
    assert maps.find("the USA", table) == "USA"
    assert maps.find("America", table) == "USA"
    assert maps.find("the UK", table) == "GBR"
    assert maps.find("England", table) == "GBR"
    assert maps.find("Holland", table) == "NLD"
    assert maps.find("the Netherlands", table) == "NLD"
    assert maps.find("FRANCE", table) == "FRA"
    assert maps.find("the Republic of France", table) == "FRA"
    assert maps.find("Narnia", table) is None
    assert maps.find("", table) is None


def test_a_country_is_its_main_landmass_with_what_lies_close(world):
    usa = maps.table(world)["USA"]
    assert usa["main"] == [-125, 25, -64, 49]  # the island off the coast, not the far territory
    assert usa["short"] == "USA" and usa["name"] == "United States of America"
    assert len(usa["rings"]) == 3  # every part is still drawn
    view = maps.views(usa)
    assert view["fit"][:2] == [-94.5, 37.0]
    assert maps.CLOSE_MIN <= view["close"] <= maps.CLOSE_MAX
    assert view["wide"] > view["fit"][2] > view["close"]


def test_outlines_are_simplified_within_their_tolerance():
    t = np.linspace(0, 2 * np.pi, 2000)
    ring = np.stack([np.cos(t) * 10, np.sin(t) * 10], axis=1)
    thin = maps.simplify(ring, 0.05)
    assert 20 < len(thin) < 400
    assert (thin[0] == ring[0]).all() and (thin[-1] == ring[-1]).all()
    radius = np.hypot(thin[:, 0], thin[:, 1])
    assert np.all(np.abs(radius - 10) < 1e-9)  # kept points are the ring's own


def test_cards_go_inside_the_country_spread_apart(world):
    usa = maps.table(world)["USA"]
    close = maps.views(usa)["close"]
    spots = maps.spots(usa, 5, close, seed=3)
    assert len(spots) == 5
    rings = [np.asarray(r, dtype=float).reshape(-1, 2) for r in usa["rings"]]
    assert maps.inside(np.asarray(spots), rings).all()
    squash = np.cos(np.radians(37))
    for i, a in enumerate(spots):
        for b in spots[i + 1 :]:
            gap = np.hypot((a[0] - b[0]) * squash, a[1] - b[1])
            assert gap >= close * maps.SPACING * 0.45 - 1e-6
    assert maps.spots(usa, 5, close, seed=3) == spots  # the same every time
    # A tiny country with many things still gets a spot for each.
    assert len(maps.spots(maps.table(world)["NLD"], 5, 4.0)) == 5


def test_countries_take_the_palette_in_turn_and_keep_their_colour():
    got = maps.colours([None, "USA", "CAN", "USA", "FRA", None])
    assert got[0] == "" and got[-1] == ""
    assert got[1] == maps.PALETTE[0] and got[2] == maps.PALETTE[1]
    assert got[3] == got[1] and got[4] == maps.PALETTE[2]


def test_the_ocean_turns_teal_and_the_land_darker():
    pixels = np.array([[maps.OCEAN_IN, (200, 180, 120), (11, 10, 52)]], dtype=np.uint8)
    toned = maps.tone(pixels)
    assert tuple(toned[0, 0]) == maps.OCEAN_OUT
    assert np.abs(toned[0, 2].astype(int) - maps.OCEAN_OUT).max() <= 3
    land = toned[0, 1].astype(int)
    assert land.sum() < 500 * maps.LAND_GAIN + 3 and land[0] > land[2]


def test_tiles_are_cut_at_every_level(tmp_path, monkeypatch):
    monkeypatch.setattr(maps, "LEVELS", 2)
    source = tmp_path / "earth.jpg"
    Image.new("RGB", (6000, 3000), maps.OCEAN_IN).save(source)
    dest = tmp_path / "tiles"
    maps.build_tiles(source, dest)
    assert (dest / "done").is_file()
    assert len(list((dest / "0").glob("*.jpg"))) == 18
    assert len(list((dest / "1").glob("*.jpg"))) == 72
    with Image.open(dest / "1" / "11_5.jpg") as tile:
        assert tile.size == (maps.TILE, maps.TILE)
        r, g, _ = tile.getpixel((250, 250))
        assert abs(g - maps.OCEAN_OUT[1]) < 6 and r < 8


def test_the_map_is_fetched_once_and_checked(tmp_path, monkeypatch):
    monkeypatch.setattr(maps, "LEVELS", 1)
    earth = tmp_path / "earth-src.jpg"
    Image.new("RGB", (3000, 1500), (20, 90, 30)).save(earth)
    files = {"earth.jpg": earth.read_bytes(), "countries.geojson": json.dumps(WORLD).encode()}
    monkeypatch.setattr(
        maps,
        "SOURCES",
        {k: (f"https://example.org/{k}", hashlib.sha256(v).hexdigest()) for k, v in files.items()},
    )
    asked = []

    def download(url, dest):
        asked.append(url)
        dest.write_bytes(files[url.rsplit("/", 1)[1]])

    models = tmp_path / "models"
    maps.fetch(models, download=download)
    assert maps.ready(models) and len(asked) == 2
    assert maps.find("Holland", maps.table(models)) == "NLD"
    maps.fetch(models, download=download)
    assert len(asked) == 2  # never again

    def wrong(url, dest):
        dest.write_bytes(b"not it")

    with pytest.raises(maps.MapError, match="wrong contents"):
        maps.fetch(tmp_path / "other", download=wrong)


# ---- a World map reel ---------------------------------------------------------------------


def country(name, narration, *items):
    return {
        "country": name,
        "narration": narration,
        "items": [{"name": n, "emoji": "🍰", "picture": f"a plate of {n.lower()}"} for n in items],
    }


TOUR = [
    country("The United States", "The United States has apple pie, brownies, and cheesecake.",
            "apple pie", "brownies", "cheesecake"),
    country("the UK", "The UK has sticky toffee pudding and scones.", "sticky toffee pudding",
            "scones"),
    country("France", "France has macarons, which began in Italy, and crème brûlée.", "macarons",
            "crème brûlée"),
    country("Italy", "Italy has tiramisu and gelato.", "tiramisu", "gelato"),
    country("Holland", "Holland has stroopwafels.", "stroopwafels"),
    country("Japan", "Japan has mochi and dorayaki.", "mochi", "dorayaki"),
]  # fmt: skip


class MapModel(Writer):
    """The planner model for World map reels: the writer's replies in turn, and the line
    by line check (mapwriter.vet) answered with `wrong` (the lines it calls wrong, the same
    each time, or one list per check)."""

    def __init__(self, *replies, wrong=()):
        super().__init__(*replies)
        self.wrong = list(wrong)
        self.vetted: list[list[dict]] = []

    def chat_json(self, messages, schema, temperature=0.3):
        if schema is mapwriter.VET_SCHEMA:
            self.vetted.append(messages)
            each = self.wrong and isinstance(self.wrong[0], list)
            wrong = (self.wrong.pop(0) if self.wrong else []) if each else self.wrong
            count = messages[0]["content"].count("\n") + 1
            return {
                "lines": [
                    {"n": n, "is_one": n not in wrong, "from_there": True} for n in range(1, count)
                ]
            }
        return super().chat_json(messages, schema, temperature)


def map_reply(countries=None, **changes):
    reply = {
        "title": "Most Famous Desserts In Every Country",
        "hook": "The most famous desserts from every country.",
        "headline": "Most famous desserts in every country",
        "highlight": "desserts",
        "countries": countries if countries is not None else TOUR,
        "caption": "Which one is your favourite? #maps #desserts",
    }
    reply.update(changes)
    return reply


def map_plan(**changes) -> Plan:
    return mapwriter.to_plan(map_reply(**changes), [], mapwriter.VOICE)


def test_the_writers_answer_becomes_a_hook_and_a_scene_a_country():
    plan = map_plan()
    assert plan.style == "map" and plan.mood == "upbeat" and plan.kind == "facts"
    assert plan.voice == "am_michael" and plan.cta == mapwriter.CTA
    first, usa = plan.scenes[0], plan.scenes[1]
    assert first.visual.kind == "title" and first.visual.highlight == "desserts"
    assert first.narration == "The most famous desserts from every country."
    assert usa.visual.kind == "country" and usa.visual.text == "The United States"
    assert [i.text for i in usa.visual.items] == ["apple pie", "brownies", "cheesecake"]
    assert usa.visual.items[0].art == "a plate of apple pie"
    assert len(plan.scenes) == 7
    assert mapwriter.to_plan(mapwriter.to_reply(plan), [], plan.voice) == plan


def test_a_tour_must_name_each_country_once_and_every_card():
    assert map_problems(map_plan()) == []
    tour = [dict(c) for c in TOUR]
    tour[1] = country("the UK", "Britain loves scones.", "scones", "trifle")
    tour[3] = dict(TOUR[2])
    found = map_problems(map_plan(countries=tour[:5]))
    assert "scene 3 (the UK): the narration must name the UK" in found
    assert "scene 3 (the UK): say trifle in the narration" in found
    assert "scene 5 (France): France comes twice; each country once" in found
    assert any(
        "at least 5 countries, this has 4" in f for f in map_problems(map_plan(countries=TOUR[:4]))
    )
    # The checks are part of the plan's problems for World map reels only.
    assert map_problems(map_plan(countries=tour[:5]))[0] in problems(map_plan(countries=tour[:5]))


def test_a_draft_is_sent_back_once_for_a_country_off_the_map(world):
    table = maps.table(world)

    def known(name):
        return maps.find(name, table) is not None

    bad = [*TOUR[:5], country("Atlantis", "Atlantis has ambrosia.", "ambrosia")]
    writer = MapModel(map_reply(countries=bad), map_reply())
    brief = planner.Brief("Most famous desserts from every country", style="map", length="short")
    plan, left = planner.draft(writer, brief, [], known)
    assert len(writer.asked) == 2 and left == []
    fix = writer.asked[1][-1]["content"]
    assert "'Atlantis' isn't a country on the map" in fix
    assert "Topic: Most famous desserts from every country" in writer.asked[0][1]["content"]
    assert writer.asked[0][0]["content"] == mapwriter.GUIDE
    assert plan.style == "map" and [s.visual.text for s in plan.scenes][-1] == "Japan"


def test_a_change_keeps_the_card_pictures_that_still_fit():
    plan = map_plan()
    scenes = [s.model_dump() for s in plan.scenes]
    scenes[1]["visual"]["items"][0]["image"] = "a" * 24 + ".png"
    scenes[1]["visual"]["items"][1]["image"] = "b" * 24 + ".png"
    plan = tidy(Plan.model_validate({**plan.model_dump(), "scenes": scenes}))
    changed = [dict(c) for c in TOUR]
    changed[0] = country("The United States", "The United States has apple pie and cookies.",
                         "apple pie", "cookies")  # fmt: skip
    writer = Writer(map_reply(countries=changed))
    brief = planner.Brief("desserts", style="map", length="short")
    out, left = planner.change(writer, plan, "swap brownies for cookies", brief)
    assert left == []
    items = out.scenes[1].visual.items
    assert items[0].image == "a" * 24 + ".png" and items[1].image == ""
    assert "swap brownies for cookies" in writer.asked[0][1]["content"]
    assert out.style == "map"
    # One country written again: its scene only, the same shape.
    again = Writer(country("Italy", "Italy has panna cotta and cannoli.", "panna cotta", "cannoli"))
    redone = planner.redo_scene(again, out, 4)
    assert redone.scenes[4].visual.text == "Italy"
    assert [i.text for i in redone.scenes[4].visual.items] == ["panna cotta", "cannoli"]
    assert redone.scenes[1] == out.scenes[1]


def test_ideas_leave_out_topics_made_and_the_autopilot_takes_them_in_turn():
    made = [mapwriter.IDEAS[0].upper(), mapwriter.IDEAS[2]]
    got = mapwriter.ideas(made, count=50, seed=1)
    assert mapwriter.IDEAS[0] not in got and mapwriter.IDEAS[2] not in got
    assert len(got) == len(mapwriter.IDEAS) - 2
    assert mapwriter.next_topic(None, "", made) == mapwriter.IDEAS[1]
    writer = Writer({"topics": [mapwriter.IDEAS[0], "Most famous kites from every country"]})
    assert (
        mapwriter.next_topic(writer, "fun", list(mapwriter.IDEAS))
        == "Most famous kites from every country"
    )


def test_cards_pop_as_their_names_are_said_and_captions_come_a_word_at_a_time():
    plan = map_plan()
    tl = build(plan, voices_for(plan))
    usa = tl.scenes[1]
    words = {w.text.strip(",.").lower(): w.start for w in usa.words}
    assert usa.kind == "country"
    assert usa.beats[0] == pytest.approx(words["the"] - 0.05)
    assert usa.beats[1:] == [words["apple"], words["brownies"], words["cheesecake"]]
    assert usa.end - usa.voice_end >= 0.35 + 0.45 - 1e-9  # room for the pull back
    shown = [" ".join(w.text for w in c.words) for c in tl.captions]
    assert "The United States" in shown and "apple pie," in shown and "has" in shown
    assert "sticky toffee pudding" in shown and "crème brûlée." in shown
    first = [c for c in tl.captions if c.start < usa.start]
    assert [len(c.words) for c in first] == [1] * 7  # the hook word by word
    for a, b in zip(tl.captions, tl.captions[1:], strict=False):
        assert a.end <= b.start + 1e-9  # one caption at a time
    pops = [c.t for c in tl.cues if c.sound == "pop"]
    assert all(any(abs(p - b) < 1e-9 for p in pops) for b in usa.beats[1:])


def test_a_number_stays_with_what_it_counts():
    plan = map_plan()
    scenes = [s.model_dump() for s in plan.scenes]
    scenes[1]["narration"] = (
        "The United States has apple pie, which won 7 Oscars, brownies, and cheesecake."
    )
    plan = tidy(Plan.model_validate({**plan.model_dump(), "scenes": scenes}))
    tl = build(plan, voices_for(plan))
    shown = [" ".join(w.text for w in c.words) for c in tl.captions]
    assert "7 Oscars," in shown and "which" in shown


def test_the_upbeat_bed_is_a_little_louder_under_the_voice():
    quiet = sound.music("curious", 6.0)
    upbeat = sound.music("upbeat", 6.0)
    rms = lambda x: 20 * np.log10(np.sqrt(np.mean(x**2)))  # noqa: E731
    assert rms(upbeat) == pytest.approx(-28.0, abs=0.5)
    assert rms(upbeat) > rms(quiet) + 2


def test_only_world_map_reels_get_the_map(world):
    plan = map_plan()
    data = maps.attach(player_data(plan, build(plan, voices_for(plan))), plan, world)
    m = data["map"]
    assert m["scenes"][0] is None and m["scenes"][-1] is None  # the hook and the ending
    codes = [s["code"] for s in m["scenes"][1:-1]]
    assert codes == ["USA", "GBR", "FRA", "ITA", "NLD", "JPN"]
    usa = m["scenes"][1]
    assert usa["color"] == maps.PALETTE[0] and len(usa["spots"]) == 3
    assert set(m["countries"]) == set(codes) and m["countries"]["USA"]["short"] == "USA"
    assert m["tiles"] is False and m["version"] == maps.VERSION
    other = plan.model_copy(update={"style": "glow"})
    plain = player_data(other, build(other, voices_for(other)))
    assert maps.attach(plain, other, world) is plain


def test_a_country_off_the_map_is_noted(world):
    tour = [*TOUR[:5], country("Wakanda", "Wakanda has vibranium cake.", "vibranium cake")]
    plan = map_plan(countries=tour)
    assert maps.missing(plan, world) == ["Wakanda"]
    data = maps.attach(player_data(plan, build(plan, voices_for(plan))), plan, world)
    assert data["map"]["scenes"][6] is None
    assert maps.missing(plan.model_copy(update={"style": "glow"}), world) == []


# ---- the studio ------------------------------------------------------------------------------


def map_env(tmp_path, world):
    settings, Session = studio_env(tmp_path)
    return settings.model_copy(update={"models_dir": world}), Session


def test_a_world_map_reel_is_written_voiced_and_mapped(tmp_path, world, monkeypatch):
    settings, Session = map_env(tmp_path, world)
    monkeypatch.setattr(planner, "gather", lambda *a, **k: [])
    monkeypatch.setattr(planner, "subject", lambda llm, idea: ("", ""))
    monkeypatch.setattr(maps, "fetch", lambda *a, **k: pytest.fail("the map is here already"))
    monkeypatch.setattr(maps, "ready", lambda models_dir: True)
    tour = [*TOUR, country("Atlantis", "Atlantis has ambrosia.", "ambrosia")]
    model = MapModel(map_reply(countries=tour), map_reply(countries=tour))
    with Session() as db:
        db.add(Animation(id="m1", owner_id="u1", idea="desserts", style="map", images=False,
                         task={"op": "draft"}))  # fmt: skip
        db.commit()
    studio.run_task("m1", Session, settings, Router(model), speak=fake_speak)
    with Session() as db:
        a = db.get(Animation, "m1")
        assert a.status == "ready", a.error
        assert a.plan["style"] == "map" and a.plan["voice"] == "am_michael"
        assert a.player["map"]["scenes"][1]["code"] == "USA"
        assert a.player["map"]["tiles"] is True
        assert any("isn't a country on the map" in n for n in a.notes)
        assert "not on the map (shown without flying there): Atlantis" in a.notes
        assert a.player["captions"][0]["words"][0]["text"] == "The"


def test_card_pictures_are_painted_alone_on_white_and_dropped_when_off(tmp_path, world):
    settings, Session = map_env(tmp_path, world)
    plan = map_plan()
    asked = []

    def paint(pictures, style, settings, progress, size=images.SIZE):
        asked.append((pictures, style, size))
        return [images.name_of(p.prompt, p.seed, "m", size) for p in pictures]

    painted, notes = studio.paint_plan(plan, "m2", Session, settings, paint)
    ((pictures, style, size),) = asked
    assert style == "map" and size == images.CARD and notes == []
    assert len(pictures) == sum(len(s.visual.items) for s in plan.scenes)
    assert pictures[0].prompt == "a plate of apple pie" and not pictures[0].empty
    assert "isolated on a plain pure white background" in images.full_prompt(pictures[0], "map")
    usa = painted.scenes[1].visual.items
    assert all(i.image.endswith(".png") for i in usa)
    assert all(not s.visual.image for s in painted.scenes)  # no painting behind a scene
    assert all(not i.image for s in studio.without_pictures(painted).scenes for i in s.visual.items)
    _, notes = studio.paint_plan(plan, "m2", Session, settings)
    assert notes == [
        "card pictures need the image model (installed on the Mac); the cards show emoji"
    ]


def test_a_map_autopilot_needs_no_niche(tmp_path, world, monkeypatch):
    from datetime import datetime

    from agentic_editor.animate import schedule
    from agentic_editor.db import AnimationAutopilot, Setting, User

    settings, Session = map_env(tmp_path, world)
    with Session() as db:
        db.add(User(id="u1", username="boss", password_hash="x", role="admin"))
        db.add(Setting(key="feature:animation_studio", value=json.dumps(["boss"])))
        db.add(AnimationAutopilot(id="p1", owner_id="u1", style="map", niche="", topics=[],
                                  hour=0, active=True, length="short"))  # fmt: skip
        db.commit()
    started = schedule.start_due(Session, datetime(2026, 11, 23, 12), queue=lambda i: None)
    assert len(started) == 1
    monkeypatch.setattr(planner, "gather", lambda *a, **k: [])
    monkeypatch.setattr(planner, "subject", lambda llm, idea: ("", ""))
    monkeypatch.setattr(planner, "fact_check", lambda *a: [])
    monkeypatch.setattr(schedule, "render", lambda *a: None)
    monkeypatch.setattr(maps, "fetch", lambda *a, **k: pytest.fail("no downloads in tests"))
    monkeypatch.setattr(maps, "ready", lambda models_dir: True)
    studio.run_task(
        started[0], Session, settings, Router(MapModel(map_reply(), map_reply())), speak=fake_speak
    )
    with Session() as db:
        a = db.get(Animation, started[0])
        assert a.status == "ready", a.error
        assert a.idea == mapwriter.IDEAS[0]
        assert db.get(AnimationAutopilot, "p1").used == [mapwriter.IDEAS[0]]


def test_spoken_helper_matches_kokoro_shape():
    s = spoken("Japan has mochi.")
    assert [w.text for w in s.words] == ["Japan", "has", "mochi."]


def test_settings_default_models_folder_holds_the_map():
    assert maps.folder(Settings().models_dir).name == "maps"


def test_only_the_short_facts_of_a_tour_are_fact_checked():
    tour = [dict(c) for c in TOUR]
    tour[0] = country(
        "The United States",
        "The United States has Mickey Mouse, whose first version became public domain in 2024, "
        "Tom and Jerry, who won seven Oscars, and Bugs Bunny.",
        "Mickey Mouse", "Tom and Jerry", "Bugs Bunny",
    )  # fmt: skip
    plan = map_plan(countries=tour)
    assert mapwriter.asides(plan) == [
        "The United States, Mickey Mouse: whose first version became public domain in 2024",
        "The United States, Tom and Jerry: who won seven Oscars",
        "France, macarons: which began in Italy",
    ]
    writer = Writer({"unsupported": ["who won seven Oscars"]})
    found = [planner.Found("Cartoons", "https://example.org", "Tom and Jerry won seven Oscars.")]
    assert planner.fact_check(writer, plan, found) == ["who won seven Oscars"]
    asked = writer.asked[0][0]["content"]
    assert "1. The United States, Mickey Mouse: whose first" in asked
    assert "Bugs Bunny" not in asked  # the names themselves aren't claims
    assert planner.fact_check(writer, plan, []) == [
        f"no source to check: {c}" for c in mapwriter.asides(plan)
    ]
    plain = map_plan(countries=[c for c in TOUR if c["country"] != "France"])
    assert planner.fact_check(None, plain, []) == []  # names only: nothing to check


def test_a_tour_is_said_the_reference_way_whatever_the_writer_wrote():
    wordy = [
        country(
            "The United States",
            "The United States has apple pie, a symbol of home comfort, brownies, which were "
            "first baked in Chicago, and cheesecake, a creamy cake.",
            "apple pie", "brownies", "cheesecake",
        ),
        country("Canada", "In Canada people love butter tarts and Nanaimo bars.", "butter tarts",
                "Nanaimo bars"),
        *TOUR[2:],
    ]  # fmt: skip
    plan = mapwriter.tighten(map_plan(countries=wordy), "auto")
    said = [s.narration for s in plan.scenes]
    # A description is dropped, a telling fact kept (one a country), names as said.
    assert said[1] == (
        "The United States has apple pie, brownies, which were first baked in Chicago, "
        "and cheesecake."
    )
    assert said[2] == "Canada has butter tarts and Nanaimo bars."
    assert said[3] == "France has macarons, which began in Italy, and crème brûlée."
    assert said[5] == "Holland has stroopwafels."
    assert map_problems(plan) == []
    # Too long for its length: the facts go, then things past four, then the last countries.
    many = [
        country(name, f"{name} has cakes.", *(f"{x} grand cake" for x in "abcde"))
        for name in ("France", "Italy", "Japan", "the UK", "Holland", "The United States")
    ]
    short = mapwriter.tighten(map_plan(countries=many), "short")
    from agentic_editor.animate.plan import MAP_WORDS, narration_words

    countries = [s for s in short.scenes if s.visual.kind == "country"]
    assert all(len(s.visual.items) <= mapwriter.ITEMS_WHEN_LONG for s in countries)
    assert len(countries) >= 5 and narration_words(short) <= MAP_WORDS["short"][1] + 10
    for fact in ("who won seven Oscars", "created by a marine biologist", "yes, it's Canadian"):
        assert mapwriter.telling(fact), fact
    for fact in ("a symbol of home comfort", "a custard topped with caramel"):
        assert not mapwriter.telling(fact), fact


def test_many_card_pictures_are_painted_in_turns_of_the_gpu(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from agentic_editor import slots
    from tests.test_animate import FakeProc

    turns = []

    @contextmanager
    def turn(what, progress=None):
        turns.append(what)
        yield

    monkeypatch.setattr(slots.gpu, "turn", turn)
    settings = Settings(cache_dir=tmp_path / "cache")
    shown = []
    pictures = [images.Picture(f"a plate of dish number {n}", n) for n in range(19)]
    names = images.paint(
        pictures, "map", settings, lambda s, m: shown.append(m), run=FakeProc, size=images.CARD
    )
    assert all(names) and len(set(names)) == 19
    assert len(turns) == 3  # 8 + 8 + 3: anyone waiting for the GPU goes between turns
    assert (FakeProc.last["width"], FakeProc.last["height"]) == images.CARD
    assert len(FakeProc.last["jobs"]) == 3
    assert shown[-1] == "painted 19 of 19 pictures" and "painted 9 of 19 pictures" in shown


def test_a_line_asked_for_twice_at_once_is_spoken_once(tmp_path, monkeypatch):
    """The studio's sound is fetched more than once at a time (the browser's ranges): the
    second asker waits for the first's recording instead of making it again."""
    import threading
    import time

    pytest.importorskip("torch")
    from tests.test_animate import FakePipeline

    class Slow(FakePipeline):
        def __call__(self, text, voice, speed):
            time.sleep(0.3)
            yield from super().__call__(text, voice, speed)

    fake = Slow()
    monkeypatch.setattr(voice, "_pipeline", lambda lang: fake)
    got = []

    def ask():
        got.append(voice.speak("Japan has mochi.", "am_michael", tmp_path))

    threads = [threading.Thread(target=ask) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert fake.calls == 1 and len(got) == 3 and got[0] == got[1] == got[2]


def test_things_that_dont_belong_are_left_out_after_a_line_by_line_check():
    tour = [*TOUR, country("Canada", "Canada has maple syrup.", "maple syrup")]
    # Lines: 1-3 the US, 4-5 the UK, 6-7 France, 8-9 Italy, 10 Holland, 11-12 Japan, 13 Canada.
    # (Short of words afterwards: written again, the same, so the first stays.)
    model = MapModel(map_reply(countries=tour), map_reply(countries=tour), wrong=[2, 13])
    brief = planner.Brief("Most famous desserts from every country", style="map", length="short")
    plan, notes = planner.draft(model, brief, [])
    asked = model.vetted[0][0]["content"]
    assert "1. The United States: apple pie" in asked and "13. Canada: maple syrup" in asked
    assert "Its things must be desserts." in asked
    assert mapwriter.kind_of("Most haunted places from every country") == "places"
    assert mapwriter.kind_of("Popular car brands in different countries") == "car brands"
    usa = plan.scenes[1]
    assert [i.text for i in usa.visual.items] == ["apple pie", "cheesecake"]
    assert usa.narration == "The United States has apple pie and cheesecake."
    assert "Canada" not in [s.visual.text for s in plan.scenes]
    assert notes[0] == "left out as not fitting the topic: The United States: brownies, Canada"
    # A check that calls most of it wrong has misread the question: nothing is left out.
    model = MapModel(map_reply(), wrong=list(range(1, 10)))
    plan, notes = planner.draft(model, brief, [])
    assert sum(len(s.visual.items) for s in plan.scenes) == 12 and not notes
    # Too little left: written once more with real ones, and that is checked too.
    short = planner.Brief("Most famous desserts from every country", style="map", length="short")
    better = [dict(c) for c in TOUR]
    better[0] = country(
        "The United States",
        "The United States has apple pie, pecan pie, key lime pie, and cheesecake.",
        "apple pie", "pecan pie", "key lime pie", "cheesecake",
    )  # fmt: skip
    model = MapModel(map_reply(), map_reply(countries=better), wrong=[[2, 3, 4, 5, 6, 7], [3]])
    plan, notes = planner.draft(model, short, [])
    assert len(model.vetted) == 2
    assert "Write the whole reel again with real desserts" in model.asked[-1][-1]["content"]
    assert [i.text for i in plan.scenes[1].visual.items] == ["apple pie", "pecan pie", "cheesecake"]
    assert notes[1] == "left out as not fitting the topic: The United States: key lime pie"


def test_a_busy_map_server_is_asked_again(tmp_path, monkeypatch):
    import io
    import urllib.error
    import urllib.request

    answers = [
        urllib.error.HTTPError("u", 429, "Too many requests", {}, None),
        io.BytesIO(b"the earth"),
    ]

    class Answer:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self.body

        def __exit__(self, *exc):
            return False

    def urlopen(request, timeout):
        got = answers.pop(0)
        if isinstance(got, Exception):
            raise got
        return Answer(got)

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    dest = tmp_path / "earth.jpg"
    maps._download("https://example.org/earth.jpg", dest, pause=0)
    assert dest.read_bytes() == b"the earth" and not answers
    answers[:] = [urllib.error.HTTPError("u", 404, "Not found", {}, None)]
    with pytest.raises(urllib.error.HTTPError):
        maps._download("https://example.org/earth.jpg", dest, pause=0)


def test_a_card_with_a_plain_emoji_shows_its_countrys_other_one():
    tour = [dict(c) for c in TOUR]
    first = tour[0]["items"]
    tour[0] = {**tour[0], "items": [{**first[0], "emoji": "⚫"}, *first[1:]]}
    tour[5] = {**tour[5], "items": [{**i, "emoji": "🟦"} for i in tour[5]["items"]]}
    plan = map_plan(countries=tour)
    assert plan.scenes[1].visual.items[0].emoji == "🍰"  # the country's own
    assert plan.scenes[6].visual.items[0].emoji == "🌍"  # none left: the default


def test_the_tours_opening_country_is_never_left_out_whole():
    model = MapModel(map_reply(), map_reply(), wrong=[1, 2, 3, 10])  # all the US, and Holland
    brief = planner.Brief("Most famous desserts from every country", style="map", length="short")
    plan, _ = planner.draft(model, brief, [])
    countries = [s.visual.text for s in plan.scenes if s.visual.kind == "country"]
    assert countries[0] == "The United States" and "Holland" not in countries
    assert len(plan.scenes[1].visual.items) == 3  # kept as written


def test_a_world_map_reel_is_encoded_lighter(monkeypatch, tmp_path):
    from agentic_editor.animate import render

    asked = []
    monkeypatch.setattr(render.subprocess, "Popen", lambda cmd, **kw: asked.append(cmd))
    render._encoder(tmp_path / "a.mp4", 30, render.CRFS.get("map", render.CRF))
    render._encoder(tmp_path / "b.mp4", 30)
    crf = [cmd[cmd.index("-crf") + 1] for cmd in asked]
    assert crf == ["22", "17"]
