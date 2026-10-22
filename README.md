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
                                         ^                     |
                                         +---- fix and redo ---+
```

- Analysis isolates the vocals when a preset asks for them, transcribes with word
  timings, and finds scene cuts, silences, loudness and faces.
- Review checks each render (length, framing, loudness, caption sync) and applies
  deterministic fixes, re-rendering up to twice.

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
GPUs), pulls the Ollama models and prints the detected backends. Build the web app
once with `(cd web && npm ci && npm run build)` if you want the browser interface.

## Setup: macOS on Apple Silicon

```
brew install ffmpeg uv ollama
git clone https://github.com/titan007-A/Agentic-AI-Editor.git
cd Agentic-AI-Editor
cp .env.example .env
uv sync --extra ml
ollama pull qwen3.5:9b && ollama pull qwen3.5:4b
uv run aiedit backends
(cd web && npm ci && npm run build)
```

On macOS the platform layer transcribes with mlx-whisper; `ENCODER=videotoolbox` trades
larger files for much faster encodes.

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
(cd web && npm ci && npm run build)   # once, and again after frontend changes
uv run aiedit serve
```

For frontend work, run `npm run dev` in `web/` next to `aiedit serve`; the dev server
proxies `/api` to port 8000. Format with `npm run format`.

To use it from a phone on WSL2, set `networkingMode=mirrored` in `%UserProfile%\.wslconfig`
and open `http://<windows-lan-ip>:8000`. If the phone can't connect, allow the port
through the Windows firewall from an admin PowerShell:

```
New-NetFirewallRule -DisplayName "Agentic AI Editor" -Direction Inbound -Protocol TCP -LocalPort 8000 -Action Allow
```

`serve` runs the API, the job worker and the built frontend in one process on
`HOST:PORT` (default `0.0.0.0:8000`), so any device on the network can reach it. Jobs
are queued in SQLite through Huey and run one at a time unless `MAX_CONCURRENT_JOBS`
is raised. Each pasted link becomes its own job; progress streams to the browser over
server-sent events.

Downloads are keyed by the site's video id and uploads by content hash, and all
analysis (vocal stems, transcripts, scene cuts) is cached by the hash of the source
file, so the same video is never downloaded, separated or transcribed twice.

## Presets

A preset is a YAML file in `presets/` whose file name matches its `name`. It sets the
frame (`aspect_ratio`, `resolution`, `fps`), the length range, and how each part of the
render behaves:

| Section | Controls |
| --- | --- |
| `captions` | word-by-word captions: font, size, colours, position, animation |
| `text_blocks` | fixed text drawn on every frame, such as a series tagline |
| `framing` | how the picture fills the frame (see below); background colour |
| `audio` | `original` or `vocals` (instruments removed), high-pass, loudness target |
| `silence` | what counts as silence when trimming a quiet start or end |
| `hook`, `title_overlay`, `logo`, `music`, `intro`, `outro` | optional extras |
| `style_notes` | plain-language guidance the planner reads before choosing the edit |

Framing `smart` (the default) frames each shot so nothing that matters is cut. Heads
come from the face detector; the vision model points out what else must stay whole (the
microphone or instrument in use, readable text, a logo). A shot whose essentials fit a
full-height 9:16 window is cropped, following the subject if it moves; one that doesn't is
widened and padded above and below instead of cut, a name banner sliding in only widens
the seconds it is on screen, and when most of a reel is too wide every shot gets the same
framing so it doesn't jump at each cut. Fresh moments are then re-checked by geometry and
any shot whose edge would cut something is widened before rendering. `face`, `center` and
`none` (show the whole picture) remain for fixed looks.

The quickest way to make one is to duplicate an existing preset on the web app's
Presets page and edit it there; the server validates every save. Editing the YAML by
hand works too: `uv run aiedit presets` loads and validates all of them. Fonts are
looked up by file name (without extension) in `fonts/`.

## Models

| Job | Setting | Default |
| --- | --- | --- |
| Planning the edit | `LLM_MODEL` | `qwen3.5:9b` (Ollama) |
| Checking frames, thumbnails | `VISION_MODEL` | `qwen3.5:4b` (Ollama) |
| Transcription | `WHISPER_MODEL` / `MLX_WHISPER_MODEL` | `large-v3-turbo` |
| Vocal isolation | `SEPARATION_MODEL` | `vocals_mel_band_roformer.ckpt` |

To try another model, pull it (`ollama pull <name>`) or pick one from
`uv run audio-separator --list_models`, set it in `.env`, and run the benchmark and the
end-to-end test. The Ollama models must support JSON-schema output, and the vision model
must accept images. Changing the separation model re-separates cached sources the next
time they are used. A new kind of model belongs in `router.py`, which is the only place
that maps jobs to models.

## Benchmark

```
uv run aiedit benchmark                 # Commons sample performance, 246 s
uv run aiedit benchmark ~/Videos/x.mp4 -p shorts-9x16 --no-llm
```

It times each stage without the caches. On an RTX 5050 laptop (8 GB) under WSL2:

```
  transcription        11.8s  20.9x realtime, 83 words, faster-whisper
  vocal separation     42.2s  5.8x realtime, vocals_mel_band_roformer.ckpt
  llm qwen3.5:9b        6.7s  31.0 tokens/s over 203 tokens
  llm qwen3.5:4b        3.2s  63.3 tokens/s over 195 tokens
  render               14.3s  1.4x realtime, 42 fps at 1080x1920, libx264
```

## Configuration

All settings come from `.env`; see `.env.example`. Any backend can be forced:

```
TRANSCRIBE_BACKEND=faster-whisper   # or mlx, cpu
ENCODER=nvenc                       # or videotoolbox, x264
```

By default the final encode uses libx264 (`slower`, CRF 16) even when a GPU encoder is
present: measured with VMAF against lossless renders it kept the same quality as the
NVENC settings it replaced in 30-57% less space. Forcing `ENCODER=nvenc` or
`videotoolbox` is several times faster for larger files.

Vocals are isolated with the MelBand Roformer vocal model (`SEPARATION_MODEL`); on the
MUSDB18 test excerpts it scored 11.9 dB median vocal SDR against 8.5 dB for the older
UVR-MDX-NET-Voc_FT, in less time.

## Development

```
uv sync --extra ml
uv run pre-commit install   # ruff and prettier on every commit
uv run pytest               # unit tests, no GPU or models needed
uv run pytest -m e2e        # real pipeline on a short sample video
uv run ruff check . && uv run ruff format --check .
(cd web && npm run lint && npm run format:check)
```

CI runs the Python checks and the web lint, format and build on every pull request.
The macOS job only runs when the workflow is started by hand (Actions, ci, Run
workflow), because GitHub's Apple Silicon runners are often unavailable.
