"""Regenerate models/segformer-satellite-landcover.onnx (dev-only).

The segmentation op runs this model with onnxruntime at runtime; this script is
only needed to reproduce the committed export. It requires PyTorch and
transformers, which are deliberately not runtime dependencies of the service.

    python -m venv /tmp/segvenv
    /tmp/segvenv/bin/pip install torch --index-url https://download.pytorch.org/whl/cpu
    /tmp/segvenv/bin/pip install "transformers>=4.40" onnx
    /tmp/segvenv/bin/python tools/export_segformer_landcover.py

Source: https://huggingface.co/Pranilllllll/segformer-satellite-segementation (MIT)
Class order (model ids 0-6): background, residential_area, road, river, forest,
unused_land, agricultural_area.
"""

import hashlib
import os

import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoModelForSemanticSegmentation

REPO = "Pranilllllll/segformer-satellite-segementation"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "models", "segformer-satellite-landcover.onnx")
SIZE = 512
MEAN = [0.485, 0.456, 0.406]
STD = [0.229, 0.224, 0.225]


class ExportWrapper(nn.Module):
    """Normalise [0,1] RGB, run SegFormer, upsample, softmax to per-class probs."""

    def __init__(self, model):
        super().__init__()
        self.model = model
        self.register_buffer("mean", torch.tensor(MEAN).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(STD).view(1, 3, 1, 1))

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        x = (pixel_values - self.mean) / self.std
        logits = self.model(pixel_values=x).logits
        logits = F.interpolate(
            logits, size=pixel_values.shape[-2:], mode="bilinear", align_corners=False
        )
        return torch.softmax(logits, dim=1)


def main() -> None:
    model = AutoModelForSemanticSegmentation.from_pretrained(REPO)
    wrapper = ExportWrapper(model).eval()

    torch.onnx.export(
        wrapper,
        torch.rand(1, 3, SIZE, SIZE),
        OUT,
        input_names=["pixel_values"],
        output_names=["probabilities"],
        opset_version=17,
        do_constant_folding=True,
        dynamo=False,
    )
    sha = hashlib.sha256(open(OUT, "rb").read()).hexdigest()
    print(f"wrote {OUT}")
    print(f"sha256 {sha}")
    print("Update the checksum in Dockerfile if it changed.")


if __name__ == "__main__":
    main()
