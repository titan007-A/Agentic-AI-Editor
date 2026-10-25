#!/usr/bin/env bash
# macOS on Apple Silicon: ffmpeg, Ollama, the Python environment, models and the web app.
set -euo pipefail

cd "$(dirname "$0")/.."

if [ "$(uname -s)" != Darwin ] || [ "$(uname -m)" != arm64 ]; then
    echo "this script is for macOS on Apple Silicon; on Linux use scripts/setup-linux.sh"
    exit 1
fi
command -v brew >/dev/null || {
    echo "Homebrew is required: https://brew.sh"
    exit 1
}

# Homebrew's ffmpeg has VideoToolbox, libx264 and libass; node builds the web app.
brew install ffmpeg uv ollama node
# Ollama as a login service, so the models are there whenever the editor is.
brew services start ollama

[ -f .env ] || cp .env.example .env
prefix="$(brew --prefix)"
# launchd starts the editor without Homebrew on its PATH, so ffmpeg is named in full.
sed -i '' "s|^FFMPEG_BIN=.*|FFMPEG_BIN=$prefix/bin/ffmpeg|; s|^FFPROBE_BIN=.*|FFPROBE_BIN=$prefix/bin/ffprobe|" .env

uv sync --extra ml

for _ in $(seq 30); do
    curl -fs http://127.0.0.1:11434/api/version >/dev/null && break
    sleep 1
done
set -a
# shellcheck disable=SC1091
source .env
set +a
ollama pull "${LLM_MODEL:-qwen3.5:9b-q4_K_M}"
ollama pull "${VISION_MODEL:-qwen3.5:4b-q4_K_M}"
uv run aiedit fetch-models

(cd web && npm ci && npm run build)

uv run aiedit backends

if ! grep -q "^PASSWORD_HASH=." .env; then
    echo
    echo "Set a login password: run 'uv run aiedit hash-password' and put its two lines in .env."
fi
echo "To run the editor in the background from every login: scripts/macos-service.sh install"
