"""The edit decision list: what the planner hands to the renderer."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from agentic_editor.presets import Color, Preset

CropMode = Literal["face", "center", "none"]


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

    clips: list[Clip] = Field(min_length=1)
    hook: str = ""
    title: str = ""
    caption_overrides: CaptionOverrides = CaptionOverrides()
    zooms: list[Zoom] = []
    crop_mode: CropMode = "center"

    @property
    def duration(self) -> float:
        return sum(c.duration for c in self.clips)

    def to_output_time(self, source_time: float) -> float | None:
        """Map a source timestamp onto the rendered timeline, or None if it was cut."""
        offset = 0.0
        for clip in self.clips:
            if clip.start <= source_time < clip.end:
                return offset + source_time - clip.start
            offset += clip.duration
        return None


def problems(edl: EDL, preset: Preset, source_duration: float, tolerance: float = 0.5) -> list[str]:
    """Rule violations that the planner has to fix; empty when the EDL is usable."""
    found = []
    if len(edl.clips) > preset.clip_count:
        found.append(f"{len(edl.clips)} clips, the preset allows at most {preset.clip_count}")
    for i, clip in enumerate(edl.clips):
        if clip.end > source_duration + tolerance:
            found.append(f"clip {i} ends at {clip.end:.2f}s, after the source ({source_duration:.2f}s)")
        if i and clip.start < edl.clips[i - 1].end:
            found.append(f"clip {i} starts before clip {i - 1} ends; clips must be in order")
    if edl.duration < preset.min_duration - tolerance:
        found.append(f"total {edl.duration:.1f}s is shorter than the {preset.min_duration:g}s minimum")
    if edl.duration > preset.max_duration + tolerance:
        found.append(f"total {edl.duration:.1f}s is longer than the {preset.max_duration:g}s maximum")
    return found
