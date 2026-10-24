"""Moving the editor to another machine. Finished videos, their thumbnails and downloaded
sources are stored by absolute path, so once the media folder has been copied across those
paths are rewritten from where the data was to where it is now."""

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy.orm import sessionmaker

from agentic_editor.db import Feed, FeedItem, Output

log = logging.getLogger(__name__)


@dataclass
class Moved:
    outputs: int = 0
    sources: int = 0
    feed_items: int = 0
    # Folder feeds whose folder doesn't exist here: watched folders aren't copied.
    missing_folders: list[str] = field(default_factory=list)


def moved(path: str | None, old: str, new: str) -> str | None:
    """`path` with the `old` prefix replaced by `new`; anything else unchanged."""
    if path is None or not (path == old or path.startswith(old + "/")):
        return path
    return new + path[len(old) :]


def relocate(Session: sessionmaker, media_dir: Path, old: str, new: str) -> Moved:
    old, new = old.rstrip("/"), new.rstrip("/")
    result = Moved()
    with Session() as db:
        for out in db.query(Output):
            path, thumbnail = moved(out.path, old, new), moved(out.thumbnail, old, new)
            if (path, thumbnail) != (out.path, out.thumbnail):
                out.path, out.thumbnail = path, thumbnail
                result.outputs += 1
        for item in db.query(FeedItem):
            source = moved(item.source, old, new)
            if source != item.source:
                item.source = source
                result.feed_items += 1
        for feed in db.query(Feed).filter(Feed.kind == "folder"):
            if not Path(feed.location).is_dir():
                result.missing_folders.append(f"{feed.name}: {feed.location}")
        db.commit()
    for meta_file in (media_dir / "sources").glob("*/source.json"):
        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        path = moved(meta.get("path"), old, new)
        if path != meta.get("path"):
            meta["path"] = path
            meta_file.write_text(json.dumps(meta), encoding="utf-8")
            result.sources += 1
    return result
