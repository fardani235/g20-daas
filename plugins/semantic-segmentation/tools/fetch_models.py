"""Fetch model weights that are not committed to the repository.

The SegFormer land-cover model (15 MB) ships in git. The FLAIR U-Net (98 MB
as ONNX) does not; this script downloads IGN's published PyTorch weights,
verifies them and exports the ONNX graph the plugin runs:

    python3 -m venv /tmp/exportvenv
    /tmp/exportvenv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    /tmp/exportvenv/bin/pip install segmentation-models-pytorch onnx
    /tmp/exportvenv/bin/python tools/fetch_models.py

Run it before tools/build.sh so the package contains every model its cards
describe. Without it the plugin still works — the FLAIR card is reported as
unavailable and auto falls back to the SegFormer model.
"""

import hashlib
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(os.path.dirname(HERE), "models")

FLAIR_URL = ("https://huggingface.co/IGNF/FLAIR-INC_rgb_15cl_resnet34-unet/resolve/main/"
             "FLAIR-INC_rgb_15cl_resnet34-unet_weights.pth")
FLAIR_PTH_SHA256 = "d0033871116361360fa6d2024cc4425a01858afdff1bef2ed1d35514cd48a015"
FLAIR_ONNX = os.path.join(MODELS, "flair-rgb-resnet34-unet.onnx")


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def download(url, dest, expected_sha):
    if os.path.isfile(dest) and sha256(dest) == expected_sha:
        print(f"already have {dest}")
        return dest
    print(f"downloading {url}")
    urllib.request.urlretrieve(url, dest)
    actual = sha256(dest)
    if actual != expected_sha:
        os.remove(dest)
        raise SystemExit(f"checksum mismatch for {url}: {actual}")
    return dest


def main():
    if os.path.isfile(FLAIR_ONNX):
        print(f"already exported: {FLAIR_ONNX}")
        return 0
    pth = download(FLAIR_URL, os.path.join(MODELS, "flair-rgb-resnet34-unet.pth"), FLAIR_PTH_SHA256)
    sys.path.insert(0, HERE)
    import export_flair_unet  # needs torch + segmentation_models_pytorch
    export_flair_unet.main(pth)
    os.remove(pth)
    return 0


if __name__ == "__main__":
    sys.exit(main())
