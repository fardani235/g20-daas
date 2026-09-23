"""3D Reconstruction — WebODM user plugin entrypoint.

Invoked by the plugin sandbox as ``python main.py request.json``. Reads the
selected inputs (orthophoto / DSM / DTM / point cloud / 3D model), picks a
reconstruction workflow (``recon.pipeline``) and writes one web-ready GLB to
``output_path``, plus run metadata to ``result_path``. While running it
publishes ``{"percent", "message"}`` to ``progress_path`` when the request
provides one.

Exit status 0 means success. Expected problems (missing or incompatible
inputs, invalid parameters) are printed as one clear line; anything else is a
bug and prints its traceback so the run panel shows what happened.
"""

import json
import os
import sys
import traceback

# Bound GDAL's block cache before rasterio loads: its default (5% of RAM) is
# sized for the host, not for the sandbox's address-space limit.
os.environ.setdefault("GDAL_CACHEMAX", "128")

from recon.errors import ReconstructionError
from recon.pipeline import run
from recon.progress import Progress


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: main.py request.json", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        request = json.load(f)

    progress = Progress(progress_path=request.get("progress_path"))
    try:
        metadata = run(request, progress)
    except ReconstructionError as e:
        progress.log(f"error: {e}")
        return 1
    except MemoryError:
        progress.log("error: out of memory — choose a lower quality, a coarser resolution_m or a smaller texture_size")
        return 1
    except Exception:
        progress.log("unexpected error:\n" + traceback.format_exc())
        return 1

    result_path = request.get("result_path")
    if result_path:
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump({"metadata": metadata}, f)
    progress.log("finished")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
