"""Make a video bigger and sharper and nothing else: every frame through Real-ESRGAN's
general model (realesr-general-x4v3, the small one made for real footage, with its weak-
denoise twin mixed in by `denoise`), then the faces lightly redrawn by GFPGAN v1.4 and
blended back by `faces`, so a blurry face gains eyes and edges without turning into
somebody else's. The sound is copied, the length and frame rate stay.

All four models are free to use and are downloaded once into models/upscale:
- realesr-general-x4v3 and realesr-general-wdn-x4v3 (Real-ESRGAN, BSD 3-Clause);
- GFPGANv1.4 (Apache 2.0), with its network code copied in archs.py;
- YuNet face detection 2023mar (OpenCV Zoo, MIT), which gives the five points a face is
  lined up by before GFPGAN sees it.

The model runs at a size where it does the most for the time it takes: half the output's
short side at most (a 1080p reel is drawn at 2160 and brought down, which cleans edges), or
the source's own size when that is smaller. It sees each frame in tiles, so its memory stays
the same whatever the size (a 4K frame drawn whole needs gigabytes).

Colour stays clean end to end: frames go in and out as 16-bit RGB, BT.709 throughout, and
the encoder gets them dithered down to 10-bit 4:2:0 HEVC (no banding in skies and soft
light), or 8-bit H.264 for players that need it.

It never holds a live job up: between frames it gives the GPU and the encoder to any job
waiting for them and carries on after (slots.others_waiting)."""

import logging
import math
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from agentic_editor import ffmpeg, logs, slots
from agentic_editor.backends import Backends, encoder_args

log = logging.getLogger(__name__)

Progress = Callable[[str, str], None]

RELEASES = "https://github.com"
MODELS = {
    "realesr-general-x4v3.pth": (
        f"{RELEASES}/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-x4v3.pth",
        "8dc7edb9ac80ccdc30c3a5dca6616509367f05fbc184ad95b731f05bece96292",
    ),
    "realesr-general-wdn-x4v3.pth": (
        f"{RELEASES}/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesr-general-wdn-x4v3.pth",
        "1641f8c4464b9f097c9fdda5589273713f67cf59f3d909e0bd688f0cee269dca",
    ),
    "GFPGANv1.4.pth": (
        f"{RELEASES}/TencentARC/GFPGAN/releases/download/v1.3.0/GFPGANv1.4.pth",
        "e2cd4703ab14f4d01fd1383a8a8b266f9a5833dacee8e6a79d3bf21a1b6be5ad",
    ),
    "face_detection_yunet_2023mar.onnx": (
        f"{RELEASES}/opencv/opencv_zoo/raw/main/models/face_detection_yunet/"
        "face_detection_yunet_2023mar.onnx",
        "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4",
    ),
}
SIZES = {"1080p": 1080, "1440p": 1440, "4k": 2160}  # the output's short side
BITRATES = {1080: 14.0, 1440: 24.0, 2160: 40.0}  # Mbps, for the encoder's cap
SCALE = 4  # the model's own factor
# Where GFPGAN's 512 px face has its eyes, nose and mouth corners (facexlib's template).
FACE_POINTS = np.array(
    [[192.98138, 239.94708], [318.90277, 240.1936], [256.63416, 314.01935],
     [201.26117, 371.41043], [313.08905, 371.15118]],
    dtype=np.float32,
)  # fmt: skip
FACE_SIZE = 512
MIN_EYES = 14  # px between the eyes in the output: smaller faces are left as drawn
DETECT_SIDE = 640  # faces are looked for in a copy this long
FOLLOW = 0.5  # how much of a face's last place carries into the next frame (less jitter)
COPY_AUDIO = {"aac", "mp3", "alac", "ac3", "eac3"}  # MP4 takes these as they are
TILE = 256  # px of the model's input a tile covers
TILE_PAD = 24  # px of picture around each tile it sees, so the seams don't show
TILE_BATCH = 8  # tiles through the model together
QUEUE = 8  # decoded frames kept ready
FACE_BATCH = 2  # faces through GFPGAN together
# HEVC 10-bit for the best picture: constant quality (CRF) rather than a bit rate, SAO off
# and a stronger adaptive quantiser so fine texture and flat gradients both survive.
HEVC_CRF = 15
HEVC_PARAMS = "aq-mode=3:no-sao=1:colorprim=bt709:transfer=bt709:colormatrix=bt709:range=limited"


class UpscaleError(RuntimeError):
    pass


def model_dir(models_dir: Path) -> Path:
    return models_dir / "upscale"


def fetch_models(models_dir: Path) -> Path:
    """Download the models not yet here, each checked against its known hash."""
    import hashlib
    import urllib.request

    folder = model_dir(models_dir)
    folder.mkdir(parents=True, exist_ok=True)
    for name, (url, digest) in MODELS.items():
        path = folder / name
        if path.exists():
            continue
        with logs.download_progress(f"the upscaling model {name}", folder):
            partial = path.with_suffix(".part")
            urllib.request.urlretrieve(url, partial)
            found = hashlib.sha256(partial.read_bytes()).hexdigest()
            if found != digest:
                partial.unlink()
                raise UpscaleError(f"{name} downloaded with the wrong contents ({found[:12]})")
            partial.replace(path)
    return folder


@dataclass(frozen=True)
class Plan:
    """The sizes one video goes through: decoded at `model_in`, drawn by the model at
    4x that, and written at `out`. All even, as the encoder wants."""

    source: tuple[int, int]
    model_in: tuple[int, int]
    out: tuple[int, int]
    fps: float


def _even(value: float) -> int:
    return max(2, round(value / 2) * 2)


def plan(width: int, height: int, fps: float, size: str) -> Plan:
    """The output's short side is the size asked for, the picture's shape kept. The model
    sees the source at half the output at most: above that it adds time, not detail."""
    if width <= 0 or height <= 0:
        raise UpscaleError("the video has no picture to upscale")
    short = SIZES[size]
    ratio = short / min(width, height)
    out = (_even(width * ratio), _even(height * ratio))
    shrink = min(1.0, (short / 2) / min(width, height))
    model_in = (_even(width * shrink), _even(height * shrink))
    return Plan((width, height), model_in, out, fps or 30.0)


def device():
    import torch

    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


class Upscaler:
    """Real-ESRGAN's general model, `denoise` of the way from its weak-denoise twin to
    itself (Real-ESRGAN's own "denoise strength"), mixed once when loaded."""

    def __init__(self, folder: Path, denoise: float, dev):
        import torch

        from agentic_editor.upscale.archs import SRVGGNetCompact

        strong = torch.load(folder / "realesr-general-x4v3.pth", map_location="cpu",
                            weights_only=True)["params"]  # fmt: skip
        weak = torch.load(folder / "realesr-general-wdn-x4v3.pth", map_location="cpu",
                          weights_only=True)["params"]  # fmt: skip
        mixed = {k: denoise * strong[k] + (1 - denoise) * weak[k] for k in strong}
        net = SRVGGNetCompact(num_in_ch=3, num_out_ch=3, num_feat=64, num_conv=32,
                              upscale=SCALE, act_type="prelu")  # fmt: skip
        net.load_state_dict(mixed, strict=True)
        self.half = dev.type in ("cuda", "mps")
        self.net = (net.half() if self.half else net).eval().to(dev)
        self.device = dev

    def __call__(self, frame: np.ndarray, out: tuple[int, int]) -> np.ndarray:
        """A frame (h, w, 3) RGB, uint16 or uint8, drawn 4x bigger in tiles and brought to
        `out` (w, h): float32 RGB on 0-255."""
        import torch
        import torch.nn.functional as F

        top = 65535.0 if frame.dtype == np.uint16 else 255.0
        with torch.inference_mode():
            x = torch.from_numpy(frame.astype(np.float32) / top).to(self.device)
            x = x.permute(2, 0, 1)[None]
            x = x.half() if self.half else x
            y = tiled(self.net, x, SCALE)
            width, height = out
            if y.shape[-1] != width or y.shape[-2] != height:
                shrinking = y.shape[-1] > width
                y = F.interpolate(y, size=(height, width), mode="bicubic",
                                  antialias=shrinking, align_corners=False)  # fmt: skip
            y = (y.float().clamp(0, 1) * 255.0)[0].permute(1, 2, 0)
            return y.cpu().numpy()


def tiled(net, x, scale: int, tile: int = TILE, pad: int = TILE_PAD, batch: int = TILE_BATCH):
    """`net` (a `scale`x upscaler) run on x (1, c, h, w) in tiles of `tile` px, each seen
    with `pad` px of picture around it (mirrored at the frame's edges) and only its middle
    kept, so the memory is a few tiles' whatever the frame's size."""
    import torch
    import torch.nn.functional as F

    _, channels, height, width = x.shape
    rows, cols = math.ceil(height / tile), math.ceil(width / tile)
    full_h, full_w = rows * tile, cols * tile
    mode = (
        "reflect"
        if min(height, width) > pad + max(full_h - height, full_w - width)
        else "replicate"
    )
    padded = F.pad(x, (pad, pad + full_w - width, pad, pad + full_h - height), mode=mode)
    canvas = torch.empty((1, channels, full_h * scale, full_w * scale), dtype=x.dtype,
                         device=x.device)  # fmt: skip
    spots = [(r, c) for r in range(rows) for c in range(cols)]
    side, inner = (tile + 2 * pad) * scale, tile * scale
    for start in range(0, len(spots), batch):
        group = spots[start : start + batch]
        span = tile + 2 * pad
        tiles = torch.cat(
            [padded[:, :, r * tile : r * tile + span, c * tile : c * tile + span] for r, c in group]
        )
        drawn = net(tiles)
        if drawn.shape[-1] != side:
            raise UpscaleError("the model didn't scale by its factor")
        for (r, c), one in zip(group, drawn, strict=True):
            canvas[0, :, r * inner : (r + 1) * inner, c * inner : (c + 1) * inner] = one[
                :, pad * scale : pad * scale + inner, pad * scale : pad * scale + inner
            ]
    return canvas[:, :, : height * scale, : width * scale]


class FaceFixer:
    """Faces found by YuNet, lined up on GFPGAN's 512 px template by their five points,
    redrawn, and pasted back `strength` of the way over the upscaled face with soft edges."""

    def __init__(self, folder: Path, strength: float, dev):
        import cv2
        import torch

        from agentic_editor.upscale.archs import GFPGANv1Clean

        self.cv2 = cv2
        self.strength = strength
        self.detector = cv2.FaceDetectorYN.create(
            str(folder / "face_detection_yunet_2023mar.onnx"), "", (320, 320), 0.7, 0.3, 50
        )
        net = GFPGANv1Clean(
            out_size=512, num_style_feat=512, channel_multiplier=2, decoder_load_path=None,
            fix_decoder=False, num_mlp=8, input_is_latent=True, different_w=True, narrow=1,
            sft_half=True,
        )  # fmt: skip
        weights = torch.load(folder / "GFPGANv1.4.pth", map_location="cpu", weights_only=True)
        net.load_state_dict(weights["params_ema"], strict=True)
        # Always full precision: in half, the colours drift (green and blue sank a quarter).
        self.half = False
        self.net = net.eval().to(dev)
        self.device = dev
        self.last: list[np.ndarray] = []  # the five points of each face in the last frame
        mask = np.zeros((FACE_SIZE, FACE_SIZE), np.float32)
        edge = FACE_SIZE // 16
        mask[edge:-edge, edge:-edge] = 1.0
        self.mask = cv2.GaussianBlur(mask, (0, 0), edge / 2)

    def points(self, frame: np.ndarray) -> list[np.ndarray]:
        """The five points (eyes, nose, mouth corners) of each face big enough to fix,
        moved a little toward where that face was in the last frame."""
        cv2 = self.cv2
        height, width = frame.shape[:2]
        scale = min(1.0, DETECT_SIDE / max(width, height))
        small = cv2.resize(frame, (round(width * scale), round(height * scale)),
                           interpolation=cv2.INTER_AREA) if scale < 1 else frame  # fmt: skip
        small = small.round().clip(0, 255).astype(np.uint8)  # YuNet reads 8-bit
        self.detector.setInputSize((small.shape[1], small.shape[0]))
        _, found = self.detector.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
        faces = []
        for row in found if found is not None else []:
            points = row[4:14].reshape(5, 2).astype(np.float32) / scale
            eyes = float(np.linalg.norm(points[0] - points[1]))
            if eyes < MIN_EYES:
                continue
            for before in self.last:
                if np.linalg.norm(before.mean(0) - points.mean(0)) < eyes * 0.5:
                    points = FOLLOW * before + (1 - FOLLOW) * points
                    break
            faces.append(points)
        self.last = faces
        return faces

    def __call__(self, frame: np.ndarray) -> np.ndarray:
        """A frame, float32 RGB on 0-255, with its faces redrawn; the same type back."""
        import torch

        cv2 = self.cv2
        faces = self.points(frame)
        if not faces or self.strength <= 0:
            return frame
        crops, warps = [], []
        for points in faces:
            warp, _ = cv2.estimateAffinePartial2D(points, FACE_POINTS, method=cv2.LMEDS)
            if warp is None:
                continue
            crop = cv2.warpAffine(
                frame, warp, (FACE_SIZE, FACE_SIZE), borderMode=cv2.BORDER_CONSTANT,
                borderValue=(135, 133, 132),
            )  # fmt: skip
            crops.append(crop)
            warps.append(warp)
        if not crops:
            return frame
        fixed = []
        with torch.inference_mode():
            # A few faces at a time: a crowd all at once took gigabytes (5 GB for 7 faces).
            for start in range(0, len(crops), FACE_BATCH):
                group = np.stack(crops[start : start + FACE_BATCH])
                x = torch.from_numpy(group).to(self.device).permute(0, 3, 1, 2).contiguous()
                x = x.half() if self.half else x.float()
                y, _ = self.net(x / 127.5 - 1.0, return_rgb=False, randomize_noise=False)
                y = ((y.float().clamp(-1, 1) + 1) * 127.5).permute(0, 2, 3, 1)
                fixed.extend(y.cpu().numpy())
        out = frame.astype(np.float32, copy=True)
        height, width = frame.shape[:2]
        for crop, face, warp in zip(crops, fixed, warps, strict=True):
            face = self.strength * face.astype(np.float32) + (1 - self.strength) * crop
            back = cv2.invertAffineTransform(warp)
            # Only the box the face lands in is warped back, not the whole frame.
            corners = np.array([[0, 0], [FACE_SIZE, 0], [0, FACE_SIZE], [FACE_SIZE, FACE_SIZE]],
                               np.float32) @ back[:, :2].T + back[:, 2]  # fmt: skip
            x0, y0 = np.floor(corners.min(0)).astype(int).clip(0, [width, height])
            x1, y1 = np.ceil(corners.max(0)).astype(int).clip(0, [width, height])
            if x1 <= x0 or y1 <= y0:
                continue
            shift = back.copy()
            shift[:, 2] -= (x0, y0)
            box = (int(x1 - x0), int(y1 - y0))
            pasted = cv2.warpAffine(face, shift, box, flags=cv2.INTER_LINEAR)
            alpha = cv2.warpAffine(self.mask, shift, box, flags=cv2.INTER_LINEAR)[..., None]
            region = out[y0:y1, x0:x1]
            out[y0:y1, x0:x1] = alpha * pasted + (1 - alpha) * region
        return out

    def unload(self) -> None:
        self.net = None


def audio_codec(video: Path) -> str | None:
    """The first sound track's codec, or None without one."""
    import json

    from agentic_editor.settings import get_settings

    found = subprocess.run(
        [get_settings().ffprobe_bin, "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name", "-of", "json", str(video)],
        capture_output=True, text=True, errors="replace",
    )  # fmt: skip
    streams = json.loads(found.stdout or "{}").get("streams", [])
    return streams[0].get("codec_name") if streams else None


def _reader(proc: subprocess.Popen, size: tuple[int, int], frames: queue.Queue) -> None:
    """Decoded frames, 16-bit RGB, into `frames`, then None."""
    width, height = size
    length = width * height * 6
    while True:
        data = proc.stdout.read(length)
        if len(data) < length:
            frames.put(None)
            return
        frames.put(np.frombuffer(data, "<u2").reshape(height, width, 3))


def encode_args(codec: str, backends: Backends, size: str) -> list[str]:
    """The encoder's output settings: HEVC 10-bit at constant quality for the best picture,
    or H.264 8-bit (the editor's usual encoder) for players without HEVC."""
    if codec == "hevc10":
        return [
            "-c:v", "libx265", "-preset", "slow", "-crf", str(HEVC_CRF),
            "-pix_fmt", "yuv420p10le", "-profile:v", "main10", "-tag:v", "hvc1",
            "-x265-params", f"{HEVC_PARAMS}:log-level=error",
        ]  # fmt: skip
    return [*encoder_args(backends, BITRATES[SIZES[size]]), "-pix_fmt", "yuv420p"]


def free_memory() -> int | None:
    """Per cent of memory free (macOS's own measure, or MemAvailable on Linux); None if
    it can't be told."""
    try:
        found = subprocess.run(["sysctl", "-n", "kern.memorystatus_level"], capture_output=True,
                               text=True, timeout=5)  # fmt: skip
        if found.returncode == 0 and found.stdout.strip().isdigit():
            return int(found.stdout.strip())
        fields = dict(line.split(":", 1) for line in Path("/proc/meminfo").read_text().splitlines())
        total = int(fields["MemTotal"].split()[0])
        return round(100 * int(fields["MemAvailable"].split()[0]) / total)
    except (OSError, KeyError, ValueError, subprocess.SubprocessError):
        return None


def eta(seconds: float) -> str:
    if seconds < 90:
        return "about a minute left"
    if seconds < 3600:
        return f"about {round(seconds / 60)} min left"
    return f"about {seconds / 3600:.1f} h left"


def upscale(
    video: Path,
    dest: Path,
    models_dir: Path,
    backends: Backends,
    size: str = "1080p",
    faces: float = 0.5,
    denoise: float = 0.5,
    progress: Progress = lambda stage, message: None,
    codec: str = "hevc10",
) -> Plan:
    """Write `video` upscaled to `dest` (see the module's description)."""
    from agentic_editor.settings import get_settings

    info = ffmpeg.probe(video)
    if not info.has_video:
        raise UpscaleError("the file has no video")
    sizes = plan(info.width, info.height, info.fps, size)
    folder = fetch_models(models_dir)
    bin_ = get_settings().ffmpeg_bin
    total = max(1, math.ceil(info.duration * sizes.fps))
    rate = f"{sizes.fps:.6g}"
    what = "HEVC 10-bit" if codec == "hevc10" else "H.264"
    progress(
        "upscale", f"upscaling {info.width}x{info.height} to {sizes.out[0]}x{sizes.out[1]}, {what}"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    sound_codec = audio_codec(video)
    sound = (
        []
        if sound_codec is None
        else ["-map", "1:a:0", "-c:a", "copy"]
        if sound_codec in COPY_AUDIO
        else ["-map", "1:a:0", "-c:a", "aac", "-b:a", "256k"]
    )
    decode = [
        bin_, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(video),
        "-map", "0:v:0", "-vf",
        # Not full_chroma_int with accurate_rnd: FFmpeg 9's swscale halves the green when
        # the two meet on a frame it doesn't resize.
        f"fps={rate},scale={sizes.model_in[0]}:{sizes.model_in[1]}"
        ":flags=area+accurate_rnd+full_chroma_inp"
        ":in_color_matrix=auto:out_color_matrix=bt709:in_range=auto:out_range=full,format=rgb48le",
        "-f", "rawvideo", "-",
    ]  # fmt: skip
    # 16-bit RGB in, BT.709 limited range out, dithered to the encoder's depth.
    depth = "yuv420p10le" if codec == "hevc10" else "yuv420p"
    encode = [
        bin_, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb48le", "-s", f"{sizes.out[0]}x{sizes.out[1]}",
        "-framerate", rate, "-i", "-", "-i", str(video), "-map", "0:v:0", *sound,
        "-vf", "zscale=matrix=709:transfer=709:primaries=709:range=limited"
        f":transferin=709:primariesin=709:rangein=full:dither=error_diffusion,format={depth}",
        *encode_args(codec, backends, size),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-color_range", "tv", "-movflags", "+faststart", "-shortest", str(dest),
    ]  # fmt: skip
    dev = device()
    slots.gpu.acquire("upscale the video", progress=progress)
    try:
        slots.encoder.acquire("encode", progress=progress)
    except BaseException:
        slots.gpu.release()
        raise
    held = True
    upscaler = fixer = None
    reader = writer = None
    done = 0
    try:
        free = free_memory()
        if free is not None and free < 10:
            progress("upscale", f"memory is low ({free}% free): this upscale may go slowly")
        upscaler = Upscaler(folder, denoise, dev)
        fixer = FaceFixer(folder, faces, dev) if faces > 0 else None
        reader = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        writer = subprocess.Popen(encode, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        frames: queue.Queue = queue.Queue(maxsize=QUEUE)
        threading.Thread(target=_reader, args=(reader, sizes.model_in, frames),
                         daemon=True).start()  # fmt: skip
        working, said = 0.0, 0.0
        try:
            while (frame := frames.get()) is not None:
                if slots.gpu.others_waiting() or slots.encoder.others_waiting():
                    # A live job wants the GPU or the encoder: it goes first.
                    slots.encoder.release()
                    slots.gpu.release()
                    held = False
                    _free(dev)
                    progress("wait", "letting a waiting job go first")
                    with slots.behind(24 * 3600):
                        slots.gpu.acquire("upscale the video", progress=progress)
                        try:
                            slots.encoder.acquire("encode", progress=progress)
                        except BaseException:
                            slots.gpu.release()
                            raise
                    held = True
                started = time.monotonic()
                drawn = upscaler(frame, sizes.out)
                if fixer is not None:
                    drawn = fixer(drawn)
                writer.stdin.write((drawn * 257.0).round().clip(0, 65535).astype("<u2").tobytes())
                working += time.monotonic() - started
                done += 1
                if time.monotonic() - said > 20 or done == total:
                    said = time.monotonic()
                    left = (total - done) * working / done
                    progress(
                        "upscale", f"upscaled {min(done, total)} of {total} frames, {eta(left)}"
                    )
            writer.stdin.close()
        except BrokenPipeError:
            pass
    except BaseException:
        if writer is not None:
            writer.kill()  # it would wait for frames that never come
        raise
    finally:
        if reader is not None:
            if reader.poll() is None:
                reader.kill()
            reader.wait()
        written = writer.wait() if writer is not None else 1
        del upscaler, fixer
        _free(dev)
        if held:
            slots.encoder.release()
            slots.gpu.release()
    if written != 0 or done == 0:
        tail = (writer.stderr.read() or b"").decode(errors="replace")[-1500:]
        err = (reader.stderr.read() or b"").decode(errors="replace")[-1500:]
        raise UpscaleError(f"the upscaled video couldn't be written: {tail or err}".strip())
    log.info(
        "upscaled %s: %d frames %s -> %s (%s)", video.name, done, sizes.source, sizes.out, codec
    )
    return sizes


def _free(dev) -> None:
    import gc

    import torch

    gc.collect()
    if dev.type == "cuda":
        torch.cuda.empty_cache()
    elif dev.type == "mps":
        torch.mps.empty_cache()
