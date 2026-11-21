"""Paints a reel's pictures with Z-Image-Turbo (mflux), in the image model's own
environment: run by images.paint as `<image-venv>/bin/python -I paint_runner.py` with the
request as JSON on stdin. The model loads once for all of them; a line of JSON is printed
as each picture is saved. Imports nothing of the app (mflux pins its own packages)."""

import json
import os
import sys
import time


def main() -> None:
    from mflux.models.common.config import ModelConfig
    from mflux.models.z_image import ZImage

    request = json.load(sys.stdin)
    started = time.time()
    model = ZImage(model_config=ModelConfig.z_image_turbo(), model_path=request["model"])
    print(json.dumps({"loaded": round(time.time() - started, 1)}), flush=True)
    for i, job in enumerate(request["jobs"]):
        t0 = time.time()
        image = model.generate_image(
            seed=int(job["seed"]),
            prompt=job["prompt"],
            num_inference_steps=int(request["steps"]),
            width=int(request["width"]),
            height=int(request["height"]),
        )
        partial = job["dest"] + ".part.png"
        image.save(partial, overwrite=True)
        os.replace(partial, job["dest"])
        print(json.dumps({"done": i + 1, "seconds": round(time.time() - t0, 1)}), flush=True)


if __name__ == "__main__":
    main()
