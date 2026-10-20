from agentic_editor.backends import Backends, encoder_args, pick_transcriber


def test_apple_silicon_prefers_mlx():
    assert pick_transcriber("auto", apple_silicon=True, cuda_devices=0, has_mlx=True) == "mlx"


def test_cuda_machine_uses_faster_whisper():
    assert pick_transcriber("auto", False, cuda_devices=1, has_mlx=False) == "faster-whisper"


def test_falls_back_to_cpu_without_accelerator():
    assert pick_transcriber("auto", False, cuda_devices=0, has_mlx=False) == "cpu"
    assert pick_transcriber("auto", True, cuda_devices=0, has_mlx=False) == "cpu"


def test_forced_backend_wins():
    assert pick_transcriber("cpu", False, cuda_devices=1, has_mlx=False) == "cpu"


def test_encoder_args_per_backend():
    nvenc = encoder_args(Backends("linux-cuda", "faster-whisper", "nvenc", "h264"))
    assert nvenc[:2] == ["-c:v", "h264_nvenc"]
    vt = encoder_args(Backends("macos", "mlx", "videotoolbox", "hevc"))
    assert vt[:2] == ["-c:v", "hevc_videotoolbox"] and "hvc1" in vt
    x264 = encoder_args(Backends("linux-cpu", "cpu", "x264", "h264"), bitrate_mbps=8)
    assert x264[:2] == ["-c:v", "libx264"] and "12M" in x264
    assert all(args[-2:] == ["-pix_fmt", "yuv420p"] for args in (nvenc, vt, x264))
