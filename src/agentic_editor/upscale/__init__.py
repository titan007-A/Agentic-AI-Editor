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
the source's own size when that is smaller."""

import logging
import math
import queue
import subprocess
import threading
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
BATCH = 4  # frames through the model together


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

    def __call__(self, frames: np.ndarray, out: tuple[int, int]) -> np.ndarray:
        """Frames (n, h, w, 3) uint8 RGB, drawn 4x bigger and brought to `out` (w, h)."""
        import torch
        import torch.nn.functional as F

        with torch.inference_mode():
            x = torch.from_numpy(frames).to(self.device).permute(0, 3, 1, 2).contiguous()
            x = x.half() if self.half else x.float()
            y = self.net(x / 255.0)
            width, height = out
            if y.shape[-1] != width or y.shape[-2] != height:
                shrinking = y.shape[-1] > width
                y = F.interpolate(y, size=(height, width), mode="bicubic",
                                  antialias=shrinking, align_corners=False)  # fmt: skip
            y = (y.clamp(0, 1) * 255.0).round().byte().permute(0, 2, 3, 1)
            return y.cpu().numpy()


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
        with torch.inference_mode():
            x = torch.from_numpy(np.stack(crops)).to(self.device).permute(0, 3, 1, 2).contiguous()
            x = x.half() if self.half else x.float()
            y, _ = self.net(x / 127.5 - 1.0, return_rgb=False, randomize_noise=False)
            y = ((y.float().clamp(-1, 1) + 1) * 127.5).round().byte().permute(0, 2, 3, 1)
            fixed = y.cpu().numpy()
        out = frame.astype(np.float32)
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
        return out.round().clip(0, 255).astype(np.uint8)

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
    width, height = size
    length = width * height * 3
    while True:
        data = proc.stdout.read(length)
        if len(data) < length:
            frames.put(None)
            return
        frames.put(np.frombuffer(data, np.uint8).reshape(height, width, 3))


def upscale(
    video: Path,
    dest: Path,
    models_dir: Path,
    backends: Backends,
    size: str = "1080p",
    faces: float = 0.5,
    denoise: float = 0.5,
    progress: Progress = lambda stage, message: None,
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
    progress("upscale", f"upscaling {info.width}x{info.height} to {sizes.out[0]}x{sizes.out[1]}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    codec = audio_codec(video)
    sound = (
        []
        if codec is None
        else ["-map", "1:a:0", "-c:a", "copy"]
        if codec in COPY_AUDIO
        else ["-map", "1:a:0", "-c:a", "aac", "-b:a", "256k"]
    )
    decode = [
        bin_, "-hide_banner", "-nostdin", "-loglevel", "error", "-i", str(video),
        "-map", "0:v:0", "-vf",
        f"fps={rate},scale={sizes.model_in[0]}:{sizes.model_in[1]}:flags=area"
        ":in_color_matrix=auto:out_color_matrix=bt709,format=rgb24",
        "-f", "rawvideo", "-",
    ]  # fmt: skip
    encode = [
        bin_, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{sizes.out[0]}x{sizes.out[1]}",
        "-framerate", rate, "-i", "-", "-i", str(video), "-map", "0:v:0", *sound,
        "-vf", "scale=in_color_matrix=bt709:out_color_matrix=bt709:out_range=tv",
        *encoder_args(backends, BITRATES[SIZES[size]]),
        "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-movflags", "+faststart", "-shortest", str(dest),
    ]  # fmt: skip
    dev = device()
    with slots.gpu.turn("upscale the video", progress), slots.encoder.turn("encode", progress):
        upscaler = Upscaler(folder, denoise, dev)
        fixer = FaceFixer(folder, faces, dev) if faces > 0 else None
        reader = subprocess.Popen(decode, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        writer = subprocess.Popen(encode, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        frames: queue.Queue = queue.Queue(maxsize=BATCH * 3)
        threading.Thread(target=_reader, args=(reader, sizes.model_in, frames),
                         daemon=True).start()  # fmt: skip
        done = 0
        try:
            finished = False
            while not finished:
                batch = []
                while len(batch) < BATCH:
                    frame = frames.get()
                    if frame is None:
                        finished = True
                        break
                    batch.append(frame)
                if not batch:
                    break
                for drawn in upscaler(np.stack(batch), sizes.out):
                    if fixer is not None:
                        drawn = fixer(drawn)
                    writer.stdin.write(drawn.tobytes())
                done += len(batch)
                if done % (BATCH * 30) < BATCH:
                    progress("upscale", f"upscaled {min(done, total)} of {total} frames")
            writer.stdin.close()
        except BrokenPipeError:
            pass
        except BaseException:
            writer.kill()  # it would wait for frames that never come
            raise
        finally:
            if reader.poll() is None:
                reader.kill()
            reader.wait()
            written = writer.wait()
            del upscaler, fixer
            _free(dev)
        if written != 0 or done == 0:
            tail = (writer.stderr.read() or b"").decode(errors="replace")[-1500:]
            err = (reader.stderr.read() or b"").decode(errors="replace")[-1500:]
            raise UpscaleError(f"the upscaled video couldn't be written: {tail or err}".strip())
    log.info("upscaled %s: %d frames %s -> %s", video.name, done, sizes.source, sizes.out)
    return sizes


def _free(dev) -> None:
    import gc

    import torch

    gc.collect()
    if dev.type == "cuda":
        torch.cuda.empty_cache()
    elif dev.type == "mps":
        torch.mps.empty_cache()
