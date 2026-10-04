"""Building Footprints — WebODM user plugin entrypoint.

Invoked by the plugin sandbox as ``python main.py request.json``. Reads the
orthophoto, runs the selected footprint model through ``bfplugin.pipeline``
and writes one polygon per building (GeoJSON, EPSG:4326) to ``output_path``,
plus run metadata to ``result_path``.

Exit status 0 means success. Expected problems (bad input, missing or corrupt
model, wrong parameter, grid over the memory budget) are printed as one clear
line; anything else is a bug and prints its traceback so the run panel shows
what happened.
"""

import json
import sys
import traceback

from bfplugin.errors import FootprintError
from bfplugin.pipeline import run
from bfplugin.progress import Progress


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: main.py request.json", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        request = json.load(f)

    progress = Progress(progress_path=request.get("progress_path"))
    try:
        metadata = run(request, progress)
    except FootprintError as e:
        progress.log(f"error: {e}")
        return 1
    except MemoryError:
        progress.log("error: out of memory — set a coarser resolution_m (the sandbox caps a run at about 2 GB)")
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
