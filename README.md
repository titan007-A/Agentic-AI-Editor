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
  timings, and finds scene cuts, silences, short pauses, loudness and faces.
- Every reel is one continuous stretch of the source, trimmed only at its ends. Speech
  reels start and end on whole sentences: the transcript's punctuation and the pauses
  heard in the audio mark where they begin and end. Translated speech captions are
  translated a sentence at a time and laid back over the original timing.
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

`-n` (or the notes box in the web app) takes a plain-language instruction:

```
uv run aiedit run talk.mp4 -p shorts-9x16 -n "Spanish subtitles, warm look, under 30 seconds, start where he talks about the FCC"
```

The planning model lists the setting changes the instruction asks for, each with the
instruction's own words that ask for it, and the rest (which moment to use) goes to the
planner. Every change is checked in code before it is applied: its words must be in the
instruction and be about that setting, and a language, look or length must actually be
named. Changes nobody asked for are logged and dropped, so an instruction never turns on
something it didn't mention. Each change shows in the output's edit decisions. An
instruction that says where to start ("start where...", "begin with...") is looked up
on its own: the planning model names the transcript line where that moment begins and
the words that show it, and the answer counts only if those words are in that line. The
reel starts there, `hook.optimize` is off for that job so nothing moves the opening, and
it ends on the last whole sentence that fits, before any long silence.

Every output gets a confidence score. Review issues, a fallback planner, a failed
translation or caption step, a long stretch without voice, or a reel that came out short
each lower it and are listed; an output with none of them is marked ready to post, the
rest "needs a look".

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
are queued in SQLite through Huey, two in progress at once (`MAX_CONCURRENT_JOBS`). They
take turns on the GPU and on the encoder: every model fits on the card only on its own,
and two encodes would only share the CPU. A job gives the GPU up while it encodes, so the
next one transcribes, separates and plans meanwhile instead of waiting for the whole
edit. The turns are file locks too, so a command-line run started while the server is
busy waits its turn rather than running out of GPU memory. Each pasted link becomes its
own job; progress streams to the browser over server-sent events.

Downloads are keyed by the site's video id and uploads by content hash, and all
analysis (vocal stems, transcripts, scene cuts) is cached by the hash of the source
file, so the same video is never downloaded, separated or transcribed twice.

## Autopilot

The Autopilot page in the web app watches feeds and edits each new video once:

- A feed is a folder on this computer, or a playlist or channel link (a bare YouTube
  channel link reads its Videos tab; add its Shorts tab as a feed of its own). Each feed
  has its presets, an optional instruction for every video, and optionally the hours new
  videos may start, such as 23:00 to 07:00 for overnight.
- Feeds are checked every five minutes, and on demand. A new feed notes what is already
  there and edits only what arrives later, unless told to edit that too. Files still
  being copied into a folder are left until they stop changing.
- Every video a feed starts or passes over is remembered, so none is edited twice. Each
  is an ordinary job: one that fails is marked failed and the rest carry on, a few at a
  time per feed.
- Finished videos land in one of two piles: Ready, when the agent has no doubts, and
  Needs a look, with its doubts listed. "Looks fine" moves a video to Ready, "Posted"
  and "Dismiss" take it off the piles.

## Presets

A preset is a YAML file in `presets/` whose file name matches its `name`. It sets the
frame (`aspect_ratio`, `resolution`, `fps`), the length range, and how each part of the
render behaves:

| Section | Controls |
| --- | --- |
| `captions` | what they say (`speech`, `lyrics`, `translated` into `language`, or `visual`, written from the picture), font, size, colours, position, animation; kept off faces and off text already in the picture (name banners, titles) while it is on screen; lines break where they read well, never after a linking word like "the" or "de" when a better place is near, and each sentence starts a new line; on songs, presets that keep the original audio take their words from the isolated vocals when the full mix gives almost none; an .srt file is saved next to the video |
| `one_liner` | a single line the vision model writes about the video, burned in; `hint` steers it |
| `text_blocks` | fixed text drawn on every frame, such as a series tagline |
| `framing` | how the picture fills the frame (see below); background colour, or `auto_background` to take it from the footage |
| `grade` | colour: `auto` corrects a cast or a flat, dim picture; `look` (warm, cool, teal-orange, film, moody, vivid, mono, faded); a `.cube` LUT; `strength` |
| `audio` | `original` or `vocals` (instruments removed), high-pass, loudness target |
| `silence` | what counts as silence when trimming a quiet start or end |
| `beat_sync` | start and end on the beat (bar lines from the original mix), and an optional zoom pulse on each bar or beat |
| `hook` | `optimize` lets the editor choose where the reel starts: the strongest opening line (the planning model reads them for speech; the strongest entrance wins for music), and the strongest stretch when the plan runs long. Off, the planned opening stays and a long plan is cut from the end; `variants` makes up to five versions per video from different strong segments, saved as `preset-b.mp4` and so on |
| `title_overlay`, `logo`, `music`, `intro`, `outro` | optional extras |
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

`framing.picture_width` shows the whole picture narrower than the frame (0.74 of its
width, say), centred on the background, for pages that inset their videos.

### Learning a style from a reel

Give a reel that already looks the way you want, and a preset is drafted from it:

```
uv run aiedit learn "https://www.instagram.com/reel/..." --name raw-vocals-es
uv run aiedit learn reference.mp4 --name raw-vocals-es --save
```

or "Learn from a reel" on the web app's Presets page, which opens the draft in the editor
with what was found listed above it. Nothing is saved until you save it.

- Layout, from the pixels: whether the picture fills the frame, sits on a flat background
  (and its colour), or sits on a blurred copy of itself (drawn here as a colour taken from
  the footage); how wide the picture is, and the gaps to the text.
- Text: the vision model reads every text on six frames (two in the opening seconds, the
  rest timed to words being said or sung, since captions only show then). Frames are read
  padded to a square: on a tall frame its boxes drift down by up to a tenth of the height.
  Text that stays the whole reel becomes a text block, captions that change become caption
  settings, text only in the opening seconds becomes the hook banner. A username in a
  corner and text that belongs to the video shown (inside a letterboxed picture) are left
  out. The model's boxes are only roughly right, so each text is then found in the pixels:
  its exact colour, position, line breaks, an outline and a highlighted word's colour.
  Every bundled font is tried at the size that matches the measured widths, and the one
  whose heights and stroke thickness match best is chosen; every reading of a text is
  measured and they vote, so one bad box can't decide it. Caption sizes and margins are
  measured against libass, which draws captions smaller than Pillow draws text blocks.
- Captions are `speech` or `lyrics` when they are the words heard, `translated` when they
  are in another language, `visual` otherwise.
- Look: black and white is recognised; other looks are left for you.
- Sound: speech or singing (the planning model reads the transcript and what the frames
  show), and for singing, whether the instruments were taken out, in which case the
  preset isolates the vocals. The loudness target follows the reel's.
- The length range is set around the reel's length, and the planning model writes the
  label and the style notes.

Text that changes from video to video (a song title, "Day 28") is flagged so you can edit
it. Logos, music beds, intros and outros are not learned.

The quickest way to make one by hand is to duplicate an existing preset on the web app's
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
