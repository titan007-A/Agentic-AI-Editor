"""Paints a reel's pictures with Z-Image-Turbo (mflux), or with FLUX.2 [klein] 4B when
someone is painted in from their photos (request "kind": "likeness"), in the image model's own
environment: run by images.paint as `<image-venv>/bin/python -I paint_runner.py` with the
request as JSON on stdin. The model loads once for all of them; a line of JSON is printed
as each picture is saved. Imports nothing of the app (mflux pins its own packages)."""

import json
import os
import sys
import time


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


def main() -> None:
    request = json.load(sys.stdin)
    started = time.time()
    model = load(request)
    print(json.dumps({"loaded": round(time.time() - started, 1)}), flush=True)
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
        print(json.dumps({"done": i + 1, "seconds": round(time.time() - t0, 1)}), flush=True)


if __name__ == "__main__":
    main()
