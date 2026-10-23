"""What a font file says about itself, and the Noto fonts that draw what the bundled ones
can't: Devanagari, CJK, Arabic, emoji and the rest, fetched once into the models folder
the first time a reel needs them.

Read straight from the font's tables, so no font library is needed beyond Pillow.
"""

import logging
import os
import shutil
import struct
import unicodedata
import urllib.request
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

log = logging.getLogger(__name__)

NOTO_URL = (
    "https://raw.githubusercontent.com/notofonts/notofonts.github.io/main/fonts/"
    "{name}/hinted/ttf/{name}-{weight}.ttf"
)
CJK_URL = (
    "https://raw.githubusercontent.com/notofonts/noto-cjk/main/Sans/SubsetOTF/"
    "{region}/NotoSans{region}-{weight}.otf"
)
EMOJI_URL = (
    "https://raw.githubusercontent.com/google/fonts/main/ofl/notoemoji/NotoEmoji%5Bwght%5D.ttf"
)

# Unicode blocks and the Noto font for each. "CJK" is settled by the language.
SCRIPTS = [
    (0x0370, 0x052F, "NotoSans"),  # Greek, Cyrillic
    (0x1E00, 0x1EFF, "NotoSans"),  # Vietnamese and other accented Latin
    (0x0530, 0x058F, "NotoSansArmenian"),
    (0x0590, 0x05FF, "NotoSansHebrew"),
    (0x0600, 0x06FF, "NotoSansArabic"),
    (0x0750, 0x077F, "NotoSansArabic"),
    (0x08A0, 0x08FF, "NotoSansArabic"),
    (0xFB50, 0xFDFF, "NotoSansArabic"),
    (0xFE70, 0xFEFF, "NotoSansArabic"),
    (0x0900, 0x097F, "NotoSansDevanagari"),
    (0xA8E0, 0xA8FF, "NotoSansDevanagari"),
    (0x0980, 0x09FF, "NotoSansBengali"),
    (0x0A00, 0x0A7F, "NotoSansGurmukhi"),
    (0x0A80, 0x0AFF, "NotoSansGujarati"),
    (0x0B00, 0x0B7F, "NotoSansOriya"),
    (0x0B80, 0x0BFF, "NotoSansTamil"),
    (0x0C00, 0x0C7F, "NotoSansTelugu"),
    (0x0C80, 0x0CFF, "NotoSansKannada"),
    (0x0D00, 0x0D7F, "NotoSansMalayalam"),
    (0x0D80, 0x0DFF, "NotoSansSinhala"),
    (0x0E00, 0x0E7F, "NotoSansThai"),
    (0x0E80, 0x0EFF, "NotoSansLao"),
    (0x1000, 0x109F, "NotoSansMyanmar"),
    (0x10A0, 0x10FF, "NotoSansGeorgian"),
    (0x1200, 0x139F, "NotoSansEthiopic"),
    (0x1780, 0x17FF, "NotoSansKhmer"),
    (0x1100, 0x11FF, "CJK-KR"),
    (0x3130, 0x318F, "CJK-KR"),
    (0xAC00, 0xD7AF, "CJK-KR"),
    (0x3040, 0x30FF, "CJK-JP"),
    (0x31F0, 0x31FF, "CJK-JP"),
    (0x2E80, 0x2FDF, "CJK"),
    (0x3000, 0x303F, "CJK"),
    (0x3400, 0x4DBF, "CJK"),
    (0x4E00, 0x9FFF, "CJK"),
    (0xF900, 0xFAFF, "CJK"),
    (0xFF00, 0xFFEF, "CJK"),
    (0x20000, 0x2FA1F, "CJK"),
    (0x2190, 0x21FF, "NotoEmoji"),
    (0x2300, 0x23FF, "NotoEmoji"),
    (0x2600, 0x27BF, "NotoEmoji"),
    (0x2B00, 0x2BFF, "NotoEmoji"),
    (0x1F000, 0x1FAFF, "NotoEmoji"),
]
LAST_RESORT = ["NotoSans", "NotoEmoji"]
REGIONS = {
    "ja": "JP", "japanese": "JP", "ko": "KR", "korean": "KR", "zh-hant": "TC", "zh-tw": "TC",
    "zh-hk": "TC", "traditional chinese": "TC", "cantonese": "TC", "zh": "SC", "chinese": "SC",
    "mandarin": "SC", "simplified chinese": "SC",
}  # fmt: skip
HEAVY = 0.4  # share of "HOLE"'s box a font's ink fills, past which it reads as bold


class MissingGlyphs(ValueError):
    """No font at hand draws some of the text; drawing it would show empty boxes."""


def _tables(data: bytes) -> dict[str, tuple[int, int]]:
    """Offset and length of each table, for a font file or the first font of a collection."""
    start = struct.unpack(">I", data[12:16])[0] if data[:4] == b"ttcf" else 0
    (count,) = struct.unpack(">H", data[start + 4 : start + 6])
    tables = {}
    for i in range(count):
        entry = start + 12 + 16 * i
        tag = data[entry : entry + 4].decode("latin-1")
        offset, length = struct.unpack(">II", data[entry + 8 : entry + 16])
        tables[tag] = (offset, length)
    return tables


@lru_cache
def libass_scale(path: Path) -> float:
    """Pillow's size for this font that draws it as large as libass draws size 1.

    libass sizes a font by its line height, taken from the Windows ascent and descent the
    way GDI does, where Pillow sizes it by its em; for Anton that is 0.58 of the size."""
    data = path.read_bytes()
    tables = _tables(data)
    head = tables["head"][0]
    (units,) = struct.unpack(">H", data[head + 18 : head + 20])
    ascent = descent = 0
    if "OS/2" in tables:
        os2 = tables["OS/2"][0]
        ascent, descent = struct.unpack(">hh", data[os2 + 74 : os2 + 78])
    if ascent + descent <= 0:
        hhea = tables["hhea"][0]
        ascent, low = struct.unpack(">hh", data[hhea + 4 : hhea + 8])
        descent = -low
    return units / (ascent + descent)


@lru_cache
def coverage(path: Path) -> frozenset[int]:
    """Every character the font has a glyph for, from its Unicode character map."""
    data = path.read_bytes()
    base = _tables(data)["cmap"][0]
    (count,) = struct.unpack(">H", data[base + 2 : base + 4])
    subtables = {}
    for i in range(count):
        entry = base + 4 + 8 * i
        platform, encoding, offset = struct.unpack(">HHI", data[entry : entry + 8])
        (form,) = struct.unpack(">H", data[base + offset : base + offset + 2])
        subtables[(platform, encoding, form)] = base + offset
    for key in ((3, 10, 12), (0, 4, 12), (0, 6, 12), (3, 1, 4), (0, 3, 4), (0, 4, 4)):
        if key in subtables:
            reader = _format12 if key[2] == 12 else _format4
            return frozenset(reader(data, subtables[key]))
    return frozenset()


def _format12(data: bytes, at: int) -> Iterable[int]:
    (groups,) = struct.unpack(">I", data[at + 12 : at + 16])
    for g in range(groups):
        first, last, glyph = struct.unpack(">III", data[at + 16 + 12 * g : at + 28 + 12 * g])
        yield from range(first if glyph else first + 1, last + 1)


def _format4(data: bytes, at: int) -> Iterable[int]:
    (doubled,) = struct.unpack(">H", data[at + 6 : at + 8])
    n = doubled // 2
    ends = struct.unpack(f">{n}H", data[at + 14 : at + 14 + doubled])
    starts = struct.unpack(f">{n}H", data[at + 16 + doubled : at + 16 + 2 * doubled])
    deltas = struct.unpack(f">{n}h", data[at + 16 + 2 * doubled : at + 16 + 3 * doubled])
    offsets_at = at + 16 + 3 * doubled
    offsets = struct.unpack(f">{n}H", data[offsets_at : offsets_at + doubled])
    for k in range(n):
        for code in range(starts[k], ends[k] + 1):
            if code == 0xFFFF:
                continue
            if offsets[k] == 0:
                glyph = (code + deltas[k]) & 0xFFFF
            else:
                where = offsets_at + 2 * k + offsets[k] + 2 * (code - starts[k])
                (glyph,) = struct.unpack(">H", data[where : where + 2])
                glyph = (glyph + deltas[k]) & 0xFFFF if glyph else 0
            if glyph:
                yield code


@lru_cache
def weight_class(path: Path) -> int:
    data = path.read_bytes()
    tables = _tables(data)
    if "OS/2" not in tables:
        return 400
    os2 = tables["OS/2"][0]
    return struct.unpack(">H", data[os2 + 4 : os2 + 6])[0]


@lru_cache
def heavy(path: Path) -> bool:
    """Whether the font looks bold. Anton calls itself regular but is as heavy as they come,
    so this goes by how much of its letters is ink, as well as by its stated weight."""
    if weight_class(path) >= 600:
        return True
    font = ImageFont.truetype(str(path), 100)
    x0, y0, x1, y1 = font.getbbox("HOLE")
    image = Image.new("L", (x1 + 4, y1 + 4))
    ImageDraw.Draw(image).text((0, 0), "HOLE", font=font, fill=255)
    return float((np.array(image)[y0:y1, x0:x1] > 128).mean()) >= HEAVY


def ignorable(ch: str) -> bool:
    """Characters that need no glyph of their own: spaces, joiners, variation selectors."""
    code = ord(ch)
    return (
        ch.isspace()
        or unicodedata.category(ch) in ("Cc", "Cf")
        or 0xFE00 <= code <= 0xFE0F
        or 0xE0100 <= code <= 0xE01EF
    )


def _region(language: str | None, text: str) -> str:
    """Which regional CJK font draws Han characters the way readers of the text expect."""
    if language and (found := REGIONS.get(language.strip().lower())):
        return found
    if any(0x3040 <= ord(c) <= 0x30FF for c in text):
        return "JP"
    if any(0xAC00 <= ord(c) <= 0xD7AF for c in text):
        return "KR"
    return "SC"


def candidates(ch: str, language: str | None, text: str) -> list[str]:
    """Noto fonts to try for one character, the one made for its script first."""
    code = ord(ch)
    found = next((name for low, high, name in SCRIPTS if low <= code <= high), None)
    if found == "CJK":
        found = f"CJK-{_region(language, text)}"
    return list(dict.fromkeys([*([found] if found else []), *LAST_RESORT]))


def fetch(name: str, weight: str, folder: Path) -> Path:
    """A Noto font from the models folder, downloaded the first time it is needed."""
    if name.startswith("CJK-"):
        region = name.removeprefix("CJK-")
        dest = folder / f"NotoSans{region}-{weight}.otf"
        url = CJK_URL.format(region=region, weight=weight)
    elif name == "NotoEmoji":
        dest, url = folder / "NotoEmoji.ttf", EMOJI_URL
    else:
        dest = folder / f"{name}-{weight}.ttf"
        url = NOTO_URL.format(name=name, weight=weight)
    if not dest.exists():
        folder.mkdir(parents=True, exist_ok=True)
        log.info("downloading the %s font", dest.stem)
        partial = dest.with_name(f".{dest.name}.{os.getpid()}")
        try:
            _download(url, partial)
            os.replace(partial, dest)
        except OSError as exc:
            partial.unlink(missing_ok=True)
            raise MissingGlyphs(f"couldn't download the {dest.stem} font: {exc}") from exc
    return dest


def _download(url: str, dest: Path) -> None:
    urllib.request.urlretrieve(url, dest)


def chain(
    primary: Path, texts: Iterable[str], folder: Path, language: str | None = None
) -> list[Path]:
    """The primary font, then a Noto font for each script in the texts it can't draw.
    Raises MissingGlyphs if some character is in none of them."""
    texts = list(texts)
    needed = sorted({ch for text in texts for ch in text if not ignorable(ch)})
    fonts = [primary]
    missing = [ch for ch in needed if ord(ch) not in coverage(primary)]
    if not missing:
        return fonts
    weight = "Bold" if heavy(primary) else "Regular"
    joined = "".join(texts)
    unresolved = []
    for ch in missing:
        if any(ord(ch) in coverage(f) for f in fonts):
            continue
        for name in candidates(ch, language, joined):
            path = fetch(name, weight, folder)
            if ord(ch) in coverage(path):
                fonts.append(path)
                break
        else:
            unresolved.append(ch)
    if unresolved:
        shown = ", ".join(f"{c!r} (U+{ord(c):04X})" for c in unresolved[:8])
        raise MissingGlyphs(f"no font can draw {shown}")
    return fonts


def runs(text: str, fonts: list[Path]) -> list[tuple[str, Path]]:
    """The text in pieces, each with the first of the fonts that draws it. Marks and
    joiners stay with the piece before them, so a cluster is never split, and so does a
    space between two characters of the same piece, so a phrase in another script is
    shaped and ordered as one; other spaces are the first font's (an emoji font's space is
    as wide as an emoji)."""

    def own(ch: str) -> Path | None:
        if ignorable(ch) or unicodedata.category(ch).startswith("M"):
            return None
        return next((f for f in fonts if ord(ch) in coverage(f)), fonts[0])

    owners = [own(ch) for ch in text]
    out: list[tuple[str, Path]] = []
    for i, ch in enumerate(text):
        font = owners[i]
        if font is None and out:
            font = out[-1][1]
            if ch.isspace():
                following = next((f for f in owners[i + 1 :] if f is not None), None)
                if following != font or font.stem.startswith("NotoEmoji"):
                    font = fonts[0] if ord(ch) in coverage(fonts[0]) else font
        font = font or fonts[0]
        if out and out[-1][1] == font:
            out[-1] = (out[-1][0] + ch, font)
        else:
            out.append((ch, font))
    return out


def gather(paths: list[Path], folder: Path) -> Path:
    """One folder holding these fonts, as libass reads fonts from a single folder."""
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.iterdir():
        old.unlink()
    for path in paths:
        link = folder / path.name
        try:
            link.symlink_to(path.resolve())
        except OSError:
            shutil.copyfile(path, link)
    return folder
