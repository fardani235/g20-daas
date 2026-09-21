"""Elevation Mask — example WebODM user plugin.

Marks every cell of a DSM/DTM that lies above (or below) a threshold and writes
the result as a single-band GeoTIFF: 1 = masked, 0 = not masked, 255 = nodata.

The sandbox invokes this file as ``python main.py request.json``. The request
tells us where the inputs are, which parameters the user chose and where to
write the output; see docs/plugins/user-plugin-guide.md for the full contract.
"""

import json
import sys

import numpy as np
import rasterio

NODATA = 255


def load_request(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def run(request):
    src_path = request["inputs"]["raster"]
    params = request.get("params", {})
    threshold = float(params.get("threshold", 100.0))
    mode = params.get("mode", "above")
    if mode not in ("above", "below"):
        raise ValueError(f"mode must be 'above' or 'below', got {mode!r}")

    masked = 0
    valid = 0

    with rasterio.open(src_path) as src:
        profile = src.profile.copy()
        profile.update(dtype="uint8", count=1, nodata=NODATA, compress="deflate")

        # Process block by block so memory stays flat on large rasters — the
        # sandbox caps the address space a plugin may use.
        with rasterio.open(request["output_path"], "w", **profile) as dst:
            for _, window in src.block_windows(1):
                band = src.read(1, window=window, masked=True)
                hit = band > threshold if mode == "above" else band < threshold
                out = np.where(hit.filled(False), 1, 0).astype("uint8")
                out[np.ma.getmaskarray(band)] = NODATA
                dst.write(out, 1, window=window)

                masked += int(hit.filled(False).sum())
                valid += int((~np.ma.getmaskarray(band)).sum())

    # Optional: numbers shown alongside the output in the run panel.
    with open(request["result_path"], "w", encoding="utf-8") as f:
        json.dump({
            "metadata": {
                "threshold": threshold,
                "mode": mode,
                "masked_pixels": masked,
                "masked_fraction": (masked / valid) if valid else 0.0,
            }
        }, f)


def main(argv):
    if len(argv) != 2:
        print("usage: main.py request.json", file=sys.stderr)
        return 2
    run(load_request(argv[1]))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
