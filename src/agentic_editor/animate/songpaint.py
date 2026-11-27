"""A Song story's pictures (style "song"): each moment painted with its people in it.

Painted one by one from words alone, the same person changed from picture to picture (a
grandfather with a beard in one and clean-shaven in the next, a close-up with no face).
Where the likeness model is (FLUX.2 [klein] 4B, images.paint_likeness), each person named in
the story's moments is painted once as a portrait from their look (full length, alone on
plain grey, in the reel's look), and every moment is painted from the portraits of the people
named in it (at most MOST_REFS), so they keep their face, hair and clothes; one model paints
the whole reel, the moments with nobody in them too. Portraits are kept by what makes them
(the look, and the reel's seed, which comes from the song), so the song's other reels
(another part, two parts joined, every part) use them again. Without the likeness model, or
for a picture it couldn't paint, Z-Image paints the moment from the words, as before.
"""

import logging
import time

from agentic_editor.animate import images, songwriter
from agentic_editor.animate.plan import Person, Plan
from agentic_editor.settings import Settings

log = logging.getLogger(__name__)

# A portrait: tall enough for a whole person, smaller than a moment (it paints faster, and
# a moment painted from it reads fewer of its tokens).
PORTRAIT_SIZE = (576, 1024)
# The most portraits one picture is painted from; anyone else in it is painted from words.
MOST_REFS = 3
# The reel's look without its night and its dark top: a portrait's colours stay true.
PORTRAIT_LOOK = (
    "flat 2D vector illustration, simple stylised character with thick dark outlines, muted "
    "colours, soft even light"
)
FEATURES = "clearly drawn with eyes, eyebrows, nose and mouth"
KEEP = (
    "Each person from a reference image keeps exactly the face, hair, beard or clean-shaven "
    "chin, glasses, skin tone, build and clothes of their image"
)


def portrait_prompt(person: Person) -> str:
    """What a person's portrait is painted from: their look, whole, alone and plain."""
    return (
        f"A full-length character portrait of {person.look.strip().rstrip('.')}, standing "
        "straight and facing the viewer, the whole body from head to shoes in the picture, "
        f"arms relaxed, the face {FEATURES}, alone on a plain flat light grey background, "
        f"nothing else in the picture. {PORTRAIT_LOOK}, {images.NEVER}"
    )


def moment_prompt(plan: Plan, art: str, refs: list[Person]) -> str:
    """What the likeness model paints for a moment: its art with each person written out as
    they look, those painted from a portrait named by their reference image (image 1, 2...)."""
    marks = {p.name: f"the person in image {k}" for k, p in enumerate(refs, 1)}
    scene = songwriter.painted(plan, art, marks).strip().rstrip(".")
    keep = f"{KEEP}. " if refs else ""
    return f"{scene}. {keep}{images.LOOKS[songwriter.STYLE]}, every face {FEATURES}, {images.NEVER}"


def paint(plan: Plan, settings: Settings, paint, likeness, progress) -> tuple[Plan, list[str]]:
    """`plan` with each moment's picture painted (kept ones reused) and each person's
    portrait where the likeness model painted one; the notes to show."""
    song = plan.song
    prompts = [songwriter.painted(plan, s.visual.art) for s in plan.scenes]
    base = song.video if song is not None else plan.title
    seeds = [images.seed_of(base, s.visual.art_seed) for s in plan.scenes]
    wanted = [i for i, p in enumerate(prompts) if p.strip()]
    if not wanted:
        return plan, []
    names = [""] * len(plan.scenes)
    portraits: dict[str, str] = {}
    notes: list[str] = []
    if likeness is not None:
        portraits, names, notes = _with_likeness(plan, wanted, base, settings, likeness, progress)
    left = [i for i in wanted if not names[i]]
    tried = likeness is not None
    if left and paint is images.paint and not images.available(settings):
        if not tried:  # the pictures stay as they were; no portrait stands for a look now
            note = "painted pictures need the image model (installed on the Mac)"
            if song is None:
                return plan, [note]
            people = [p.model_copy(update={"image": ""}) for p in song.people]
            return plan.model_copy(update={"song": song.model_copy(update={"people": people})}), [
                note
            ]
    elif left:
        todo = list(dict.fromkeys((prompts[i], seeds[i]) for i in left))
        started = time.time()
        painted = paint([images.Picture(p, s) for p, s in todo], plan.style, settings, progress)
        by_key = dict(zip(todo, painted, strict=True))
        for i in left:
            names[i] = by_key.get((prompts[i], seeds[i]), "")
        log.info("song story: %d pictures painted from words in %.0f s", len(todo),
                 time.time() - started)  # fmt: skip
        if tried:
            notes.append(f"{len(todo)} pictures were painted from words alone (the likeness "
                         "model couldn't paint them)")  # fmt: skip
    scenes = [
        s.model_copy(update={"visual": s.visual.model_copy(update={"image": names[i]})})
        for i, s in enumerate(plan.scenes)
    ]
    if song is not None:
        people = [p.model_copy(update={"image": portraits.get(p.name, "")}) for p in song.people]
        song = song.model_copy(update={"people": people})
    asked = len({(prompts[i], seeds[i]) for i in wanted})
    missing = len({(prompts[i], seeds[i]) for i in wanted if not names[i]})
    if missing:
        notes.append(f"{missing} of {asked} pictures couldn't be painted")
    return plan.model_copy(update={"scenes": scenes, "song": song}), notes


def _with_likeness(
    plan: Plan, wanted: list[int], base: str, settings: Settings, likeness, progress
) -> tuple[dict[str, str], list[str], list[str]]:
    """The portraits of the people in the moments (by name) and the moments painted from
    them by the likeness model ("" where one couldn't be), and what to tell."""
    folder = images.folder(settings)
    who = {i: songwriter.people_in(plan, plan.scenes[i].visual.art) for i in wanted}
    cast = list({p.name: p for i in wanted for p in who[i]}.values())
    portraits: dict[str, str] = {}
    notes: list[str] = []
    if cast:
        started = time.time()
        asked = [images.Picture(portrait_prompt(p), images.seed_of(base, p.art_seed)) for p in cast]
        made = likeness(asked, settings, progress, size=PORTRAIT_SIZE, what="portraits",
                        why="paint the story's people")  # fmt: skip
        portraits = {p.name: name for p, name in zip(cast, made, strict=True) if name}
        log.info("song story: %d portraits in %.0f s (%d painted)", len(cast),
                 time.time() - started, len(portraits))  # fmt: skip
        if not portraits:  # the painter itself is failing: words alone, as without it
            notes.append("the people's portraits couldn't be painted; the pictures are painted "
                         "from words alone")  # fmt: skip
            return {}, [""] * len(plan.scenes), notes
        if len(portraits) < len(cast):
            lost = ", ".join(p.name for p in cast if p.name not in portraits)
            notes.append(f"no portrait of {lost}: painted from words in their pictures")
    keys: dict[int, tuple] = {}
    for i in wanted:
        refs = [p for p in who[i] if p.name in portraits][:MOST_REFS]
        prompt = moment_prompt(plan, plan.scenes[i].visual.art, refs)
        paths = tuple(str(folder / portraits[p.name]) for p in refs)
        keys[i] = (prompt, images.seed_of(base, plan.scenes[i].visual.art_seed), paths)
    todo = list(dict.fromkeys(keys.values()))
    started = time.time()
    made = likeness([images.Picture(p, s, refs=r) for p, s, r in todo], settings, progress,
                    size=images.SIZE, what="pictures", why="paint the story")  # fmt: skip
    painted = dict(zip(todo, made, strict=True))
    log.info("song story: %d pictures from %d portraits in %.0f s (%d painted)", len(todo),
             len(portraits), time.time() - started, sum(1 for n in made if n))  # fmt: skip
    names = [painted.get(keys[i], "") if i in keys else "" for i in range(len(plan.scenes))]
    return portraits, names, notes
