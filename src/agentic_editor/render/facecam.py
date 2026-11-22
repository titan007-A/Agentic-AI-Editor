"""The facecam layout (presets.Facecam): the streamer's camera in a panel at the top, the
game in the panel under it, each cut from the stream's picture in its panel's shape and
scaled to the frame's width.

`layout` works out the two cuts once per reel (kept on the EDL), `graph_parts` lays them
out in the ffmpeg graph, and the preview draws the same rows (preview.py). The camera cut
stays inside the camera's box (a hair in from its border), the face a little above its
middle; the game cut looks where the preset's focus says, moved off the camera when the
camera would show in it."""

from agentic_editor.analysis.facecam import Camera
from agentic_editor.edl import Facecam as Layout
from agentic_editor.presets import Preset

INSET = 0.02  # of the box's size, left off each side so its border never shows
FACE_AT = 0.42  # where down the camera panel the face sits
KEEP = 0.7  # filling the panel must keep this share of the camera, else it is shown whole
BLUR = 24  # px of blur on the copy behind a camera shown whole


def _even(value: float) -> int:
    """Down to an even whole number (the encoder's chroma wants even sizes and places)."""
    return max(0, int(value) // 2 * 2)


def _fit(start: float, length: float, low: float, high: float) -> float:
    """`start` moved so `length` from it stays within low..high."""
    return min(max(start, low), max(low, high - length))


def _inside(box: tuple[int, int, int, int]) -> tuple[float, float, float, float]:
    x, y, w, h = box
    return x + w * INSET, y + h * INSET, w * (1 - 2 * INSET), h * (1 - 2 * INSET)


def fits_whole(box: tuple[int, int, int, int], shape: float) -> bool:
    """Whether the camera is shown whole: filling a panel `shape` wide for each 1 tall
    would cut away more than KEEP of it."""
    _, _, w, h = box
    return min(w / h, shape) / max(w / h, shape) < KEEP


def camera_cut(
    box: tuple[int, int, int, int], face: tuple[int, int, int, int] | None, shape: float
) -> tuple[int, int, int, int]:
    """The part of the camera's `box` (x, y, width, height) shown in a panel `shape` wide
    for each 1 tall: as much of it as that shape holds, round the face."""
    x, y, w, h = _inside(box)
    cx, cy = (face[0] + face[2] / 2, face[1] + face[3] / 2) if face else (x + w / 2, y + h / 2)
    if w / h > shape:  # wider than the panel: its sides are cut
        cut_w, cut_h = h * shape, h
        left, top = _fit(cx - cut_w / 2, cut_w, x, x + w), y
    else:  # taller: its top and bottom are cut
        cut_w, cut_h = w, w / shape
        left, top = x, _fit(cy - FACE_AT * cut_h, cut_h, y, y + h)
    cut_w, cut_h = max(2, _even(cut_w)), max(2, _even(cut_h))
    return _even(_fit(left, cut_w, x, x + w)), _even(_fit(top, cut_h, y, y + h)), cut_w, cut_h


def game_cut(
    source: tuple[int, int], shape: float, focus: float, box: tuple[int, int, int, int] | None
) -> tuple[int, int, int, int]:
    """The part of the picture shown in the game panel (`shape` wide for each 1 tall): the
    picture's whole height, across where `focus` says, moved off the camera's `box` as far
    as the picture allows."""
    width, height = source
    cut_w, cut_h = height * shape, float(height)
    if cut_w > width:
        cut_w, cut_h = width, width / shape
    cut_w, cut_h = max(2, _even(cut_w)), max(2, _even(cut_h))
    left = focus * (width - cut_w)
    if box is not None:
        bx, _, bw, _ = box
        if left < bx + bw and bx < left + cut_w:  # the camera would show in it
            on_left = bx + bw / 2 < width / 2
            left = bx + bw if on_left else bx - cut_w
    top = (height - cut_h) / 2
    return _even(_fit(left, cut_w, 0, width)), _even(_fit(top, cut_h, 0, height)), cut_w, cut_h


def layout(camera: Camera, source: tuple[int, int], preset: Preset) -> Layout:
    """The reel's two cuts for a camera with a box."""
    assert camera.box is not None
    width, height = preset.size
    (cam_top, cam_bottom), (game_top, game_bottom) = preset.facecam.rows(height)
    shape = width / (cam_bottom - cam_top)
    whole = fits_whole(camera.box, shape)
    if whole:
        x, y, w, h = _inside(camera.box)
        cut = (_even(x + 1), _even(y + 1), max(2, _even(w - 2)), max(2, _even(h - 2)))
    else:
        cut = camera_cut(camera.box, camera.face, shape)
    return Layout(
        camera=cut,
        game=game_cut(source, width / (game_bottom - game_top), preset.facecam.focus, camera.box),
        face=camera.face,
        fit=whole,
    )


def shown_camera(cuts: Layout, panel: tuple[int, int]) -> tuple[int, int, int, int]:
    """Where the camera's cut lands in its panel, x, y, width, height: filling it, or
    shown whole in its middle."""
    width, height = panel
    if not cuts.fit:
        return 0, 0, width, height
    _, _, cw, ch = cuts.camera
    scale = min(width / cw, height / ch)
    w, h = max(2, _even(cw * scale)), max(2, _even(ch * scale))
    return (width - w) // 2, (height - h) // 2, w, h


def graph_parts(label: str, cuts: Layout, preset: Preset, background: str, out: str) -> list[str]:
    """Filters that cut the camera and the game from `label` and lay them in their panels
    on a frame of the background colour; the result is labelled `out`."""
    width, height = preset.size
    (cam_top, cam_bottom), (game_top, game_bottom) = preset.facecam.rows(height)
    cx, cy, cw, ch = cuts.camera
    gx, gy, gw, gh = cuts.game
    panel = cam_bottom - cam_top
    crop = f"crop={cw}:{ch}:{cx}:{cy}"
    if cuts.fit:
        # Whole in the middle, over a copy filling the panel, blurred.
        x, y, w, h = shown_camera(cuts, (width, panel))
        camera = [
            f"[{out}a]{crop},split=2[{out}c1][{out}c2]",
            f"[{out}c1]scale={width}:{panel}:force_original_aspect_ratio=increase:"
            f"flags=bicubic,crop={width}:{panel},boxblur={BLUR}:2,setsar=1[{out}fill]",
            f"[{out}c2]scale={w}:{h}:flags=lanczos,setsar=1[{out}whole]",
            f"[{out}fill][{out}whole]overlay={x}:{y}[{out}cam]",
        ]
    else:
        camera = [f"[{out}a]{crop},scale={width}:{panel}:flags=lanczos,setsar=1[{out}cam]"]
    # At the reel's frame rate first: half the work on a 60 fps stream, and with the
    # source's rate the panels stalled the graph (concat waited forever on a 60 fps
    # stream with a camera shown whole).
    return [
        f"[{label}]fps={preset.fps},split=2[{out}a][{out}b]",
        *camera,
        f"[{out}b]crop={gw}:{gh}:{gx}:{gy},scale={width}:{game_bottom - game_top}"
        f":flags=lanczos,setsar=1[{out}game]",
        f"color=c={background}:s={width}x{height}:r={preset.fps}[{out}bg]",
        f"[{out}bg][{out}cam]overlay=0:{cam_top}:shortest=1[{out}one]",
        f"[{out}one][{out}game]overlay=0:{game_top}:shortest=1[{out}]",
    ]


def face_rows(cuts: Layout, preset: Preset) -> tuple[float, float] | None:
    """Rows of the frame the streamer's face covers in the camera panel."""
    if cuts.face is None:
        return None
    (cam_top, cam_bottom), _ = preset.facecam.rows(preset.size[1])
    _, cy, _, ch = cuts.camera
    _, y, _, h = shown_camera(cuts, (preset.size[0], cam_bottom - cam_top))
    scale = h / ch
    top = cam_top + y + (cuts.face[1] - cy) * scale
    bottom = cam_top + y + (cuts.face[1] + cuts.face[3] - cy) * scale
    top, bottom = max(top, cam_top), min(bottom, cam_bottom)
    return (top, bottom) if bottom > top else None
