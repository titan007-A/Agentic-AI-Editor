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
    separation_model: str = "UVR-MDX-NET-Voc_FT.onnx"

    ollama_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen3.5:9b"
    vision_model: str = "qwen3.5:4b"

    max_concurrent_jobs: int = Field(default=1, ge=1)

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
