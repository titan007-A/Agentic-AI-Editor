"""Files presets use (logos, pictures, clips, music, LUTs), kept in media/assets under a
name made from their content, so the same file kept twice is stored once."""

import hashlib
import re
from pathlib import Path

from agentic_editor.settings import ROOT


def folder(media_dir: Path) -> Path:
    return media_dir / "assets"


def name_for(digest: str, original: str, fallback: str) -> str:
    stem = re.sub(r"[^\w-]+", "-", Path(original).stem).strip("-") or fallback
    return f"{digest[:12]}-{stem[:60]}{Path(original).suffix.lower()}"


def keep(data: bytes, original: str, media_dir: Path) -> Path:
    """Store `data` as an asset named after `original`; where it was kept."""
    dest = folder(media_dir) / name_for(hashlib.sha256(data).hexdigest(), original, "file")
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        partial = dest.with_name(f".{dest.name}.part")
        partial.write_bytes(data)
        partial.replace(dest)
    return dest


def preset_value(path: Path) -> str:
    """How a preset names a kept file: from the project folder when it is in it, as the
    render looks there for relative names."""
    path = path.resolve()
    return str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
