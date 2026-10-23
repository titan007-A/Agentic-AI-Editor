import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import JSON, ForeignKey, String, Text, UniqueConstraint, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def _now() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid4().hex[:12]


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    source: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    presets: Mapped[list[str]] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str] = mapped_column(String(32), default="queued")
    progress: Mapped[float] = mapped_column(default=0.0)
    error: Mapped[str | None] = mapped_column(Text)
    parent_id: Mapped[str | None] = mapped_column(String(12))
    created_at: Mapped[datetime] = mapped_column(default=_now)
    updated_at: Mapped[datetime] = mapped_column(default=_now, onupdate=_now)

    outputs: Mapped[list["Output"]] = relationship(
        back_populates="job", cascade="all, delete-orphan", order_by="Output.id"
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    stage: Mapped[str] = mapped_column(String(32))
    message: Mapped[str] = mapped_column(Text)
    progress: Mapped[float] = mapped_column(default=0.0)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Output(Base):
    __tablename__ = "outputs"

    id: Mapped[int] = mapped_column(primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey("jobs.id", ondelete="CASCADE"), index=True)
    preset: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(Text)
    thumbnail: Mapped[str | None] = mapped_column(Text)
    duration: Mapped[float]
    title: Mapped[str] = mapped_column(Text, default="")
    planner: Mapped[str] = mapped_column(String(64))
    renders: Mapped[int] = mapped_column(default=1)
    issues: Mapped[list[str]] = mapped_column(JSON, default=list)
    edl: Mapped[dict] = mapped_column(JSON)
    # 0-1: how sure the agent is the video is ready to post as it is.
    confidence: Mapped[float] = mapped_column(default=1.0)
    doubts: Mapped[list[str]] = mapped_column(JSON, default=list)
    # The editor's call, which overrides the agent's: approved, posted or dismissed.
    verdict: Mapped[str | None] = mapped_column(String(16))

    job: Mapped[Job] = relationship(back_populates="outputs")

    @property
    def pile(self) -> str | None:
        """ready, look (needs a look) or None once posted or dismissed."""
        if self.verdict in ("posted", "dismissed"):
            return None
        if self.verdict == "approved" or not (self.doubts or self.issues):
            return "ready"
        return "look"


class Feed(Base):
    """A folder, playlist or channel the autopilot watches for new videos."""

    __tablename__ = "feeds"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))  # folder or link
    location: Mapped[str] = mapped_column(Text)
    presets: Mapped[list[str]] = mapped_column(JSON)
    notes: Mapped[str | None] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(default=True)
    # Hours of the day (0-23) new videos may start, e.g. 23 to 7 for overnight; None for any.
    active_from: Mapped[int | None]
    active_to: Mapped[int | None]
    batch: Mapped[int] = mapped_column(default=3)  # videos in progress at once, at most
    include_existing: Mapped[bool] = mapped_column(default=False)
    last_checked: Mapped[datetime | None]
    last_result: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)

    items: Mapped[list["FeedItem"]] = relationship(
        back_populates="feed", cascade="all, delete-orphan", order_by="FeedItem.id.desc()"
    )


class FeedItem(Base):
    """A video a feed has seen, so it is never edited twice."""

    __tablename__ = "feed_items"
    __table_args__ = (UniqueConstraint("feed_id", "key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    feed_id: Mapped[int] = mapped_column(ForeignKey("feeds.id", ondelete="CASCADE"), index=True)
    key: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(Text)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"))
    skipped: Mapped[bool] = mapped_column(default=False)  # already there when the feed began
    created_at: Mapped[datetime] = mapped_column(default=_now)

    feed: Mapped[Feed] = relationship(back_populates="items")
    job: Mapped[Job | None] = relationship()


class StyleDraft(Base):
    """A preset being drafted from a reel that has the look wanted."""

    __tablename__ = "style_drafts"

    id: Mapped[str] = mapped_column(String(12), primary_key=True, default=_new_id)
    source: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="queued")
    message: Mapped[str] = mapped_column(Text, default="waiting for a free slot")
    preset: Mapped[dict | None] = mapped_column(JSON)
    notes: Mapped[list[str]] = mapped_column(JSON, default=list)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=_now)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


def make_sessionmaker(path: Path) -> sessionmaker:
    path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(connection, _record):
        cursor = connection.cursor()
        # WAL lets the SSE readers poll while the worker thread writes progress.
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    add_missing_columns(engine)
    return sessionmaker(engine, expire_on_commit=False)


def add_missing_columns(engine) -> None:
    """create_all makes new tables but never touches existing ones; add the columns later
    versions introduced so an older database keeps working."""
    with engine.begin() as connection:
        for table in Base.metadata.sorted_tables:
            have = {
                row[1] for row in connection.exec_driver_sql(f"PRAGMA table_info({table.name})")
            }
            for column in table.columns:
                if column.name in have:
                    continue
                literal = "NULL"
                if column.default is not None:
                    value = column.default.arg
                    # Callable defaults (list, dict) are wrapped to take a context argument.
                    value = value(None) if callable(value) else value
                    if isinstance(value, bool | int | float):
                        literal = str(int(value) if isinstance(value, bool) else value)
                    elif isinstance(value, list | dict | str):
                        text = value if isinstance(value, str) else json.dumps(value)
                        literal = "'" + text.replace("'", "''") + "'"
                kind = column.type.compile(engine.dialect)
                connection.exec_driver_sql(
                    f"ALTER TABLE {table.name} ADD COLUMN {column.name} {kind} DEFAULT {literal}"
                )
