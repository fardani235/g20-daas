"""Change Detection — WebODM user plugin entrypoint.

Invoked by the plugin sandbox as ``python main.py request.json``. Compares the
survey the run belongs to with a second survey of the same project through
``cdplugin.pipeline`` and writes the change regions (GeoJSON, EPSG:4326) or
the elevation difference (GeoTIFF) to ``output_path``, plus the numbers and
the audit trail to ``result_path``.

Exit status 0 means success. Expected problems (surveys that do not overlap,
no comparable pair of inputs, a wrong parameter) are printed as one clear
line; anything else is a bug and prints its traceback so the run panel shows
what happened.
"""

import json
import sys
import traceback

from cdplugin.errors import ChangeError
from cdplugin.pipeline import run
from cdplugin.progress import Progress


def main(argv) -> int:
    if len(argv) != 2:
        print("usage: main.py request.json", file=sys.stderr)
        return 2
    with open(argv[1], encoding="utf-8") as f:
        request = json.load(f)

    progress = Progress(progress_path=request.get("progress_path"))
    try:
        metadata = run(request, progress)
    except ChangeError as e:
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
