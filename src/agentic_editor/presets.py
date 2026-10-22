import re
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

Color = Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")]
Corner = Literal["top-left", "top-right", "bottom-left", "bottom-right"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CaptionStyle(Strict):
    enabled: bool = True
    # speech: what is said, word by word. lyrics: sung lines from the isolated vocals.
    # translated: what is said, in `language`. visual: written from what is on screen.
    mode: Literal["speech", "lyrics", "translated", "visual"] = "speech"
    language: str | None = Field(default=None, description="target language for translated")
    avoid_faces: bool = True
    export_srt: bool = True
    font: str = "Anton-Regular"
    size: int = Field(default=84, ge=12, le=300)
    color: Color = "#FFFFFF"
    highlight_color: Color = "#FFE000"
    outline_color: Color = "#000000"
    outline: float = Field(default=5, ge=0, le=20)
    position: Literal["top", "middle", "bottom"] = "bottom"
    margin: int = Field(default=520, ge=0, description="px from the frame edge for top/bottom")
    words_per_line: int = Field(default=3, ge=1, le=12)
    animation: Literal["highlight", "pop", "karaoke", "none"] = "highlight"
    uppercase: bool = True


class OneLiner(Strict):
    """A single line the vision model writes about the video, burned in."""

    enabled: bool = False
    font: str = "OpenSans-ExtraBold"
    size: int = Field(default=64, ge=12, le=300)
    color: Color = "#FFFFFF"
    box_color: Color | None = "#000000"
    position: Literal["top", "bottom"] = "top"
    offset: int = Field(default=260, ge=0, description="px from the anchored frame edge")
    duration: float | None = Field(default=None, gt=0, description="seconds; None for all")
    max_words: int = Field(default=10, ge=2, le=20)
    hint: str = Field(default="", description="what the line should be about or sound like")


class TextBlock(Strict):
    """A fixed piece of text drawn on every frame, such as a series tagline."""

    text: str
    font: str = "OpenSans-ExtraBold"
    size: int = Field(ge=12, le=300)
    min_size: int = Field(default=28, ge=8)
    color: Color = "#FFFFFF"
    anchor: Literal["top", "bottom"]
    offset: int = Field(ge=0, description="px from the anchored frame edge to the text top")
    max_width: int = Field(default=1000, ge=100)
    line_spacing: float = Field(default=1.0, gt=0)
    max_shrink: float = Field(
        default=1.0, gt=0, le=1, description="smallest scale allowed when making room"
    )


class TitleOverlay(Strict):
    enabled: bool = False
    font: str = "Anton-Regular"
    size: int = 72
    color: Color = "#FFFFFF"
    box_color: Color | None = "#000000"
    offset: int = 300
    duration: float = Field(default=3.0, gt=0)


class Hook(Strict):
    enabled: bool = True
    overlay: bool = False
    # Choose where the reel starts for the strongest first seconds.
    optimize: bool = True
    # Versions per video, each from a different strong segment, for comparing hooks.
    variants: int = Field(default=1, ge=1, le=5)


class Logo(Strict):
    file: str | None = None
    position: Corner = "top-right"
    opacity: float = Field(default=0.85, ge=0, le=1)
    width: int = Field(default=160, ge=16)
    margin: int = 48


class Music(Strict):
    file: str | None = None
    volume_db: float = Field(default=-20, le=0)
    ducking: bool = True


class Framing(Strict):
    mode: Literal["smart", "face", "center", "none"] = "smart"
    background: Color = "#000000"
    # Take the background colour from the footage instead, toned to keep text readable.
    auto_background: bool = False
    crop_black_bars: bool = False
    smoothing: float = Field(default=0.8, ge=0, le=0.99)
    text_gap_above: int | None = 0
    text_gap_below: int | None = 130


class Grade(Strict):
    """Colour for the picture: a light automatic correction, a look, a .cube LUT."""

    auto: bool = False
    look: Literal[
        "none", "natural", "warm", "cool", "teal-orange", "film", "moody", "vivid", "mono", "faded"
    ] = "none"
    lut: str | None = Field(default=None, description=".cube file in the assets folder")
    strength: float = Field(default=1.0, ge=0, le=1, description="how much of the look/LUT")


class Watermarks(Strict):
    """Logos, channel names and usernames burnt into the source get blurred out."""

    blur: bool = True
    strength: float = Field(
        default=18, ge=2, le=60, description="smallest blur radius, in source pixels"
    )


class Audio(Strict):
    source: Literal["original", "vocals"] = "original"
    highpass_hz: int = Field(default=0, ge=0)
    loudness_lufs: float = -14.0
    true_peak_db: float = -1.5
    fade_in: float = Field(default=0.03, ge=0)
    fade_out: float = Field(default=0.25, ge=0)


class BeatSync(Strict):
    """Musical timing: start and end on the beat, and an optional pulse on each bar."""

    snap_trims: bool = False
    pulse: bool = False
    pulse_scale: float = Field(default=1.04, ge=1.0, le=1.2)
    pulse_on: Literal["bar", "beat"] = "bar"


class Silence(Strict):
    """What counts as silence, used to trim a silent start or end; never to cut inside."""

    threshold_db: float = -35.0
    min_duration: float = Field(default=0.4, gt=0)


class Preset(Strict):
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    label: str
    platform: str = "instagram_reels"
    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    fps: int = Field(default=30, ge=12, le=60)
    min_duration: float = Field(gt=0)
    max_duration: float = Field(gt=0)
    captions: CaptionStyle = CaptionStyle()
    hook: Hook = Hook()
    title_overlay: TitleOverlay = TitleOverlay()
    one_liner: OneLiner = OneLiner()
    text_blocks: list[TextBlock] = []
    logo: Logo = Logo()
    intro: str | None = None
    outro: str | None = None
    music: Music = Music()
    silence: Silence = Silence()
    beat_sync: BeatSync = BeatSync()
    zoom_punch_ins: bool = False
    framing: Framing = Framing()
    watermarks: Watermarks = Watermarks()
    grade: Grade = Grade()
    audio: Audio = Audio()
    bitrate_mbps: float = Field(default=12.0, gt=0)
    style_notes: str = ""

    @model_validator(mode="before")
    @classmethod
    def _drop_retired_keys(cls, data: object) -> object:
        # Every edit is now one continuous stretch of the source, so the settings that
        # spliced several clips or cut pauses out are gone. Older files still load.
        if isinstance(data, dict):
            data = {k: v for k, v in data.items() if k != "clip_count"}
            silence = data.get("silence")
            if isinstance(silence, dict):
                data["silence"] = {k: v for k, v in silence.items() if k != "max_pause"}
            elif silence is None:
                data.pop("silence", None)  # a section left empty once max_pause went
        return data

    @field_validator("resolution")
    @classmethod
    def _resolution_format(cls, value: str) -> str:
        if not re.fullmatch(r"\d{2,5}x\d{2,5}", value):
            raise ValueError("resolution must look like 1080x1920")
        return value

    @model_validator(mode="after")
    def _consistent(self) -> "Preset":
        if self.min_duration > self.max_duration:
            raise ValueError("min_duration is longer than max_duration")
        w, h = self.size
        if w % 2 or h % 2:
            raise ValueError("resolution must use even numbers")
        num, _, den = self.aspect_ratio.partition(":")
        if abs(w / h - float(num) / float(den)) > 0.01:
            raise ValueError(f"resolution {self.resolution} does not match {self.aspect_ratio}")
        return self

    @property
    def size(self) -> tuple[int, int]:
        w, _, h = self.resolution.partition("x")
        return int(w), int(h)


def load_preset(path: Path) -> Preset:
    return Preset.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_presets(directory: Path) -> dict[str, Preset]:
    presets = {}
    for path in sorted(directory.glob("*.yaml")):
        preset = load_preset(path)
        if preset.name != path.stem:
            raise ValueError(f"{path.name}: name '{preset.name}' must match the file name")
        presets[preset.name] = preset
    return presets


def save_preset(preset: Preset, directory: Path) -> Path:
    path = directory / f"{preset.name}.yaml"
    data = preset.model_dump(mode="json", exclude_defaults=False)
    path.write_text(yaml.safe_dump(data, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return path
