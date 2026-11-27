"""Paints a reel's pictures with Z-Image-Turbo (mflux), or with FLUX.2 [klein] 4B when
someone is painted in from their photos (request "kind": "likeness"), in the image model's own
environment: run by images.paint as `<image-venv>/bin/python -I paint_runner.py` with the
request as JSON on stdin. The model loads once for all of them; a line of JSON is printed
as each picture is saved. Imports nothing of the app (mflux pins its own packages).

Memory: MLX keeps every buffer a picture frees for the next one, so one runner painting a
dozen pictures grew to 17 GB on the M5 and swapped (each picture then took twice as long).
The cache is emptied after each picture and held to CACHE_LIMIT, and every prompt is encoded
before the first picture so the text encoder (Qwen3-4B, about 4 GB) can be let go."""

import gc
import json
import os
import sys
import time

# The most freed memory MLX keeps for the next allocation (bytes), as mflux's own low-RAM
# mode does; the rest goes back to the system.
CACHE_LIMIT = 1 << 30
CLEAR_EACH = True  # empty the cache after each picture
FREE_TEXT_ENCODER = True  # encode every prompt first, then let the text encoder go


def load(request: dict):
    from mflux.models.common.config import ModelConfig

    if request.get("kind") == "likeness":
        # FLUX.2 [klein] 4B: one model paints someone from their photos into a picture
        # (image_paths), as an edit, and a picture from words alone.
        from mflux.models.flux2.variants import Flux2KleinEdit

        return from_words_too(Flux2KleinEdit)(
            model_config=ModelConfig.flux2_klein_4b(), model_path=request["model"]
        )
    from mflux.models.z_image import ZImage

    return ZImage(model_config=ModelConfig.z_image_turbo(), model_path=request["model"])


def from_words_too(edit):
    """FLUX.2 [klein]'s edit model that also paints a picture with no reference image: mflux
    0.24's joins the references' tokens to the picture's, and with none it fails (a picture
    from words alone came back as nothing). With none to join it joins an empty set."""

    class Klein(edit):
        def _predict(self, transformer):
            inner = super()._predict(transformer)

            def predict(*, latents, image_latents, latent_ids, image_latent_ids, **rest):
                if image_latents is None:
                    image_latents, image_latent_ids = latents[:, :0], latent_ids[:, :0]
                return inner(
                    latents=latents, image_latents=image_latents, latent_ids=latent_ids,
                    image_latent_ids=image_latent_ids, **rest,
                )  # fmt: skip

            return predict

    return Klein


# How each model encodes a prompt, and the arguments generate_image gives it as the runner
# calls it (no guidance: neither model reads a negative prompt then).
ENCODERS = {
    "_encode_prompt_pair": {"negative_prompt": " ", "guidance": 1.0},  # FLUX.2 [klein]
    "_encode_prompts": {"negative_prompt": None, "guidance": 0.0},  # Z-Image
}


def encode_first(model, prompts: list[str]) -> bool:
    """Every prompt encoded once, before any picture, and the text encoder let go: the
    pictures are painted from the kept encodings, exactly as they would have been. False
    (nothing changed) for a model encoded some other way."""
    import mlx.core as mx

    name = next((n for n in ENCODERS if hasattr(model, n)), None)
    if name is None or getattr(model, "text_encoder", None) is None:
        return False
    encode, given = getattr(model, name), ENCODERS[name]
    kept = {}
    for prompt in dict.fromkeys(prompts):
        out = encode(prompt=prompt, **given)
        mx.eval([a for a in out if a is not None])
        kept[prompt] = out

    def encoded(*, prompt, negative_prompt=None, guidance=0.0):
        if prompt not in kept or (guidance is not None and guidance > 1.0):
            raise RuntimeError("the text encoder was let go: every prompt is encoded first")
        return kept[prompt]

    setattr(model, name, encoded)  # an instance attribute: the class's stays as it is
    model.text_encoder = None
    gc.collect()
    mx.clear_cache()
    return True


def footprint() -> tuple[float, float] | None:
    """This process's memory as macOS counts it (Activity Monitor's and top's MEM: resident
    and compressed, the GPU's buffers too) and the most it has been, in GB; None elsewhere."""
    if sys.platform != "darwin":
        return None
    import ctypes

    class Info(ctypes.Structure):  # rusage_info_v4 up to ri_lifetime_max_phys_footprint
        _fields_ = [("uuid", ctypes.c_uint8 * 16)] + [
            (n, ctypes.c_uint64)
            for n in (
                "user", "system", "idle_wakeups", "interrupt_wakeups", "pageins", "wired",
                "resident", "phys_footprint", "start", "exit", "child_user", "child_system",
                "child_idle_wakeups", "child_interrupt_wakeups", "child_pageins",
                "child_elapsed", "diskio_read", "diskio_written", "qos_default",
                "qos_maintenance", "qos_background", "qos_utility", "qos_legacy",
                "qos_user_initiated", "qos_user_interactive", "billed_system",
                "serviced_system", "logical_writes", "lifetime_max_phys_footprint",
                "instructions", "cycles", "billed_energy", "serviced_energy",
                "interval_max_phys_footprint", "runnable_time",
            )
        ]  # fmt: skip

    info = Info()
    try:
        libc = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
        if libc.proc_pid_rusage(os.getpid(), 4, ctypes.byref(info)) != 0:
            return None
    except (OSError, AttributeError):
        return None
    return round(info.phys_footprint / 1e9, 1), round(info.lifetime_max_phys_footprint / 1e9, 1)


def main() -> None:
    request = json.load(sys.stdin)
    started = time.time()
    model = load(request)
    try:
        import mlx.core as mx

        mx.set_cache_limit(CACHE_LIMIT)
    except (ImportError, AttributeError):
        mx = None
    said = {"loaded": round(time.time() - started, 1)}
    if FREE_TEXT_ENCODER and request["jobs"]:
        try:
            said["encoded_first"] = encode_first(model, [job["prompt"] for job in request["jobs"]])
        except Exception as exc:  # the pictures are painted as before, encoder and all
            print(f"prompts not encoded first: {type(exc).__name__}: {exc}", file=sys.stderr)
    print(json.dumps(said), flush=True)
    for i, job in enumerate(request["jobs"]):
        t0 = time.time()
        extra = {"image_paths": job["refs"]} if job.get("refs") else {}
        try:
            image = model.generate_image(
                seed=int(job["seed"]),
                prompt=job["prompt"],
                num_inference_steps=int(request["steps"]),
                width=int(request["width"]),
                height=int(request["height"]),
                **extra,
            )
            partial = job["dest"] + ".part.png"
            image.save(partial, overwrite=True)
            os.replace(partial, job["dest"])
        except Exception as exc:  # one picture that fails never takes the rest with it
            print(f"picture {i + 1} failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            print(json.dumps({"failed": i + 1}), flush=True)
            continue
        finally:
            image = None
            if CLEAR_EACH and mx is not None:
                gc.collect()
                mx.clear_cache()
        done = {"done": i + 1, "seconds": round(time.time() - t0, 1)}
        memory = footprint()
        if memory:
            done["memory_gb"], done["peak_gb"] = memory
        print(json.dumps(done), flush=True)


if __name__ == "__main__":
    main()
