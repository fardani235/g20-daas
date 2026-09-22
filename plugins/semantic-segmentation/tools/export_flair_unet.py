"""Export the IGNF FLAIR RGB land-cover U-Net to models/flair-rgb-resnet34-unet.onnx.

Dev-only: the plugin runs the exported graph with onnxruntime; this script
reproduces the export from the published PyTorch weights.

    python3 -m venv /tmp/exportvenv
    /tmp/exportvenv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
    /tmp/exportvenv/bin/pip install segmentation-models-pytorch onnx
    curl -L -o /tmp/flair.pth https://huggingface.co/IGNF/FLAIR-INC_rgb_15cl_resnet34-unet/resolve/main/FLAIR-INC_rgb_15cl_resnet34-unet_weights.pth
    /tmp/exportvenv/bin/python tools/export_flair_unet.py /tmp/flair.pth

Source: https://huggingface.co/IGNF/FLAIR-INC_rgb_15cl_resnet34-unet (Etalab-2.0)
Paper:  Garioud et al., "FLAIR: a Country-Scale Land Cover Semantic Segmentation
        Dataset From Multi-Source Optical Imagery", NeurIPS 2023 (arXiv:2310.13336).
Model:  U-Net with ResNet-34 encoder (segmentation_models_pytorch), trained on
        512x512 patches of 0.2 m RGB aerial orthophotos, 15 classes.

The exported graph takes [0,1] RGB (1x3x512x512), applies the model card's
channel normalisation (0-255 scale, mean/std below) internally and returns
softmax class probabilities (1x19x512x512) at input resolution.
"""

import hashlib
import os
import sys

import torch
import torch.nn as nn
import segmentation_models_pytorch as smp

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "models", "flair-rgb-resnet34-unet.onnx")
SIZE = 512
MEAN = [105.08, 110.87, 101.82]   # model card, 0-255 scale
STD = [52.17, 45.38, 44.00]
CLASSES = 19  # full FLAIR nomenclature; the "15cl" head keeps all 19 outputs, 4 are never predicted
PREFIX = "model.seg_model."


class ExportWrapper(nn.Module):
    def __init__(self, model):
        super().__init__()
        self.model = model
        self.register_buffer("mean", torch.tensor(MEAN).view(1, 3, 1, 1))
        self.register_buffer("std", torch.tensor(STD).view(1, 3, 1, 1))

    def forward(self, pixel_values):
        x = (pixel_values * 255.0 - self.mean) / self.std
        return torch.softmax(self.model(x), dim=1)


def main(weights: str) -> None:
    state = torch.load(weights, map_location="cpu", weights_only=False)
    state = {k[len(PREFIX):]: v for k, v in state.items() if k.startswith(PREFIX)}
    model = smp.Unet(encoder_name="resnet34", encoder_weights=None, in_channels=3, classes=CLASSES)
    missing, unexpected = model.load_state_dict(state, strict=True), None
    wrapper = ExportWrapper(model).eval()
    torch.onnx.export(
        wrapper, torch.rand(1, 3, SIZE, SIZE), OUT,
        input_names=["pixel_values"], output_names=["probabilities"],
        opset_version=17, do_constant_folding=True, dynamo=False,
    )
    sha = hashlib.sha256(open(OUT, "rb").read()).hexdigest()
    print(f"wrote {OUT} ({os.path.getsize(OUT) / 1e6:.1f} MB)")
    print(f"sha256 {sha}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "/tmp/flair/flair_rgb_resnet34_unet.pth")
