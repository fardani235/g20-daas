"""3D Reconstruction — WebODM user plugin entrypoint.

Invoked by the plugin sandbox as ``python main.py request.json``. Detects which
task outputs were handed over (DSM / DTM / orthophoto / point cloud / existing
model), picks a workflow (``recon3d.workflow``), writes a web-ready GLB to
``output_path`` and run metadata to ``result_path``.

Exit status 0 means success. Expected problems (unusable inputs, incompatible
parameters) are printed as one clear line; anything else is a bug and prints
its traceback so the run panel shows what happened.
"""

import json
import sys
import traceback

from recon3d.errors import ReconstructionError
from recon3d.progress import Progress
from recon3d.workflow import run


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: main.py request.json", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        request = json.load(f)

    progress = Progress()
    try:
        metadata = run(request, progress)
    except ReconstructionError as e:
        progress.log(f"error: {e}")
        return 1
    except MemoryError:
        progress.log("error: out of memory — choose a lighter quality preset, a coarser resolution_m "
                     "or a smaller texture_size")
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
