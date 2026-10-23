"""What a font file says about itself: its metrics and which characters it can draw.

Read straight from the font's tables, so no font library is needed beyond Pillow.
"""

import struct
from functools import lru_cache
from pathlib import Path


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
