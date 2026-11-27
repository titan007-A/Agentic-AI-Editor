"""Song story people who look the same in every picture: each painted once as a portrait,
every moment painted from the portraits of the people in it by the likeness model, Z-Image
from words where it isn't or a picture fails, and the portraits kept across the song's
reels. No model runs here: the painters are stand-ins. Every lyric here is made up."""

import functools

from agentic_editor.animate import images, songhook, songpaint, songwriter, studio
from agentic_editor.animate.plan import HookChoice, Person, Plan, Scene, Song, Visual, tidy
from agentic_editor.db import Animation
from tests.test_animate import FakeProc, studio_env
from tests.test_animate_song import StudioRouter
from tests.test_animate_song_more import english_reply, song_of

LEO = Person(name="Leo", look="a boy of nine with curly red hair in a yellow raincoat")
NANA = Person(name="Nana", look="an old woman with a grey bun and round glasses, green shawl")
SAM = Person(name="Sam", look="a tall man with a black beard in a grey hoodie")
ANA = Person(name="Ana", look="a girl with two black braids in a blue dress")
ARTS = [
    "Leo and Nana fold paper boats at the kitchen table, warm lamp light",
    "Sam waits alone in his car at night, a cold phone glow on his face",
    "an empty street after the rain under one street light",
    "Nana, Leo, Sam and Ana at the window watching the storm, close-up",
    "Leo runs down the wet street with Nana's umbrella, Nana calling after him",
]

TWO_PARTS = [HookChoice(start=9.7, end=18.0), HookChoice(start=40.0, end=60.0)]


def faces_plan(arts=ARTS, people=(LEO, NANA, SAM, ANA), video="made") -> Plan:
    song = song_of([3.0] * len(arts)).model_copy(update={"people": list(people), "video": video})
    scenes = [
        Scene(narration=line.text, visual=Visual(kind="moment", art=art, camera="push_in"))
        for line, art in zip(song.lines, arts, strict=True)
    ]
    return tidy(Plan(title="Boats", kind="story", style="song", voice="", scenes=scenes, song=song))


class Painters:
    """The likeness model and Z-Image without the models: every picture asked is kept with
    how it was asked, and each gets a file name (or "" for those told to fail)."""

    def __init__(self, fail_likeness=(), fail_portraits=False):
        self.likeness_calls: list[tuple[list[images.Picture], dict]] = []
        self.words: list[images.Picture] = []
        self.fail_likeness = set(fail_likeness)
        self.fail_portraits = fail_portraits

    def likeness(self, pictures, settings, progress, **kw):
        self.likeness_calls.append((list(pictures), kw))
        portraits = kw.get("what") == "portraits"
        return [
            ""
            if (portraits and self.fail_portraits)
            or (not portraits and any(bad in p.prompt for bad in self.fail_likeness))
            else images.likeness_name(p, "klein", kw.get("size", images.SIZE))
            for p in pictures
        ]

    def paint(self, pictures, style, settings, progress, **kw):
        assert style == "song"
        self.words.extend(pictures)
        return [images.name_of(p.prompt, p.seed, "zimage") for p in pictures]


def test_people_are_painted_once_as_portraits_and_each_moment_from_theirs(tmp_path):
    settings, _ = studio_env(tmp_path)
    plan = faces_plan()
    painters = Painters()
    painted, notes = songpaint.paint(plan, settings, painters.paint, painters.likeness, None)
    assert notes == [] and painters.words == []  # nothing painted from words alone
    (portraits, how), (moments, how_moments) = painters.likeness_calls
    # Everyone named in a moment, once each, whole and alone, in the order they first come.
    assert how == {"size": songpaint.PORTRAIT_SIZE, "what": "portraits",
                   "why": "paint the story's people"}  # fmt: skip
    assert [p.prompt.split(" of ", 1)[1].split(", standing")[0] for p in portraits] == [
        LEO.look, NANA.look, SAM.look, ANA.look
    ]  # fmt: skip
    assert all("full-length" in p.prompt and "plain flat light grey" in p.prompt
               and "no text" in p.prompt and not p.refs for p in portraits)  # fmt: skip
    # One seed a reel: the portraits' and the moments', from the song.
    seed = images.seed_of("made", 0)
    assert {p.seed for p in portraits} == {p.seed for p in moments} == {seed}
    assert how_moments["size"] == images.SIZE and how_moments["what"] == "pictures"
    folder = images.folder(settings)
    by_name = {p.name: str(folder / p.image) for p in painted.song.people}
    assert all(p.image for p in painted.song.people)
    first, alone, empty, crowd, running = moments
    # The people in it, by their reference picture, in the order the moment names them.
    assert first.refs == (by_name["Leo"], by_name["Nana"])
    assert f"{LEO.look} (the person in image 1)" in first.prompt
    assert f"{NANA.look} (the person in image 2)" in first.prompt
    assert "Leo" not in first.prompt and "keeps exactly the face" in first.prompt
    assert alone.refs == (by_name["Sam"],) and "his car" in alone.prompt
    assert empty.refs == () and "reference" not in empty.prompt
    assert "flat 2D vector" in empty.prompt and "no text" in empty.prompt
    # Four in one picture: painted from the first three portraits, the fourth from words.
    assert crowd.refs == (by_name["Nana"], by_name["Leo"], by_name["Sam"])
    assert len(crowd.refs) == songpaint.MOST_REFS
    assert ANA.look in crowd.prompt and "image 4" not in crowd.prompt
    # Named twice (and with a possessive): marked once, written out each time.
    assert running.prompt.count(NANA.look) == 2 and running.prompt.count("image 2") == 1
    assert f"{NANA.look} (the person in image 2)'s umbrella" in running.prompt
    assert all(s.visual.image for s in painted.scenes)


def test_a_person_in_no_moment_gets_no_portrait(tmp_path):
    settings, _ = studio_env(tmp_path)
    plan = faces_plan(arts=ARTS[:3])
    painters = Painters()
    painted, _ = songpaint.paint(plan, settings, painters.paint, painters.likeness, None)
    assert len(painters.likeness_calls[0][0]) == 3  # Leo, Nana, Sam; Ana is in none
    assert [bool(p.image) for p in painted.song.people] == [True, True, True, False]


def test_without_the_likeness_model_each_moment_is_painted_from_words_as_before(tmp_path):
    settings, _ = studio_env(tmp_path)
    plan = faces_plan()
    plan = plan.model_copy(
        update={"song": plan.song.model_copy(
            update={"people": [p.model_copy(update={"image": "a" * 24 + ".png"})
                               for p in plan.song.people]})}
    )  # fmt: skip
    painters = Painters()
    painted, notes = songpaint.paint(plan, settings, painters.paint, None, None)
    assert painters.likeness_calls == [] and notes == []
    # Exactly today's pictures: the art with people written out, the song's seed.
    assert [p.prompt for p in painters.words] == [songwriter.painted(plan, a) for a in ARTS]
    assert {p.seed for p in painters.words} == {images.seed_of("made", 0)}
    assert all(not p.refs for p in painters.words)
    assert all(s.visual.image for s in painted.scenes)
    assert not any(p.image for p in painted.song.people)  # no portraits behind them


def test_a_picture_the_likeness_model_couldnt_paint_is_painted_from_words(tmp_path):
    settings, _ = studio_env(tmp_path)
    plan = faces_plan()
    painters = Painters(fail_likeness=["his car"])
    painted, notes = songpaint.paint(plan, settings, painters.paint, painters.likeness, None)
    assert [p.prompt for p in painters.words] == [songwriter.painted(plan, ARTS[1])]
    assert painted.scenes[1].visual.image == images.name_of(
        painters.words[0].prompt, painters.words[0].seed, "zimage"
    )
    assert all(s.visual.image for s in painted.scenes)
    assert notes == [
        "1 pictures were painted from words alone (the likeness model couldn't paint them)"
    ]


def test_when_no_portrait_can_be_painted_the_moments_are_painted_from_words(tmp_path):
    settings, _ = studio_env(tmp_path)
    plan = faces_plan()
    painters = Painters(fail_portraits=True)
    painted, notes = songpaint.paint(plan, settings, painters.paint, painters.likeness, None)
    assert len(painters.likeness_calls) == 1  # the painter failing: no moment asked of it
    assert len(painters.words) == len(ARTS) and all(s.visual.image for s in painted.scenes)
    assert "portraits couldn't be painted" in notes[0]
    assert not any(p.image for p in painted.song.people)


def test_a_moment_that_goes_on_with_the_last_shares_its_picture(tmp_path):
    settings, _ = studio_env(tmp_path)
    plan = faces_plan(arts=[ARTS[0], ARTS[0], ARTS[1]])
    painters = Painters()
    painted, _ = songpaint.paint(plan, settings, painters.paint, painters.likeness, None)
    assert len(painters.likeness_calls[1][0]) == 2
    assert painted.scenes[0].visual.image == painted.scenes[1].visual.image


def likeness_on(settings):
    """The real likeness painter asking the runner without the model (FakeProc)."""
    settings.identity_model = "mflux-community/flux2-klein-4b-mflux-q8"
    return functools.partial(images.paint_likeness, run=FakeProc)


def test_editing_a_look_repaints_its_portrait_and_only_the_moments_with_that_person(tmp_path):
    settings, _ = studio_env(tmp_path)
    likeness = likeness_on(settings)
    shown = []
    plan = faces_plan()
    painted, _ = songpaint.paint(plan, settings, None, likeness, lambda s, m: shown.append(m))
    assert "painted 4 of 4 portraits" in shown and shown[-1] == "painted 5 of 5 pictures"
    asked = FakeProc.last
    assert (asked["width"], asked["height"], asked["kind"]) == (720, 1280, "likeness")
    # Sam's look changed: his portrait and the two moments he is in, nothing else.
    people = [
        p.model_copy(update={"look": "a short man with a shaved head in a red jacket"})
        if p.name == "Sam"
        else p
        for p in painted.song.people
    ]
    edited = painted.model_copy(update={"song": painted.song.model_copy(update={"people": people})})
    calls = []
    real = likeness

    def counting(pictures, settings_, progress, **kw):
        names = real(pictures, settings_, progress, **kw)
        calls.append((kw["what"], FakeProc.last))
        return names

    FakeProc.last = None
    again, _ = songpaint.paint(edited, settings, None, counting, None)
    (what1, portraits), (what2, moments) = calls
    assert what1 == "portraits" and len(portraits["jobs"]) == 1
    assert "shaved head" in portraits["jobs"][0]["prompt"]
    assert (portraits["width"], portraits["height"]) == songpaint.PORTRAIT_SIZE
    assert what2 == "pictures" and len(moments["jobs"]) == 2
    assert all("shaved head" in job["prompt"] for job in moments["jobs"])
    sam = next(p for p in again.song.people if p.name == "Sam")
    assert sam.image and sam.image != next(p.image for p in painted.song.people if p.name == "Sam")
    changed = [a.visual.image != b.visual.image for a, b in zip(painted.scenes, again.scenes,
                                                                 strict=True)]  # fmt: skip
    assert changed == [False, True, False, True, False]


def test_portraits_are_used_again_by_the_songs_other_reels(tmp_path):
    settings, _ = studio_env(tmp_path)
    likeness = likeness_on(settings)
    songpaint.paint(faces_plan(), settings, None, likeness, None)
    # Another part of the same song: the same people, new moments.
    other = faces_plan(arts=["Leo asleep on the bus, Nana beside him", "Sam on the pier"])
    calls = []

    def counting(pictures, settings_, progress, **kw):
        FakeProc.last = None
        names = likeness(pictures, settings_, progress, **kw)
        calls.append((kw["what"], FakeProc.last))
        return names

    painted, _ = songpaint.paint(other, settings, None, counting, None)
    assert calls[0] == ("portraits", None)  # all three kept: none painted again
    assert len(calls[1][1]["jobs"]) == 2 and all(p.image for p in painted.song.people[:3])
    # Another song (another seed): painted anew.
    calls.clear()
    songpaint.paint(faces_plan(video="another"), settings, None, counting, None)
    assert len(calls[0][1]["jobs"]) == 4
    # A portrait asked for again: a new one.
    calls.clear()
    asked_again = other.model_copy(update={"song": other.song.model_copy(update={
        "people": [p.model_copy(update={"art_seed": 1 if p.name == "Leo" else 0})
                   for p in other.song.people]})})  # fmt: skip
    songpaint.paint(asked_again, settings, None, counting, None)
    assert len(calls[0][1]["jobs"]) == 1 and LEO.look in calls[0][1]["jobs"][0]["prompt"]


def test_the_people_in_a_moment_are_found_by_name_in_the_order_they_come():
    plan = faces_plan(people=(LEO, NANA, Person(name="Ana Maria", look="a woman"), ANA))
    found = songwriter.people_in(plan, "Ana Maria hugs Nana while Leo's kite and Ana fly")
    assert [p.name for p in found] == ["Ana Maria", "Nana", "Leo", "Ana"]
    assert [p.name for p in songwriter.people_in(plan, "Ana Maria at the door")] == ["Ana Maria"]
    assert songwriter.people_in(plan, "Leonard and Nanami at the fair") == []


def test_a_portrait_and_its_number_are_kept_tidy():
    plan = faces_plan(people=(
        LEO.model_copy(update={"image": "../../etc/passwd", "art_seed": -3}),
        NANA.model_copy(update={"image": "b" * 24 + ".png", "art_seed": 99_999}),
    ))  # fmt: skip
    leo, nana = plan.song.people
    assert (leo.image, leo.art_seed) == ("", 0)
    assert (nana.image, nana.art_seed) == ("b" * 24 + ".png", 10_000)


# ---- in the studio ------------------------------------------------------------------------


NAMED = [ARTS[0], ARTS[2], ARTS[4]]  # Leo and Nana, or nobody


class Writes:
    """The writer: the lines as given, then a story whose people are told by `people` and
    its moments by `arts` (then by `fixed`, when sent back)."""

    def __init__(self, song: Song, people: list[dict], arts=NAMED, fixed=None):
        self.song, self.people, self.arts, self.fixed = song, people, arts, fixed or arts
        self.asked: list[str] = []
        self.count = 0

    def chat_json(self, messages, schema, temperature=0.3):
        text = messages[-1]["content"]
        if "lines of the part a reel plays" in text:
            return english_reply(self.song)
        self.asked.append(text)
        arts = self.arts
        if text.startswith("Fix these"):
            arts = self.fixed
        else:
            self.count = int(text.split("Write the reel: ")[1].split(" moments")[0])
        return {
            "title": "Boats in the rain", "story": "A storm and a family.", "caption": "Rain.",
            "people": self.people,
            "moments": [{"n": k + 1, "new_picture": True, "camera": "push_in",
                         "art": arts[k % len(arts)]} for k in range(self.count)],
        }  # fmt: skip


def test_a_story_with_someone_unnamed_in_a_picture_is_sent_back_once():
    song = song_of([3.0, 2.5, 2.5])
    people = [LEO.model_dump(include={"name", "look"}), NANA.model_dump(include={"name", "look"})]
    unnamed = [ARTS[0], "a man waits at the bus stop, his hands cold", ARTS[2]]
    writer = Writes(song, people, arts=unnamed, fixed=NAMED)
    plan, _ = songwriter.draft(writer, song)
    assert len(writer.asked) == 2
    assert writer.asked[1].startswith("Fix these") and (
        "moments 2: name who is in each picture by their names from people (Leo, Nana)"
        in writer.asked[1]
    )
    assert [s.visual.art for s in plan.scenes] == NAMED
    # The story's only grandmother and its only boy are themselves without their names too.
    assert [p.name for p in songwriter.people_in(plan, "the boy hugs his nana")] == ["Leo", "Nana"]
    assert songwriter.painted(plan, "The old woman smiles").startswith(NANA.look)
    assert songwriter.people_in(plan, "a boy and the other woman") == []


def test_a_song_story_with_the_likeness_model_shows_each_persons_portrait(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    song = song_of([3.0, 2.5, 2.5])
    monkeypatch.setattr(songhook, "find", lambda *a, **k: song)
    monkeypatch.setattr(studio, "preview_sound", lambda *a, **k: None)
    with Session() as db:
        db.add(Animation(id="f1", owner_id="u1", idea="Paper Boats", style="song",
                         task={"op": "draft"}))  # fmt: skip
        db.commit()
    painters = Painters()
    writer = Writes(song, [LEO.model_dump(include={"name", "look"}),
                           NANA.model_dump(include={"name", "look"})])  # fmt: skip
    studio.run_task("f1", Session, settings, StudioRouter(writer), paint=painters.paint,
                    likeness=painters.likeness)  # fmt: skip
    with Session() as db:
        a = db.get(Animation, "f1")
        assert a.status == "ready", a.error
        plan = Plan.model_validate(a.plan)
    assert all(p.image for p in plan.song.people) and painters.words == []
    assert a.player["scenes"][0]["visual"]["image"] == plan.scenes[0].visual.image != ""


def test_another_part_of_the_song_is_written_with_its_people_as_they_look(tmp_path, monkeypatch):
    settings, Session = studio_env(tmp_path)
    song = song_of([3.0, 2.5, 2.5])
    kept_leo = LEO.model_copy(update={"art_seed": 2})
    current = faces_plan(arts=ARTS[:3], people=(kept_leo, NANA))
    current = current.model_copy(update={"song": current.song.model_copy(update={
        "choices": TWO_PARTS})})  # fmt: skip
    monkeypatch.setattr(songhook, "rechoose", lambda s, n, *a: s.model_copy(
        update={"lines": song.lines, "playing": [n]}))  # fmt: skip
    monkeypatch.setattr(studio, "preview_sound", lambda *a, **k: None)
    with Session() as db:
        db.add(Animation(id="f2", owner_id="u1", idea="Paper Boats", style="song",
                         plan=current.model_dump(mode="json"), status="ready",
                         task={"op": "hook", "choice": 1}))  # fmt: skip
        db.commit()
    # The writer keeps Leo by name but words his look its own way, and adds someone.
    writer = Writes(song, [{"name": "leo", "look": "a red-haired boy in a raincoat"},
                           {"name": "Pip", "look": "a small brown dog"}])  # fmt: skip
    painters = Painters()
    studio.run_task("f2", Session, settings, StudioRouter(writer), paint=painters.paint,
                    likeness=painters.likeness)  # fmt: skip
    asked = writer.asked[0]
    assert "The song's other reels have these people:" in asked
    assert f"- Leo: {LEO.look}" in asked and f"- Nana: {NANA.look}" in asked
    with Session() as db:
        plan = Plan.model_validate(db.get(Animation, "f2").plan)
    leo, pip = plan.song.people
    # Leo as the song's other reels have him, word for word, his portrait asked for again too.
    assert (leo.name, leo.look, leo.art_seed) == ("Leo", LEO.look, 2)
    assert pip.name == "Pip" and plan.song.playing == [1]


def test_a_story_changed_in_words_keeps_a_portrait_asked_for_again():
    plan = faces_plan(arts=ARTS[:3], people=(LEO.model_copy(update={"art_seed": 3}), SAM))
    reply = {
        "title": "Boats", "story": "s", "caption": "c",
        "people": [LEO.model_dump(include={"name", "look"}),
                   {"name": "Sam", "look": "a short man in a red jacket"}],
        "moments": [{"n": k + 1, "new_picture": True, "camera": "still", "art": ARTS[k]}
                    for k in range(3)],
    }  # fmt: skip

    class One:
        def chat_json(self, messages, schema, temperature=0.3):
            return reply

    changed, _ = songwriter.change(One(), plan, "make Sam younger")
    leo, sam = changed.song.people
    assert leo.art_seed == 3 and sam.art_seed == 0 and sam.look == "a short man in a red jacket"


def test_every_part_of_a_song_is_made_with_its_people(tmp_path):
    _, Session = studio_env(tmp_path)
    plan = faces_plan(arts=ARTS[:3], people=(LEO, NANA))
    plan = plan.model_copy(update={"song": plan.song.model_copy(update={
        "choices": TWO_PARTS, "playing": [0]})})  # fmt: skip
    with Session() as db:
        found = Animation(id="root", owner_id="u1", idea="Paper Boats", style="song",
                          plan=plan.model_dump(mode="json"), status="ready")  # fmt: skip
        db.add(found)
        db.commit()
        made = studio.new_parts(db, found)
        kept = Song.model_validate(made[0].part["song"])
    assert [(p.name, p.look) for p in kept.people] == [(LEO.name, LEO.look),
                                                       (NANA.name, NANA.look)]  # fmt: skip
    assert kept.lines == [] and kept.story == ""
