"""The World map style: the map's data, the writer's tour, timing, captions and the studio."""

import copy
import hashlib
import json
import re
import typing
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from agentic_editor.animate import images, maps, mapwriter, planner, sound, studio, voice
from agentic_editor.animate.plan import Item, Plan, map_problems, problems, tidy
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
    # No two overlap at any zoom: a card's width apart across, or its height (with its
    # label) down, as the camera shows them close (cards grow and shrink with the map).
    lat = (usa["main"][1] + usa["main"][3]) / 2
    squash = max(0.2, np.cos(np.radians(lat)))
    across, down = (close * share for share in maps.CARD_SPAN)
    for i, a in enumerate(spots):
        for b in spots[i + 1 :]:
            dx, dy = abs(a[0] - b[0]) * squash, abs(a[1] - b[1])
            assert dx >= across - 1e-6 or dy >= down - 1e-6, (a, b)
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
    """The planner model for World map reels: the writer's replies in turn; the strict
    check (mapwriter.judge) says yes to every thing but those in `wrong` (a name, or
    "Country: name", any case); a refill names the next of `refills` (a list of names per
    refill), a search for more countries answers `more`."""

    def __init__(
        self, *replies, wrong=(), refills=(), more=(), same=(), iconic=(), near=(), origins=None
    ):
        super().__init__(*replies)
        # where the second look says a thing comes from (else the card's own country)
        self.origins = {k.lower(): v for k, v in (origins or {}).items()}
        self.home: dict[str, str] = {}  # each thing's card country, from the first look
        self.near = {n.lower() for n in near}  # passed the first look, not the second
        self.second: list[str] = []  # the second looks, as asked
        self.wrong = {w.lower() for w in wrong}
        self.refills = [list(r) for r in refills]
        self.more = list(more)
        self.same = list(same)  # pairs of names the duplicates check calls the same thing
        self.iconic = list(iconic)  # the topic's best known things: (country, name)
        self.judged: list[str] = []
        self.checks: list[str] = []  # the check prompts, as asked

    def chat_json(self, messages, schema, temperature=0.3):
        if schema is mapwriter.CHECK_SCHEMA:
            text = messages[-1]["content"]
            self.checks.append(text)
            country = re.search(r"Country: (.+?)\. Thing", text).group(1)
            thing = re.search(r"Thing: (.+?)\.\n", text).group(1)
            kind = re.search(r"must be one of the (.+?)\.\n", text).group(1)
            self.judged.append(f"{country}: {thing}")
            self.home[thing.lower()] = country
            bad = {thing.lower(), f"{country}: {thing}".lower()} & self.wrong
            allowed = sorted(mapwriter.allowed_for(kind) or {"other"})
            return {
                "what": thing, "category": allowed[0], "is_kind": "no" if bad else "yes",
                "from_country": "yes", "famous": "yes",
            }  # fmt: skip
        if schema is mapwriter.KIND_SCHEMA:
            text = messages[-1]["content"]
            self.second.append(text)
            thing = re.search(r"One card shows: (.+?) \(", text).group(1)
            assert " card:" not in text  # the card's country isn't given away
            home = self.home[thing.lower()]
            return {
                "plain": thing, "answer": "no" if thing.lower() in self.near else "yes",
                "origin": self.origins.get(thing.lower(), home),
            }  # fmt: skip
        if schema is mapwriter.ALLOWED_SCHEMA:
            return {"categories": ["other"]}
        if schema is mapwriter.REFILL_SCHEMA:
            names = self.refills.pop(0) if self.refills else []
            return {
                "items": [{"name": n, "emoji": "🍰", "picture": f"a plate of {n}"} for n in names]
            }
        if schema is mapwriter.MORE_SCHEMA:
            return {"countries": self.more}
        if schema is mapwriter.DUP_SCHEMA:
            lines = re.findall(r"^(\d+)\. (.+)$", messages[-1]["content"], re.M)
            number = {name.lower(): int(n) for n, name in lines}
            pairs = [
                {"first": number[a.lower()], "second": number[b.lower()]}
                for a, b in self.same
                if a.lower() in number and b.lower() in number
            ]
            return {"same": pairs}
        if schema is mapwriter.ICONIC_SCHEMA:
            return {
                "examples": [
                    {"country": c, "name": n, "emoji": "⭐", "picture": f"a picture of {n}"}
                    for c, n in self.iconic
                ]
            }
        return super().chat_json(messages, schema, temperature)


def map_reply(countries=None, **changes):
    reply = {
        "title": "Most Famous Desserts In Every Country",
        "hook": "The most famous desserts from every country.",
        "headline": "Most famous desserts in every country",
        "highlight": "desserts",
        "countries": copy.deepcopy(countries if countries is not None else TOUR),
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
    writer = MapModel(map_reply(countries=changed), map_reply(countries=changed))
    brief = planner.Brief("desserts", style="map", length="short")
    out, left = planner.change(writer, plan, "swap brownies for cookies", brief)
    assert writer.judged == ["The United States: cookies"]  # only what the change brought
    assert not any("left out" in n for n in left)
    items = out.scenes[1].visual.items
    assert items[0].image == "a" * 24 + ".png" and items[1].image == ""
    assert "swap brownies for cookies" in writer.asked[0][1]["content"]
    assert out.style == "map"
    # One country written again: its scene only, the same shape.
    again = MapModel(
        country("Italy", "Italy has panna cotta and cannoli.", "panna cotta", "cannoli")
    )
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


def test_each_thing_is_checked_on_its_own_and_a_thin_country_is_filled_up():
    tour = [*TOUR, country("Canada", "Canada has maple syrup.", "maple syrup")]
    model = MapModel(
        map_reply(countries=tour),
        map_reply(countries=tour),
        wrong=["brownies", "cheesecake", "maple syrup"],
        # The US kept only apple pie: two more asked for (brownies was turned down, so it
        # isn't taken again); Canada kept nothing and gets nothing more.
        refills=[["pecan pie", "brownies", "key lime pie"], []],
    )
    brief = planner.Brief("Most famous desserts from every country", style="map", length="short")
    plan, notes = planner.draft(model, brief, [])
    first = model.checks[0]
    assert "Each thing must be one of the desserts." in first
    assert "Country: The United States. Thing: apple pie." in first
    assert len(model.judged) == 13 + 2  # every thing on its own, the refills too
    usa = plan.scenes[1]
    assert [i.text for i in usa.visual.items] == ["apple pie", "pecan pie", "key lime pie"]
    assert usa.narration == "The United States has apple pie, pecan pie, and key lime pie."
    assert "Canada" not in [s.visual.text for s in plan.scenes]
    assert notes[0] == (
        "left out as not sure to fit the topic: The United States: brownies, "
        "The United States: cheesecake, Canada: maple syrup"
    )
    assert notes[1] == "countries left out (nothing sure to show): Canada"
    assert plan.look == "neon"  # a food topic


def test_anything_short_of_a_clear_yes_never_reaches_a_card():
    """The check's answers each have to be "yes", and the category one the topic allows:
    pizza is a food, so it is no invention, however sure the model sounds."""

    class Doubts(MapModel):
        def chat_json(self, messages, schema, temperature=0.3):
            got = super().chat_json(messages, schema, temperature)
            if schema is mapwriter.CHECK_SCHEMA:
                if "Thing: pizza." in messages[-1]["content"]:
                    got["category"] = "food or dish"
                if "Thing: kangaroo." in messages[-1]["content"]:
                    got["famous"] = "unsure"
            return got

    tour = [
        country("The United States", "The United States has the telephone.", "telephone"),
        country("Italy", "Italy has pizza and the radio.", "pizza", "radio"),
        country("Australia", "Australia has the kangaroo and the black box.", "kangaroo",
                "black box"),
    ]  # fmt: skip
    plan = mapwriter.to_plan(map_reply(countries=tour), [], mapwriter.VOICE)
    model = Doubts()
    vetted, notes = mapwriter.vet(model, plan, "Most famous inventions from every country")
    kept = [i.text for s in vetted.scenes for i in s.visual.items]
    assert kept == ["telephone", "radio", "black box"]
    assert "Italy: pizza" in notes[0] and "Australia: kangaroo" in notes[0]
    # A thing the model couldn't be asked about isn't kept either; when it can't be asked
    # about anything at all, the tour stays as written and the person is told.

    class Down(MapModel):
        def chat_json(self, messages, schema, temperature=0.3):
            if schema is mapwriter.CHECK_SCHEMA:
                raise mapwriter.LLMError("no answer")
            return super().chat_json(messages, schema, temperature)

    same, notes = mapwriter._vetted(Down(), plan, "Most famous inventions from every country")
    assert same == plan and notes == ["the things on the cards couldn't be checked; look them over"]


def test_too_few_countries_left_are_made_up_with_more_checked_ones():
    model = MapModel(
        map_reply(),
        map_reply(),
        wrong=["The United States: apple pie", "brownies", "cheesecake", "macarons",
               "crème brûlée", "stroopwafels"],
        more=[
            country("Spain", "", "churros", "flan"),
            country("Japan", "", "mochi"),  # there already
            country("Greece", "", "baklava"),
        ],
    )  # fmt: skip
    brief = planner.Brief("Most famous desserts from every country", style="map", length="short")
    plan, notes = planner.draft(model, brief, [])
    countries = [s.visual.text for s in plan.scenes if s.visual.kind == "country"]
    # The US, France and Holland had nothing sure left (the opening country goes too).
    assert countries == ["the UK", "Italy", "Japan", "Spain", "Greece"]
    assert plan.scenes[-1].narration == "Greece has baklava."
    assert "countries left out (nothing sure to show): The United States, France, Holland" in notes


def test_a_thing_judged_once_is_not_asked_about_again(tmp_path):
    path = tmp_path / "map-checks.json"
    model = MapModel(wrong=["brownies"])
    plan = map_plan()
    mapwriter.vet(model, plan, "Most famous desserts from every country",
                             mapwriter.Checks(path))  # fmt: skip
    asked = len(model.judged)
    assert asked == 12 and path.is_file()
    again = MapModel(wrong=[])  # a later reel: the verdicts are kept, brownies too
    second, _ = mapwriter.vet(again, plan, "Most famous desserts from every country",
                              mapwriter.Checks(path))  # fmt: skip
    assert again.judged == []
    assert [i.text for i in second.scenes[1].visual.items] == ["apple pie", "cheesecake"]


def test_a_change_checks_only_the_things_it_brings():
    plan = map_plan()
    reply = map_reply()
    reply["countries"][3] = country("Italy", "Italy has tiramisu, gelato, and pizza.",
                                    "tiramisu", "gelato", "pizza")  # fmt: skip
    model = MapModel(reply, reply, wrong=["pizza"])
    changed, notes = mapwriter.change(
        model, plan, "Most famous desserts from every country", "add pizza to Italy", "short"
    )
    assert model.judged == ["Italy: pizza"]
    assert [i.text for i in changed.scenes[4].visual.items] == ["tiramisu", "gelato"]
    assert "Italy: pizza" in notes[0]


def test_a_country_written_again_has_its_things_checked():
    plan = map_plan()
    model = MapModel(
        country("Italy", "Italy has tiramisu, pizza, and cannoli.", "tiramisu", "pizza", "cannoli"),
        wrong=["pizza"],
    )
    redone = mapwriter.redo_country(model, plan, 4, idea="Most famous desserts from every country")
    assert [i.text for i in redone.scenes[4].visual.items] == ["tiramisu", "cannoli"]
    assert redone.scenes[4].narration == "Italy has tiramisu and cannoli."


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


def test_the_look_and_the_badges_come_from_the_topic():
    assert mapwriter.look_of("Most haunted places from every country") == "horror"
    assert mapwriter.look_of("Scariest legends from every country") == "horror"
    assert mapwriter.look_of("Most famous street foods from every country") == "neon"
    assert mapwriter.look_of("Most famous desserts from every country") == "neon"
    assert mapwriter.look_of("Most famous castles from every country") == "cards"
    assert mapwriter.ranking("Top 10 countries for pizza") == "rank"
    assert mapwriter.ranking("How many castles every country has") == "count"
    assert mapwriter.ranking("Most famous castles from every country") == ""
    assert mapwriter.kind_of("Most haunted places from every country") == "places"
    assert mapwriter.kind_of("Popular car brands in different countries") == "car brands"
    assert mapwriter.kind_of("How many castles every country has") == "castles"
    assert mapwriter.allowed_for("inventions") == {"invention, technology or machine", "vehicle"}
    assert mapwriter.allowed_for("sportswear brands") == {"brand or company"}
    assert mapwriter.allowed_for("castles") == {"place, building or landmark"}
    assert mapwriter.allowed_for("inventors") == {"person"}
    assert mapwriter.allowed_for("superstitions") is None  # the model is asked

    # A ranking: the countries numbered by their place, number one last.
    model = MapModel(map_reply(), map_reply())
    ranked, _ = mapwriter.draft(model, "Top 6 countries for desserts", "short", "", [],
                                mapwriter.VOICE)  # fmt: skip
    badges = [(s.visual.value, s.visual.emoji) for s in ranked.scenes if s.visual.kind == "country"]
    assert badges[0] == ("#6", "🏆") and badges[-1] == ("#1", "🏆")
    assert "number one comes last" in model.asked[0][1]["content"]
    # A count: each country's number as the writer gave it, said in its sentence.
    counts = [{**c, "number": f"{n},000"} for n, c in enumerate(TOUR, 1)]
    model = MapModel(
        map_reply(countries=counts, badge_emoji="🏰"), map_reply(countries=counts, badge_emoji="🏰")
    )
    counted, _ = mapwriter.draft(model, "How many castles every country has", "short", "", [],
                                 mapwriter.VOICE)  # fmt: skip
    usa = counted.scenes[1]
    assert (usa.visual.value, usa.visual.emoji) == ("1,000", "🏰")
    assert usa.narration.startswith("The United States has 1,000 castles, including apple pie")
    assert '"number"' in json.dumps(model.asked[0]) or "number" in model.asked[0][1]["content"]
    # A topic that is neither keeps no badge, even one written by hand before.
    plain = map_plan()
    plain.scenes[1].visual.value = "#2"
    assert (
        mapwriter.badges(plain, "Most famous desserts from every country").scenes[1].visual.value
        == ""
    )


def test_a_world_map_reel_is_encoded_lighter(monkeypatch, tmp_path):
    from agentic_editor.animate import render

    asked = []
    monkeypatch.setattr(render.subprocess, "Popen", lambda cmd, **kw: asked.append(cmd))
    render._encoder(tmp_path / "a.mp4", 30, render.CRFS.get("map", render.CRF))
    render._encoder(tmp_path / "b.mp4", 30)
    crf = [cmd[cmd.index("-crf") + 1] for cmd in asked]
    assert crf == ["22", "17"]


def painter(asked):
    """A stand-in for the image model: each picture a small white file, what it was asked
    for noted."""

    def paint(pictures, style, settings, progress, size=images.SIZE):
        out = images.folder(settings)
        out.mkdir(parents=True, exist_ok=True)
        names = []
        for p in pictures:
            asked.append((p.prompt, style, size))
            name = images.name_of(images.full_prompt(p, style), p.seed, "m", size)
            Image.new("RGB", (8, 10), "white").save(out / name)
            names.append(name)
        return names

    return paint


def test_a_card_painted_once_is_never_painted_again_in_any_reel(tmp_path, world):
    settings, Session = map_env(tmp_path, world)
    asked = []
    first, _ = studio.paint_plan(map_plan(), "m1", Session, settings, painter(asked))
    assert len(asked) == 12 and len({a[0] for a in asked}) == 12
    # A later reel names some of the same things (its pictures asked in other words, a
    # name in other capitals) and one new thing: only that one is painted.
    reply = map_reply()
    reply["countries"][0]["items"][0]["picture"] = "a warm slice of apple pie, top view"
    reply["countries"][3] = country("Italy", "Italy has Tiramisu and panna cotta.", "Tiramisu",
                                    "panna cotta")  # fmt: skip
    later = mapwriter.to_plan(reply, [], mapwriter.VOICE)
    asked.clear()
    second, notes = studio.paint_plan(later, "m2", Session, settings, painter(asked))
    assert [a[0] for a in asked] == ["a plate of panna cotta"] and notes == []
    assert second.scenes[1].visual.items[0].image == first.scenes[1].visual.items[0].image
    assert second.scenes[4].visual.items[0].image == first.scenes[4].visual.items[0].image
    # Asked to paint one again (Claude's repaint_pictures, a new number): that one only.
    again = later.model_copy(deep=True)
    again.scenes[1].visual.items[0].art_seed = 1
    asked.clear()
    third, _ = studio.paint_plan(again, "m3", Session, settings, painter(asked))
    assert [a[0] for a in asked] == ["a warm slice of apple pie, top view"]
    assert third.scenes[1].visual.items[0].image != second.scenes[1].visual.items[0].image
    # The gold horror look paints its own: the place as a dark photo, 4:3.
    asked.clear()
    studio.paint_plan(map_plan().model_copy(update={"look": "horror"}), "m4", Session, settings,
                      painter(asked))  # fmt: skip
    assert {a[1:] for a in asked} == {("map-horror", images.HORROR_CARD)} and len(asked) == 12
    assert "eerie and dark" in images.full_prompt(images.Picture("a castle", 1), "map-horror")
    # Where nothing can be painted, the ones painted before for that look are still shown.
    _, notes = studio.paint_plan(later, "m5", Session, settings)
    assert notes == [] or "image model" in notes[0]


def test_a_neon_card_is_its_dish_cut_out_of_the_white(tmp_path, world):
    from PIL import ImageDraw

    settings, Session = map_env(tmp_path, world)
    out = images.folder(settings)
    out.mkdir(parents=True)
    picture = Image.new("RGB", (100, 100), "white")
    draw = ImageDraw.Draw(picture)
    draw.ellipse((20, 20, 80, 80), fill=(200, 40, 30))
    draw.ellipse((45, 45, 55, 55), fill="white")  # white inside the dish stays
    picture.save(out / ("c" * 24 + ".png"))
    name = images.cut_out(settings, "c" * 24 + ".png")
    assert name == images.cut_name("c" * 24 + ".png") and name != "c" * 24 + ".png"
    alpha = Image.open(out / name).getchannel("A")
    assert alpha.getpixel((2, 2)) == 0 and alpha.getpixel((97, 50)) == 0
    assert alpha.getpixel((30, 50)) == 255 and alpha.getpixel((50, 50)) == 255
    assert images.cut_out(settings, "c" * 24 + ".png") == name  # made once
    assert images.cut_out(settings, "d" * 24 + ".png") == ""
    # A neon reel's cards get the cut-outs; the same things in the cards look the whole.
    asked = []
    neon, _ = studio.paint_plan(map_plan().model_copy(update={"look": "neon"}), "n1", Session,
                                settings, painter(asked))  # fmt: skip
    cards, _ = studio.paint_plan(map_plan(), "n2", Session, settings, painter(asked))
    assert len(asked) == 12  # painted once for both looks
    whole = cards.scenes[1].visual.items[0].image
    assert neon.scenes[1].visual.items[0].image == images.cut_name(whole)
    assert tidy(neon).scenes[1].visual.items[0].image == images.cut_name(whole)  # a valid name


def test_the_voice_is_recorded_while_the_cards_are_painted(tmp_path, world, monkeypatch):
    import threading
    import time

    settings, Session = map_env(tmp_path, world)
    monkeypatch.setattr(maps, "ready", lambda models_dir: True)
    events = []

    def paint(pictures, style, settings, progress, size=images.SIZE):
        events.append(("painting", time.monotonic()))
        time.sleep(0.6)
        events.append(("painted", time.monotonic()))
        return [""] * len(pictures)

    def speak(text, voice_id, cache, speed=None):
        time.sleep(0.08)  # Kokoro takes its time over each line
        events.append(("spoke", time.monotonic(), threading.current_thread().name))
        return spoken(text)

    with Session() as db:
        plan = map_plan().model_dump(mode="json")
        db.add(Animation(id="v1", owner_id="u1", idea="desserts", style="map", images=True,
                         plan=plan, task={"op": "voice"}))  # fmt: skip
        db.commit()
    studio.run_task("v1", Session, settings, speak=speak, paint=paint)
    with Session() as db:
        assert db.get(Animation, "v1").status == "ready"
    began = next(e[1] for e in events if e[0] == "painting")
    ended = next(e[1] for e in events if e[0] == "painted")
    voiced = [e for e in events if e[0] == "spoke"]
    assert voiced and all(e[2].startswith("voice") for e in voiced[:3])
    assert any(began <= e[1] <= ended for e in voiced)  # spoken while painting


def said(name, *items):
    """A country as the writer would say it: "<country> has A, B, and C."."""
    return country(name, mapwriter.sentence(name, [Item(text=i) for i in items], [""] * len(items)),
                   *items)  # fmt: skip


def test_the_same_thing_twice_is_named_once():
    tour = [
        said("The United States", "Mardi Gras", "New Orleans Mardi Gras", "Coachella"),
        said("Italy", "the Venice Carnival", "Carnevale di Venezia", "Palio di Siena"),
        said("Brazil", "Rio Carnival"),
        said("Spain", "La Tomatina", "Rio Carnival"),  # another country's already
        said("Japan", "Gion Matsuri", "Hanami"),
    ]
    plan = mapwriter.to_plan(map_reply(countries=tour), [], mapwriter.VOICE)
    model = MapModel(same=[("the Venice Carnival", "Carnevale di Venezia")])
    vetted, notes = mapwriter.vet(model, plan, "Most famous festivals from every country")
    cards = {s.visual.text: [i.text for i in s.visual.items] for s in vetted.scenes[1:]}
    assert cards["The United States"] == ["Mardi Gras", "Coachella"]  # by its words
    assert cards["Italy"] == ["the Venice Carnival", "Palio di Siena"]  # by the model
    assert cards["Spain"] == ["La Tomatina"]  # once in the whole tour
    assert "Italy: Carnevale di Venezia" in notes[0]
    assert "Spain: Rio Carnival (named for another country already)" in notes[0]
    assert mapwriter.same_by_name("Lunar New Year", "Chinese New Year") is False
    assert mapwriter.same_by_name("Rio Carnival", "Venice Carnival") is False
    assert mapwriter.same_by_name("the Telephone", "telephone") is True


def test_a_tour_never_leaves_out_the_topics_best_known_things():
    """The live festivals reel had no Germany: Oktoberfest is put in, its country next to
    its neighbours on the map; one already there isn't added twice."""

    class Atlas:
        spots: typing.ClassVar = {"united states": (-98, 39), "france": (2, 46), "italy": (12, 43),
                 "germany": (10, 51), "japan": (138, 36), "brazil": (-52, -10)}  # fmt: skip

        def __call__(self, name):
            return name.lower() in self.spots

        def locate(self, name):
            return self.spots.get(name.lower())

    tour = [
        said("The United States", "Mardi Gras", "Coachella"),
        said("France", "Bastille Day", "Fête de la Musique"),
        said("Italy", "the Venice Carnival", "Palio di Siena"),
        said("Japan", "Gion Matsuri", "Hanami"),
        said("Brazil", "Rio Carnival"),
    ]
    plan = mapwriter.to_plan(map_reply(countries=tour), [], mapwriter.VOICE)
    model = MapModel(
        iconic=[("Germany", "Oktoberfest"), ("Brazil", "Rio Carnival"), ("India", "Diwali")],
        refills=[["Christmas markets"]],
    )
    vetted, notes = mapwriter.vet(model, plan, "Most famous festivals from every country",
                                  known=Atlas())  # fmt: skip
    countries = [s.visual.text for s in vetted.scenes if s.visual.kind == "country"]
    # Right after its nearest neighbour on the map (Italy, here).
    assert countries == ["The United States", "France", "Italy", "Germany", "Japan", "Brazil"]
    germany = next(s for s in vetted.scenes if s.visual.text == "Germany")
    assert [i.text for i in germany.visual.items] == ["Oktoberfest", "Christmas markets"]
    assert germany.narration == "Germany has Oktoberfest and Christmas markets."
    assert "put in as the topic's best known: Germany: Oktoberfest" in notes[-1]
    assert "India" not in countries  # not on this map
    # A ranking keeps its own order and countries: nothing is put in.
    model = MapModel(iconic=[("Germany", "Oktoberfest")])
    ranked, _ = mapwriter.vet(model, plan, "Top 5 festivals in the world", known=Atlas())
    assert "Germany" not in [s.visual.text for s in ranked.scenes]


def test_a_thing_only_its_own_country_knows_is_not_famous_enough():
    model = MapModel()
    mapwriter.judge(model, "Most famous festivals from every country", "festivals", "Turkey",
                    "Istanbul Kite Festival", None)  # fmt: skip
    asked = model.checks[0]
    assert "would a typical viewer anywhere in the world recognise it" in asked
    assert "known well outside Turkey" in asked


def test_a_field_name_left_in_a_text_is_taken_out():
    plan = map_plan(headline="Most famous festivals in every country; highlight: festivals",
                    highlight="")  # fmt: skip
    title = plan.scenes[0].visual
    assert title.text == "Most famous festivals in every country"
    assert title.highlight == "festivals"
    plan = map_plan(headline="Most famous castles, kicker: World tour", title="Castles; hook: x")
    assert plan.scenes[0].visual.text == "Most famous castles" and plan.title == "Castles"
    assert tidy(map_plan(headline="highlight: festivals")).scenes[0].visual.text == (
        "highlight: festivals"
    )  # nothing left without it: kept as written


def test_a_best_known_thing_goes_to_its_country_however_it_is_named():
    """The castles tour got an "England" next to its "United Kingdom" (the map calls both
    GBR), and England's refill named Windsor Castle a second time. A best known thing goes
    on its country's own cards; one the tour has (plurals aside) isn't put in again; what
    a new country is filled with is never a thing the tour has."""

    class Atlas:
        codes: typing.ClassVar = {"united kingdom": "GBR", "england": "GBR", "france": "FRA",
                                  "spain": "ESP", "mexico": "MEX", "japan": "JPN",
                                  "italy": "ITA"}  # fmt: skip

        def __call__(self, name):
            return name.lower() in self.codes

        def code(self, name):
            return self.codes.get(name.lower())

        def locate(self, name):
            return {"GBR": (-2, 54), "FRA": (2, 46), "ESP": (-4, 40), "MEX": (-102, 23),
                    "JPN": (138, 36), "ITA": (12, 43)}.get(self.code(name))  # fmt: skip

    tour = [
        said("United Kingdom", "Windsor Castle", "Edinburgh Castle"),
        said("France", "Château de Chambord", "Mont-Saint-Michel"),
        said("Spain", "Alcázar of Segovia", "churro"),
        said("Japan", "Himeji Castle", "Matsumoto Castle"),
        said("Italy", "Castel Sant'Angelo", "Castello Sforzesco"),
    ]
    plan = mapwriter.to_plan(map_reply(countries=tour), [], mapwriter.VOICE)
    model = MapModel(
        iconic=[("England", "Tower of London"), ("Mexico", "Churros"), ("Spain", "Alhambra")],
        refills=[],
    )
    vetted, _ = mapwriter.vet(model, plan, "Most famous castles from every country",
                              known=Atlas())  # fmt: skip
    cards = {s.visual.text: [i.text for i in s.visual.items] for s in vetted.scenes[1:]}
    assert "England" not in cards and "Mexico" not in cards
    assert cards["United Kingdom"][0] == "Tower of London"  # on the UK's own cards
    assert cards["Spain"][0] == "Alhambra"
    assert "Churros" not in str(cards)  # Spain's "churro" is the same thing
    assert mapwriter.same_by_name("churro", "Churros") is True
    assert mapwriter.same_country("England", "United Kingdom", Atlas()) is True
    assert mapwriter.same_country("The UK", "United Kingdom") is False  # no map: by words
    # A country put in is filled with nothing another country has already.
    model = MapModel(iconic=[("Mexico", "Chapultepec Castle")],
                     refills=[["Windsor Castle", "Castillo de San Juan de Ulúa"]])  # fmt: skip
    vetted, _ = mapwriter.vet(model, plan, "Most famous castles from every country",
                              known=Atlas())  # fmt: skip
    mexico = next(s for s in vetted.scenes if s.visual.text == "Mexico")
    assert [i.text for i in mexico.visual.items] == [
        "Chapultepec Castle", "Castillo de San Juan de Ulúa"
    ]  # fmt: skip
    asked = model.checks
    assert not any("Thing: Windsor Castle" in a and "Country: Mexico" in a for a in asked)


def test_a_near_kind_that_passes_the_first_look_fails_the_second():
    """Asked with everything else, the PC's model passed the Palace of Versailles as a
    castle; a second question on the kind alone turns it down."""
    tour = [
        said("France", "Palace of Versailles", "Château de Chambord"),
        said("Germany", "Neuschwanstein Castle", "Hohenzollern Castle"),
        said("Japan", "Himeji Castle", "Matsumoto Castle"),
        said("Italy", "Castel Sant'Angelo", "Castello Sforzesco"),
        said("Spain", "Alcázar of Segovia", "Alhambra"),
    ]
    plan = mapwriter.to_plan(map_reply(countries=tour), [], mapwriter.VOICE)
    model = MapModel(near=["Palace of Versailles"], refills=[[]])
    vetted, notes = mapwriter.vet(model, plan, "Most famous castles from every country")
    france = next(s for s in vetted.scenes if s.visual.text == "France")
    assert [i.text for i in france.visual.items] == ["Château de Chambord"]
    assert "France: Palace of Versailles" in notes[0]
    assert len(model.second) == 10  # only things the first look passed are looked at again
    asked = model.second[0]
    assert "is that one of the castles?" in asked and "Palace of Versailles" in asked


def test_what_is_put_in_later_is_checked_for_duplicates_too():
    """Measured on the PC: a refill named "Día de los Muertos" next to "Day of the Dead",
    and the best known "Chinese New Year" went next to "Lunar New Year"."""
    tour = [
        said("Mexico", "Day of the Dead", "Royal Wedding"),
        said("China", "Lunar New Year", "Mid-Autumn Festival"),
        said("Spain", "La Tomatina", "Las Fallas"),
        said("Italy", "Carnevale di Venezia", "Palio di Siena"),
        said("Japan", "Gion Matsuri", "Sapporo Snow Festival"),
    ]
    plan = mapwriter.to_plan(map_reply(countries=tour), [], mapwriter.VOICE)
    model = MapModel(
        wrong=["Royal Wedding"],
        refills=[["Día de los Muertos"]],
        same=[
            ("Day of the Dead", "Día de los Muertos"),
            ("Chinese New Year", "Lunar New Year"),
        ],
        iconic=[("China", "Chinese New Year")],
    )
    vetted, notes = mapwriter.vet(model, plan, "Most famous festivals from every country")
    cards = {s.visual.text: [i.text for i in s.visual.items] for s in vetted.scenes[1:]}
    assert cards["Mexico"] == ["Day of the Dead"]
    # A best known thing the country has under another name isn't put in: the tour's
    # own card stays ("Rio Carnival" stayed, not a bare "Carnival", on the Mac).
    assert cards["China"] == ["Lunar New Year", "Mid-Autumn Festival"]
    assert "Mexico: Día de los Muertos (the same as another card)" in notes[0]
    assert not any("Chinese New Year" in n for n in notes)


def test_a_thing_from_another_country_is_found_by_asking_where_it_is_from():
    """Asked "is it from Canada?", the PC's model said yes to Dexter's Laboratory; asked
    where it comes from, it names the United States, and the map tells the two apart (and
    knows "England" is the United Kingdom)."""

    class Atlas:
        codes: typing.ClassVar = {"canada": "CAN", "united states": "USA", "japan": "JPN",
                                  "united kingdom": "GBR", "england": "GBR", "india": "IND",
                                  "australia": "AUS", "france": "FRA"}  # fmt: skip

        def __call__(self, name):
            return name.lower() in self.codes

        def code(self, name):
            return self.codes.get(name.lower())

    tour = [
        said("Canada", "Caillou", "Dexter's Laboratory"),
        said("United Kingdom", "Wallace and Gromit", "Peppa Pig"),
        said("Japan", "Doraemon", "Astro Boy"),
        said("India", "Chhota Bheem", "Motu Patlu"),
        said("Australia", "Bluey", "Blinky Bill"),
    ]
    plan = mapwriter.to_plan(map_reply(countries=tour), [], mapwriter.VOICE)
    model = MapModel(
        origins={
            "Dexter's Laboratory": "United States",
            "Peppa Pig": "England",
            "Blinky Bill": "unknown",
        },
        refills=[["PAW Patrol"], ["Dot."]],
    )
    vetted, notes = mapwriter.vet(model, plan, "Most famous cartoons from every country",
                                  known=Atlas())  # fmt: skip
    cards = {s.visual.text: [i.text for i in s.visual.items] for s in vetted.scenes[1:]}
    assert cards["Canada"] == ["Caillou", "PAW Patrol"]  # filled up with a Canadian one
    assert cards["United Kingdom"] == ["Wallace and Gromit", "Peppa Pig"]
    assert "Blinky Bill" not in cards["Australia"]  # where from not known: not a clear yes
    assert "Canada: Dexter's Laboratory" in notes[0]
    # Without the map the origin can't be told apart reliably: not asked to decide.
    model = MapModel(origins={"Dexter's Laboratory": "United States"}, refills=[[], []])
    vetted, _ = mapwriter.vet(model, plan, "Most famous cartoons from every country")
    assert "Dexter's Laboratory" in str(vetted.scenes)
    # A verdict from an earlier check (no version) is asked again.
    checks = mapwriter.Checks(None)
    key = mapwriter.Checks.key("cartoons", "Canada", "Dexter's Laboratory")
    checks.found[key] = {"keep": True, "what": "", "category": "", "answers": []}
    assert checks.get(key) is None


def test_only_a_few_countries_are_put_in_for_the_best_known_things():
    tour = [
        said(c, f"{c} Fair", f"{c} Night") for c in ("France", "Italy", "Spain", "Japan", "India")
    ]
    plan = mapwriter.to_plan(map_reply(countries=tour), [], mapwriter.VOICE)
    far = [("Peru", "Inti Raymi"), ("Thailand", "Songkran"), ("Germany", "Oktoberfest"),
           ("Brazil", "Rio Carnival"), ("Mexico", "Day of the Dead")]  # fmt: skip
    model = MapModel(iconic=far, refills=[[] for _ in far])
    vetted, _ = mapwriter.vet(model, plan, "Most famous festivals from every country")
    names = [s.visual.text for s in vetted.scenes if s.visual.kind == "country"]
    assert len(names) == 5 + mapwriter.ADDED_COUNTRIES
    assert {"Peru", "Thailand", "Germany"} <= set(names)  # the first ones named go in
    assert "Mexico" not in names and "Brazil" not in names
