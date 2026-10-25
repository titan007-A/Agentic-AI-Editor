from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=ROOT / ".env", extra="ignore")

    media_dir: Path = Path("media")
    cache_dir: Path = Path("cache")
    models_dir: Path = Path("models")
    presets_dir: Path = Path("presets")
    fonts_dir: Path = Path("fonts")

    ffmpeg_bin: str = "ffmpeg"
    ffprobe_bin: str = "ffprobe"

    transcribe_backend: Literal["auto", "faster-whisper", "mlx", "cpu"] = "auto"
    encoder: Literal["auto", "nvenc", "videotoolbox", "x264"] = "auto"
    video_codec: Literal["h264", "hevc"] = "h264"

    whisper_model: str = "large-v3-turbo"
    mlx_whisper_model: str = "mlx-community/whisper-large-v3-turbo"
    # MelBand Roformer by Kimberley Jensen: 11.9 dB median vocal SDR on MUSDB18 against
    # 8.5 for UVR-MDX-NET-Voc_FT, and faster. Any audio-separator model name works.
    separation_model: str = "vocals_mel_band_roformer.ckpt"

    ollama_url: str = "http://127.0.0.1:11434"
    # Named by build: on Apple Silicon the bare tags fetch MLX builds, which Ollama can't
    # hold to a JSON schema, and every answer here is one.
    llm_model: str = "qwen3.5:9b-q4_K_M"
    vision_model: str = "qwen3.5:4b-q4_K_M"
    # Model layers Ollama puts on the GPU (99: all). Unset, Ollama decides and on an 8 GB
    # card runs part of the 9b planner on the CPU at about two thirds the speed.
    llm_gpu_layers: int | None = Field(default=None, ge=0)

    # Jobs in progress at once. They take turns on the GPU and the encoder, so a second
    # one uses the GPU while the first encodes.
    max_concurrent_jobs: int = Field(default=2, ge=1)

    host: str = "0.0.0.0"
    port: int = 8000
    password_hash: str = ""
    secret_key: str = ""
    database: Path = Path("media/app.db")
    queue_database: Path = Path("media/queue.db")
    web_dir: Path = Path("web/out")

    @field_validator(
        "media_dir",
        "cache_dir",
        "models_dir",
        "presets_dir",
        "fonts_dir",
        "database",
        "queue_database",
        "web_dir",
    )
    @classmethod
    def _relative_to_repo(cls, path: Path) -> Path:
        path = path.expanduser()
        return path if path.is_absolute() else ROOT / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
