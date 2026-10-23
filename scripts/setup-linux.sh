#!/usr/bin/env bash
# Linux / WSL2 + NVIDIA setup: NVENC-enabled ffmpeg, Python env, Ollama and other models.
set -euo pipefail

cd "$(dirname "$0")/.."
FFMPEG_DIR="${FFMPEG_DIR:-$HOME/.local/opt/ffmpeg-nvenc}"
FFMPEG_BUILD="${FFMPEG_BUILD:-ffmpeg-n9.0-latest-linux64-gpl-9.0}"

command -v uv >/dev/null || { echo "uv is required: https://docs.astral.sh/uv/"; exit 1; }
command -v ollama >/dev/null || { echo "ollama is required: https://ollama.com/download"; exit 1; }
nvidia-smi >/dev/null 2>&1 || echo "warning: nvidia-smi failed, the GPU paths will be unavailable"

# MediaPipe's face detector links against libGLESv2 even when it runs on the CPU.
if ! ldconfig -p | grep -q libGLESv2.so.2; then
    sudo apt-get install -y libgles2
fi

# Distribution ffmpeg packages are built without NVENC, so use a static build that has it.
if [ ! -x "$FFMPEG_DIR/bin/ffmpeg" ]; then
    tmp="$(mktemp -d)"
    curl -fL -o "$tmp/ffmpeg.tar.xz" \
        "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/$FFMPEG_BUILD.tar.xz"
    tar -xf "$tmp/ffmpeg.tar.xz" -C "$tmp"
    mkdir -p "$(dirname "$FFMPEG_DIR")"
    rm -rf "$FFMPEG_DIR"
    mv "$tmp/$FFMPEG_BUILD" "$FFMPEG_DIR"
    rm -rf "$tmp"
fi

[ -f .env ] || cp .env.example .env
sed -i "s|^FFMPEG_BIN=.*|FFMPEG_BIN=$FFMPEG_DIR/bin/ffmpeg|; s|^FFPROBE_BIN=.*|FFPROBE_BIN=$FFMPEG_DIR/bin/ffprobe|" .env

uv sync --extra ml

set -a; source .env; set +a
ollama pull "${LLM_MODEL:-qwen3.5:9b}"
ollama pull "${VISION_MODEL:-qwen3.5:4b}"
uv run aiedit fetch-models

uv run aiedit backends
