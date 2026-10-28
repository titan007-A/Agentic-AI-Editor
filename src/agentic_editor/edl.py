"""The edit decision list: what the planner hands to the renderer."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic_editor.presets import Color, Preset
from agentic_editor.transcript import Word

CropMode = Literal["smart", "face", "center", "none"]


class Clip(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0, description="source time in seconds")
    end: float = Field(gt=0, description="source time in seconds")
    reason: str = ""
    score: float = Field(default=0.5, ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> "Clip":
        if self.end <= self.start:
            raise ValueError(f"clip ends ({self.end}) before it starts ({self.start})")
        return self

    @property
    def duration(self) -> float:
        return self.end - self.start


class Shot(BaseModel):
    """How one stretch of the reel is framed: a window of the source, `width` pixels wide
    from `x`, at the picture's full height unless `height` is set (from `y`, for a picture
    taller than the frame). At the frame's own aspect it fills the frame; otherwise it is
    scaled to fit and padded, so nothing in it is cut or stretched."""

    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0, description="source time in seconds")
    end: float = Field(gt=0, description="source time in seconds")
    x: int = Field(ge=0)
    width: int = Field(gt=0)
    y: int = Field(default=0, ge=0)
    height: int | None = Field(default=None, gt=0)
    follow: bool = False  # the window tracks the subject's head instead of staying put
    # The window eases here from where the shot before left it, rather than jumping: there
    # is no cut in the source to hide the move.
    glide: bool = False
    reason: str = ""


class Cue(BaseModel):
    """One caption on the reel's own timeline. Word timings, when known, drive the
    word-by-word highlight; translated or written captions show as whole lines."""

    model_config = ConfigDict(extra="forbid")

    start: float = Field(ge=0)
    end: float = Field(gt=0)
    text: str
    words: list[Word] = []


class Zoom(BaseModel):
    model_config = ConfigDict(extra="forbid")

    time: float = Field(ge=0, description="source time in seconds")
    scale: float = Field(default=1.15, ge=1.0, le=1.5)
    duration: float = Field(default=0.8, gt=0, le=3)


class CaptionOverrides(BaseModel):
    model_config = ConfigDict(extra="forbid")

    position: Literal["top", "middle", "bottom"] | None = None
    words_per_line: int | None = Field(default=None, ge=1, le=12)
    highlight_color: Color | None = None


class EDL(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Exactly one: a reel is one continuous stretch of the source, trimmed only at its
    # start and end. Splicing pieces together is not allowed.
    clips: list[Clip] = Field(min_length=1, max_length=1)
    hook: str = ""
    title: str = ""
    caption_overrides: CaptionOverrides = CaptionOverrides()
    zooms: list[Zoom] = []
    crop_mode: CropMode = "center"
    variant: int = Field(default=0, ge=0, description="0 for the first version, 1 for B...")
    shots: list[Shot] = []
    # The picture's area in the source, black bars excluded: x, y, width, height.
    picture: tuple[int, int, int, int] | None = None
    # Captions the models wrote (translated or from the visuals) and a one-line overlay;
    # speech and lyrics captions are built from the transcript at render time instead.
    cues: list[Cue] = []
    one_liner: str = ""
    decisions: list[str] = []
    # The decisions that say something asked for couldn't be done; they lower confidence.
    doubts: list[str] = []

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    def noted(self, *notes: str, doubt: bool = False) -> "EDL":
        """A copy with `notes` added to the decisions, and to the doubts when they say
        something asked for couldn't be done."""
        update = {"decisions": [*self.decisions, *notes]}
        if doubt:
            update["doubts"] = [*self.doubts, *notes]
        return self.model_copy(update=update)

    def to_output_time(self, source_time: float) -> float | None:
        """Map a source timestamp onto the rendered timeline, or None if it was cut."""
        offset = 0.0
        for clip in self.clips:
            if clip.start <= source_time < clip.end:
                return offset + source_time - clip.start
            offset += clip.duration
        return None


def problems(
    edl: EDL,
    preset: Preset,
    source_duration: float,
    tolerance: float = 0.5,
    allow_long: bool = False,
    available: float | None = None,
) -> list[str]:
    """Rule violations that the planner has to fix; empty when the EDL is usable. With
    allow_long a segment over the maximum passes, for the trimmer to cut down. When only
    `available` seconds of material exist, a plan that long is long enough."""
    found = []
    shortest = preset.min_duration if available is None else min(preset.min_duration, available)
    for i, clip in enumerate(edl.clips):
        if clip.end > source_duration + tolerance:
            found.append(
                f"clip {i} ends at {clip.end:.2f}s, after the source ({source_duration:.2f}s)"
            )
        if i and clip.start < edl.clips[i - 1].end:
            found.append(f"clip {i} starts before clip {i - 1} ends; clips must be in order")
    if edl.duration < shortest - tolerance:
        found.append(f"total {edl.duration:.1f}s is shorter than the {shortest:g}s minimum")
    if not allow_long and edl.duration > preset.max_duration + tolerance:
        found.append(
            f"total {edl.duration:.1f}s is longer than the {preset.max_duration:g}s maximum"
        )
    return found
