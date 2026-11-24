"""World maps for the World map style: the Earth from space and every country's shape.

The style flies a camera from country to country over a satellite picture of the Earth,
lights the country being talked about in a colour and pins a picture card on it for each
thing named. Both sources are public domain and fetched once into models/maps:

- NASA's Blue Marble (24000x12000, by way of Wikimedia Commons), cut into tiles at four
  sizes with the ocean turned the deep teal of the reference reels and the land toned
  down, so the player loads only what a frame shows;
- Natural Earth's countries at 1:10m, kept as a small table: names people say ("the UK",
  "Holland"), where the label goes, the main landmass, and outlines simplified to what a
  phone screen can show.

`attach` puts what a reel needs (its countries' outlines, colours, where each card goes
and how close the camera comes) into the player's data; the player does the rest. With
no map here yet the player still flies over a plain ocean with the countries drawn.
"""

import hashlib
import json
import logging
import math
import os
import re
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from functools import lru_cache
from pathlib import Path

import numpy as np

from agentic_editor import logs

log = logging.getLogger(__name__)

SOURCES = {
    # NASA Blue Marble ("Whole world - land and oceans 12000.jpg"), public domain.
    "earth.jpg": (
        "https://upload.wikimedia.org/wikipedia/commons/8/8f/Whole_world_-_land_and_oceans_12000.jpg",
        "e38957ed3999348670d7855569a1e6775a1b2ca789d74f15b04912889d116219",
    ),
    # Natural Earth admin 0 countries at 1:10m, public domain.
    "countries.geojson": (
        "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/"
        "v5.1.2/geojson/ne_10m_admin_0_countries.geojson",
        "239eec57ac17f100a11e2536cffc56752c318b50ae765b0918ff7aab4ce8f255",
    ),
}
VERSION = 1  # of the tiles and the table made from the sources
TILE = 500
LEVELS = 4  # the Earth 3000, 6000, 12000 and 24000 pixels wide
OCEAN_IN = (11, 10, 50)  # the Blue Marble's flat ocean
OCEAN_OUT = (0, 41, 59)  # the reference reels' deep teal
LAND_GAIN = 0.74
LAND_SATURATION = 0.88
QUALITY = 84

# Colours a country is lit in, in the order the reference reels use them (the first
# country purple, then red, green, blue...), drawn at FILL_ALPHA so the land shows
# through; two countries in a row never share one.
PALETTE = (
    "#6a12a8", "#b0141f", "#178a3c", "#2348b8", "#d97a00", "#a8158f", "#00957d", "#b9a40c",
)  # fmt: skip
FILL_ALPHA = 0.74
# How close the camera comes to a country, as a share of the view that shows all of it,
# and the least and most of the Earth (degrees of arc across the screen) it shows then.
CLOSE = 0.55
CLOSE_MIN = 4.0
CLOSE_MAX = 14.0
WIDE = 1.8
WIDE_MIN = 16.0
WIDE_MAX = 95.0
SPACING = 0.42  # cards on a country at least this share of the close view apart
ASPECT = 1920 / 1080
DIGITS = 3  # of a coordinate in the player's data

# Names people use that Natural Earth doesn't list (after `key`: lower case, no accents,
# no "the", no dots).
ALIASES = {
    "us": "USA", "usa": "USA", "america": "USA", "united states of america": "USA",
    "the states": "USA", "uk": "GBR", "britain": "GBR", "great britain": "GBR",
    "england": "GBR", "scotland": "GBR", "wales": "GBR", "holland": "NLD", "czechia": "CZE",
    "korea": "KOR", "south korea": "KOR", "republic of korea": "KOR", "north korea": "PRK",
    "russia": "RUS", "ivory coast": "CIV", "cote divoire": "CIV", "uae": "ARE",
    "emirates": "ARE", "vatican": "VAT", "burma": "MMR", "swaziland": "SWZ",
    "turkiye": "TUR", "cape verde": "CPV", "east timor": "TLS", "congo": "COG",
    "drc": "COD", "dr congo": "COD", "persia": "IRN", "taiwan": "TWN", "macau": "MAC",
    "hong kong": "HKG", "palestine": "PSX", "bosnia": "BIH", "micronesia": "FSM",
}  # fmt: skip

_build_lock = threading.Lock()


class MapError(RuntimeError):
    pass


def folder(models_dir: Path) -> Path:
    return models_dir / "maps"


def tiles_dir(models_dir: Path) -> Path:
    return folder(models_dir) / f"tiles-v{VERSION}"


def table_path(models_dir: Path) -> Path:
    return folder(models_dir) / f"countries-v{VERSION}.json"


def ready(models_dir: Path) -> bool:
    return table_path(models_dir).is_file() and (tiles_dir(models_dir) / "done").is_file()


# ---- getting and making the map ----------------------------------------------------------


def fetch(models_dir: Path, progress=None, download=None) -> Path:
    """The map, downloaded and made the first time (about 33 MB, then a minute or two
    of cutting tiles); returns its folder. Safe to call from two jobs at once."""
    progress = progress or (lambda message: None)
    out = folder(models_dir)
    if ready(models_dir):
        return out
    with _build_lock:
        if ready(models_dir):
            return out
        out.mkdir(parents=True, exist_ok=True)
        for name, (url, digest) in SOURCES.items():
            path = out / name
            if path.is_file():
                continue
            progress(f"downloading the world map ({name})")
            with logs.download_progress(f"the world map's {name}", out):
                partial = path.with_suffix(f".{os.getpid()}.part")
                (download or DOWNLOAD)(url, partial)
                found = hashlib.sha256(partial.read_bytes()).hexdigest()
                if found != digest:
                    partial.unlink(missing_ok=True)
                    raise MapError(f"{name} downloaded with the wrong contents ({found[:12]})")
                partial.replace(path)
        if not table_path(models_dir).is_file():
            progress("reading the countries")
            build_table(out / "countries.geojson", table_path(models_dir))
        if not (tiles_dir(models_dir) / "done").is_file():
            progress("cutting the world map into tiles")
            build_tiles(out / "earth.jpg", tiles_dir(models_dir))
    return out


def _download(url: str, dest: Path, tries: int = 4, pause: float = 10.0) -> None:
    """`url` into `dest`; asked again a few times when the site says it's busy (Wikimedia
    answers 429 now and then)."""
    agent = "agentic-editor-maps/1.0 (a video editor fetching public-domain map data once)"
    request = urllib.request.Request(url, headers={"User-Agent": agent})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(request, timeout=600) as answer, dest.open("wb") as f:
                while chunk := answer.read(1 << 20):
                    f.write(chunk)
            return
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or attempt == tries - 1:
                raise
        except (urllib.error.URLError, TimeoutError):
            if attempt == tries - 1:
                raise
        log.info("the map's server is busy; asking again in %.0f s", pause * (attempt + 1))
        time.sleep(pause * (attempt + 1))


DOWNLOAD = _download  # how fetch downloads (tests put a stop to it)


def tone(pixels: np.ndarray) -> np.ndarray:
    """Blue Marble pixels (HxWx3 uint8) in the reels' look: its flat navy ocean turned deep
    teal (blended by how close a pixel is to it, so coasts stay soft), the land darker and
    a little less saturated."""
    rgb = pixels.astype(np.float32)
    dist = np.sqrt(np.sum((rgb - np.array(OCEAN_IN, np.float32)) ** 2, axis=2))
    sea = np.clip((46.0 - dist) / 26.0, 0.0, 1.0)[..., None]
    grey = rgb.mean(axis=2, keepdims=True)
    land = (grey + (rgb - grey) * LAND_SATURATION) * LAND_GAIN
    # The ocean keeps a little of the picture's own grain so it isn't a flat colour.
    ocean = np.array(OCEAN_OUT, np.float32) + (rgb - np.array(OCEAN_IN, np.float32)) * 0.6
    out = sea * ocean + (1.0 - sea) * land
    return np.clip(out + 0.5, 0, 255).astype(np.uint8)


def build_tiles(source: Path, dest: Path) -> None:
    """`source` (an equirectangular picture of the Earth, 2:1) as JPEG tiles of TILE
    pixels at LEVELS sizes: dest/<level>/<x>_<y>.jpg, level 0 the smallest."""
    from PIL import Image

    Image.MAX_IMAGE_PIXELS = None
    partial = dest.with_name(dest.name + ".part")
    with Image.open(source) as opened:
        full = opened.convert("RGB")
    top = 3000 * 2 ** (LEVELS - 1)
    if full.width != top:
        full = full.resize((top, top // 2), Image.Resampling.LANCZOS)
    level_image = full
    for level in range(LEVELS - 1, -1, -1):
        width = 3000 * 2**level
        if level_image.width != width:
            level_image = level_image.reduce(2)
        place = partial / str(level)
        place.mkdir(parents=True, exist_ok=True)
        for y in range(0, level_image.height, TILE):
            for x in range(0, level_image.width, TILE):
                piece = level_image.crop((x, y, x + TILE, y + TILE))
                toned = Image.fromarray(tone(np.asarray(piece)))
                toned.save(place / f"{x // TILE}_{y // TILE}.jpg", quality=QUALITY)
    (partial / "done").write_text(json.dumps({"tile": TILE, "levels": LEVELS}))
    if dest.exists():
        import shutil

        shutil.rmtree(dest)
    partial.rename(dest)


# ---- the countries' table ----------------------------------------------------------------


def key(name: str) -> str:
    """A name as compared: lower case, no accents, no "the", no punctuation."""
    text = unicodedata.normalize("NFKD", str(name)).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-z0-9 ]+", " ", text.lower().replace(".", "").replace("'", ""))
    text = " ".join(text.split())
    return text.removeprefix("the ").strip()


def _area(ring: np.ndarray) -> float:
    x, y = ring[:, 0], ring[:, 1]
    return 0.5 * float(np.dot(x, np.roll(y, -1)) - np.dot(y, np.roll(x, -1)))


def simplify(points: np.ndarray, tolerance: float) -> np.ndarray:
    """Douglas-Peucker on one ring (Nx2), its first and last point kept."""
    if len(points) <= 4:
        return points
    keep = np.zeros(len(points), dtype=bool)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        a, b = stack.pop()
        if b <= a + 1:
            continue
        seg = points[b] - points[a]
        rest = points[a + 1 : b] - points[a]
        length = math.hypot(*seg)
        if length == 0:
            dist = np.hypot(rest[:, 0], rest[:, 1])
        else:
            dist = np.abs(seg[0] * rest[:, 1] - seg[1] * rest[:, 0]) / length
        i = int(np.argmax(dist))
        if dist[i] > tolerance:
            keep[a + 1 + i] = True
            stack += [(a, a + 1 + i), (a + 1 + i, b)]
    return points[keep]


def _polygons(geometry: dict) -> list[list[np.ndarray]]:
    if geometry["type"] == "Polygon":
        polys = [geometry["coordinates"]]
    elif geometry["type"] == "MultiPolygon":
        polys = geometry["coordinates"]
    else:
        return []
    return [[np.asarray(r, dtype=np.float64)[:, :2] for r in poly] for poly in polys]


def _box(rings) -> list[float]:
    pts = np.concatenate(rings)
    return [float(pts[:, 0].min()), float(pts[:, 1].min()), float(pts[:, 0].max()),
            float(pts[:, 1].max())]  # fmt: skip


def _near(a: list[float], b: list[float], gap: float) -> bool:
    return not (b[0] > a[2] + gap or b[2] < a[0] - gap or b[1] > a[3] + gap or b[3] < a[1] - gap)


def country_entry(props: dict, geometry: dict) -> dict | None:
    """One country of the table: its names, label point, main landmass and outlines."""
    polys = _polygons(geometry)
    if not polys:
        return None
    areas = [abs(_area(p[0])) for p in polys]
    biggest = int(np.argmax(areas))
    main_box = _box([polys[biggest][0]])
    # The main landmass and what lies close to it (Japan's other islands, Denmark's
    # Zealand), not far-off territories (Alaska, Hawaii, French Guiana).
    reach = max(0.6, 0.05 * max(main_box[2] - main_box[0], main_box[3] - main_box[1]))
    changed = True
    while changed:
        changed = False
        for poly, area in zip(polys, areas, strict=True):
            box = _box([poly[0]])
            inside = all(
                main_box[i] <= box[i] if i < 2 else main_box[i] >= box[i] for i in range(4)
            )
            if not inside and area > areas[biggest] * 0.002 and _near(main_box, box, reach):
                main_box = [min(main_box[0], box[0]), min(main_box[1], box[1]),
                            max(main_box[2], box[2]), max(main_box[3], box[3])]  # fmt: skip
                changed = True
    extent = max(main_box[2] - main_box[0], main_box[3] - main_box[1])
    tolerance = min(0.06, max(0.004, extent / 1800))
    smallest = max(tolerance**2 * 6, sum(areas) * 0.00015)
    rings = []
    for poly, area in zip(polys, areas, strict=True):
        if area < smallest:
            continue
        for n, ring in enumerate(poly):
            if n and abs(_area(ring)) < smallest:
                continue  # a hole too small to see
            thin = simplify(ring, tolerance)
            if len(thin) >= 4:
                rings.append([round(float(v), DIGITS) for v in thin.reshape(-1)])
    if not rings:
        return None
    names = {
        key(props.get(f) or "")
        for f in (
            "NAME",
            "NAME_LONG",
            "ADMIN",
            "NAME_EN",
            "FORMAL_EN",
            "NAME_SORT",
            "BRK_NAME",
            "GEOUNIT",
            "NAME_ALT",
            "ABBREV",
            "NAME_CIAWF",
        )
    }
    names.discard("")
    name = str(props.get("NAME") or props.get("ADMIN") or "")
    return {
        "name": name,
        "short": name if len(name) <= 11 else re.sub(r"[^A-Za-z]", "", props.get("ABBREV") or name),
        "names": sorted(names),
        "iso2": props.get("ISO_A2_EH") if len(str(props.get("ISO_A2_EH") or "")) == 2 else "",
        "label": [
            round(float(props.get("LABEL_X") or 0), DIGITS),
            round(float(props.get("LABEL_Y") or 0), DIGITS),
        ],
        "main": [round(v, DIGITS) for v in main_box],
        "box": [round(v, DIGITS) for v in _box([np.asarray(polys[biggest][0])])],
        "rings": rings,
        "rank": int(props.get("LABELRANK") or 9),
    }


def build_table(geojson: Path, dest: Path) -> dict:
    data = json.loads(geojson.read_text(encoding="utf-8"))
    table: dict[str, dict] = {}
    for feature in data["features"]:
        props = feature.get("properties") or {}
        code = props.get("ADM0_A3") or props.get("ISO_A3")
        if not code or code == "-99" or not feature.get("geometry"):
            continue
        entry = country_entry(props, feature["geometry"])
        if entry is not None:
            table[code] = entry
    partial = dest.with_suffix(".part")
    partial.write_text(json.dumps(table, separators=(",", ":")), encoding="utf-8")
    partial.replace(dest)
    return table


@lru_cache(maxsize=2)
def _table(path: str, stamp: float) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def table(models_dir: Path) -> dict:
    path = table_path(models_dir)
    if not path.is_file():
        return {}
    return _table(str(path), path.stat().st_mtime)


def find(name: str, countries: dict) -> str | None:
    """The country a spoken name means ("The United States", "the UK", "Holland"), as its
    code in `countries`; None when no country goes by it."""
    wanted = key(name)
    if not wanted:
        return None
    if wanted in ALIASES and ALIASES[wanted] in countries:
        return ALIASES[wanted]
    best = None
    for code, entry in countries.items():
        # A sovereign country before a territory of the same name.
        if wanted in entry["names"] and (
            best is None or entry.get("rank", 9) < countries[best].get("rank", 9)
        ):
            best = code
    if best:
        return best
    # "Japan's" or "the republic of India": a known name inside what was said.
    for code, entry in sorted(countries.items(), key=lambda kv: kv[1].get("rank", 9)):
        for known in entry["names"]:
            if len(known) > 3 and re.search(rf"\b{re.escape(known)}\b", wanted):
                return code
    return None


# ---- where things go -----------------------------------------------------------------------


def _rings_of(entry: dict) -> list[np.ndarray]:
    return [np.asarray(r, dtype=np.float64).reshape(-1, 2) for r in entry["rings"]]


def inside(points: np.ndarray, rings: list[np.ndarray]) -> np.ndarray:
    """Which of `points` (Nx2 lon/lat) lie inside the outlines (even-odd, so holes are out)."""
    result = np.zeros(len(points), dtype=bool)
    px, py = points[:, 0][:, None], points[:, 1][:, None]
    for ring in rings:
        x0, y0 = ring[:, 0][None, :], ring[:, 1][None, :]
        x1, y1 = np.roll(ring[:, 0], -1)[None, :], np.roll(ring[:, 1], -1)[None, :]
        crosses = (y0 > py) != (y1 > py)
        with np.errstate(divide="ignore", invalid="ignore"):
            at = x0 + (py - y0) * (x1 - x0) / (y1 - y0)
        result ^= (np.sum(crosses & (px < at), axis=1) % 2).astype(bool)
    return result


def views(entry: dict) -> dict:
    """How the camera shows a country: all of it (`fit`: centre lon, lat and the degrees
    of arc across the screen), close (degrees across while on one card) and wide (its
    part of the world, as the camera arrives)."""
    x0, y0, x1, y1 = entry["main"]
    lat = (y0 + y1) / 2
    across = (x1 - x0) * math.cos(math.radians(lat))
    tall = (y1 - y0) / ASPECT
    fit = max(across, tall, 1.5) * 1.18
    return {
        "fit": [round((x0 + x1) / 2, DIGITS), round(lat, DIGITS), round(fit, DIGITS)],
        "close": round(min(CLOSE_MAX, max(CLOSE_MIN, fit * CLOSE)), DIGITS),
        "wide": round(min(WIDE_MAX, max(WIDE_MIN, fit * WIDE)), DIGITS),
    }


def spots(entry: dict, count: int, close: float, seed: int = 0) -> list[list[float]]:
    """Where `count` cards go on a country: inside it where it can, spread so no two are
    nearer than SPACING of the close view, the first near its label, then each the
    nearest of the rest (the camera's path from card to card stays short)."""
    if count <= 0:
        return []
    x0, y0, x1, y1 = entry["main"]
    lat = (y0 + y1) / 2
    squash = max(0.2, math.cos(math.radians(lat)))
    pad = close * 0.25
    rng = np.random.default_rng(seed)
    grid = 28
    xs = np.linspace(x0 - pad / squash, x1 + pad / squash, grid)
    ys = np.linspace(y0 - pad, y1 + pad, grid)
    cand = np.array([[x, y] for y in ys for x in xs])
    cand += rng.uniform(-0.3, 0.3, cand.shape) * [(xs[1] - xs[0]), (ys[1] - ys[0])]
    within = inside(cand, _rings_of(entry))
    label = np.asarray(entry["label"], dtype=np.float64)

    def apart(a: np.ndarray, b: np.ndarray) -> np.ndarray:
        d = a - b
        return np.hypot(d[..., 0] * squash, d[..., 1])

    gap = close * SPACING
    chosen: list[np.ndarray] = []
    pool = cand[within] if within.sum() >= count else cand
    order = np.argsort(apart(pool, label))
    for ok in (True, False):  # inside it first, then anywhere near, then closer together
        for want in (gap, gap * 0.7, gap * 0.45):
            for i in order:
                if len(chosen) >= count:
                    break
                p = pool[i]
                if all(apart(p, c) >= want for c in chosen):
                    chosen.append(p)
            if len(chosen) >= count:
                break
        if len(chosen) >= count or not ok:
            break
        pool, order = cand, np.argsort(apart(cand, label))
    while len(chosen) < count:  # a tiny country with many things: stacked near the label
        chosen.append(label + np.array([0.0, -gap * 0.3 * len(chosen)]))
    # A short path: start nearest the label, then always the nearest one left.
    path = [chosen.pop(0)]
    while chosen:
        i = int(np.argmin([apart(path[-1], c) for c in chosen]))
        path.append(chosen.pop(i))
    return [[round(float(p[0]), DIGITS), round(float(p[1]), DIGITS)] for p in path]


def colours(codes: list[str | None]) -> list[str]:
    """A palette colour per scene's country, in PALETTE's order as countries come (the
    same country keeps its colour); "" for a scene with no country."""
    out: list[str] = []
    given: dict[str, str] = {}
    turn = 0
    for code in codes:
        if not code:
            out.append("")
            continue
        if code not in given:
            given[code] = PALETTE[turn % len(PALETTE)]
            turn += 1
        out.append(given[code])
    return out


def attach(data: dict, plan, models_dir: Path) -> dict:
    """The player's data with the map a World map reel needs (`data["map"]`): every
    country scene's outline, colour, card spots and camera views. Other styles' data is
    returned as it is."""
    if getattr(plan, "style", "") != "map":
        return data
    countries = table(models_dir)
    codes: list[str | None] = []
    for scene in data["scenes"]:
        visual = scene.get("visual") or {}
        code = None
        if scene.get("kind") == "country" and countries:
            code = find(visual.get("country") or "", countries) or find(
                visual.get("text") or "", countries
            )
        codes.append(code)
    palette = colours(codes)
    shapes: dict[str, dict] = {}
    per_scene: list[dict | None] = []
    for i, (scene, code) in enumerate(zip(data["scenes"], codes, strict=True)):
        if not code:
            per_scene.append(None)
            continue
        entry = countries[code]
        if code not in shapes:
            shapes[code] = {
                "name": entry["name"],
                "short": entry.get("short") or entry["name"],
                "label": entry["label"],
                "rings": entry["rings"],
            }
        view = views(entry)
        items = (scene.get("visual") or {}).get("items") or []
        per_scene.append(
            {
                "code": code,
                "color": palette[i],
                "spots": spots(entry, len(items), view["close"], seed=i),
                **view,
            }
        )
    return {
        **data,
        "map": {
            "version": VERSION,
            "tiles": ready(models_dir),
            "tile": TILE,
            "levels": LEVELS,
            "fillAlpha": FILL_ALPHA,
            "countries": shapes,
            "scenes": per_scene,
        },
    }


def missing(plan, models_dir: Path) -> list[str]:
    """The country scenes of a World map reel whose country isn't on the map (a note for
    the person: those scenes show the card without flying anywhere)."""
    countries = table(models_dir)
    if getattr(plan, "style", "") != "map" or not countries:
        return []
    return [
        s.visual.text or s.visual.country
        for s in plan.scenes
        if s.visual.kind == "country"
        and not (find(s.visual.country, countries) or find(s.visual.text, countries))
    ]
