# Agentic AI Editor

A local video editor for short-form content. Give it a video link or file and one or
more presets; it downloads the video, analyzes it (transcript with word timings, scene
changes, silences, audio energy, faces), plans an edit, renders one video per preset
and checks the result against the preset's rules.

Everything runs on your own machine. Models are served by Ollama, Whisper and the vocal
separation model run in-process.

Only use it on content you own or have permission to use.

## How it works

```
download -> analyze -> plan (EDL) -> snap cuts -> render -> review -> done
```

- `backends.py` detects the machine at startup and picks the transcription backend
  (faster-whisper on CUDA, mlx-whisper on Apple Silicon, CPU int8 otherwise) and the
  encoder (NVENC, VideoToolbox, or libx264). Everything else is platform-neutral.
- `router.py` (`ModelRouter`) is the only code that knows which model does which job.
  Whisper and vocal separation run in a child process so GPU memory is freed before
  the next stage loads.
- The planner produces an edit decision list (`edl.py`), which `cuts.py` snaps to word
  boundaries. The renderer (`render/`) turns it into ffmpeg filtergraphs; the model
  never writes ffmpeg commands.

## Setup: Linux / WSL2 with an NVIDIA GPU

Requirements: git, [uv](https://docs.astral.sh/uv/), [Ollama](https://ollama.com),
an NVIDIA driver that `nvidia-smi` can see.

```
git clone https://github.com/titan007-A/Agentic-AI-Editor.git
cd Agentic-AI-Editor
scripts/setup-linux.sh
```

The script installs a static ffmpeg build with NVENC (Ubuntu's ffmpeg package has none)
into `~/.local/opt/ffmpeg-nvenc` and points `.env` at it, installs the Python
environment with the CUDA 12.8 build of PyTorch (needed for RTX 50-series/Blackwell
GPUs), pulls the Ollama models and prints the detected backends.

## Setup: macOS on Apple Silicon

```
brew install ffmpeg uv ollama
git clone https://github.com/titan007-A/Agentic-AI-Editor.git
cd Agentic-AI-Editor
cp .env.example .env
uv sync --extra ml
ollama pull qwen3.5:9b && ollama pull qwen3.5:4b
uv run aiedit backends
```

## Usage

```
uv run aiedit presets
uv run aiedit run "https://..." -p vocal-music-9x16 -p vocal-music-es-9x16
uv run aiedit run ~/Videos/talk.mp4 -p shorts-9x16
```

Outputs go to `media/outputs/<source-hash>/<preset>.mp4`, next to the EDL that produced
them. Analysis results are cached per source file in `cache/`.

## Web app

```
uv run aiedit hash-password      # prints PASSWORD_HASH and SECRET_KEY lines for .env
uv run aiedit serve
```

`serve` runs the API, the job worker and the built frontend in one process on
`HOST:PORT` (default `0.0.0.0:8000`), so any device on the network can reach it. Jobs
are queued in SQLite through Huey and run one at a time unless `MAX_CONCURRENT_JOBS`
is raised. Each pasted link becomes its own job; progress streams to the browser over
server-sent events.

Downloads are keyed by the site's video id and uploads by content hash, and all
analysis (vocal stems, transcripts, scene cuts) is cached by the hash of the source
file, so the same video is never downloaded, separated or transcribed twice.

## Configuration

All settings come from `.env`; see `.env.example`. Any backend can be forced:

```
TRANSCRIBE_BACKEND=faster-whisper   # or mlx, cpu
ENCODER=nvenc                       # or videotoolbox, x264
```

## Development

```
uv sync --extra ml
uv run pytest            # unit tests, no GPU or models needed
uv run pytest -m e2e     # real pipeline on a short sample video
uv run ruff check . && uv run ruff format --check .
```
