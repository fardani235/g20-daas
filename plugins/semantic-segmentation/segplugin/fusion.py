"""Decision-level fusion of an RGB model with height above ground.

RGB land-cover models confuse classes that look alike from above but differ in
height — flat roofs vs. roads or bare ground, tree crowns vs. grass and crops.
The multimodal remote-sensing literature fixes this with the nDSM: as an extra
network input (early fusion) or by combining the predictions of an RGB and a
height branch (late fusion), both studied on the ISPRS Vaihingen/Potsdam
benchmark by Audebert, Le Saux & Lefèvre (ISPRS J. Photogramm. 2018,
"Beyond RGB: Very high resolution urban remote sensing with multimodal deep
networks"), with late fusion the more robust of the two.

Since the shipped RGB model was not trained with height, this module performs
late fusion in the naive-Bayes / product-of-experts form: the model's class
posterior is multiplied by a per-class height likelihood and renormalised,

    P(c | rgb, h)  ∝  P(c | rgb) · P(h | c)^w

with P(h | c) a logistic function of h around the "elevated" threshold
(above it: buildings, trees; below: roads, water, fields), ``w`` the user's
``height_weight`` (0 disables fusion) and classes marked ``any`` unaffected.
Each model card declares which of its classes are elevated/ground, so the same
code serves other label sets.
"""

import numpy as np


def height_likelihood(ndsm: np.ndarray, kind: str, threshold_m: float, softness_m: float) -> np.ndarray:
    """P(h | class kind) as a smooth step around ``threshold_m``.

    ``elevated`` rises from ~0 below the threshold to ~1 above it; ``ground`` is
    its complement; ``any`` is flat (0.5) so it cancels out in normalisation.
    NaN heights yield 0.5 everywhere (no evidence).
    """
    if kind == "any":
        return np.full(ndsm.shape, 0.5, dtype="float32")
    z = (ndsm - threshold_m) / max(softness_m, 1e-3)
    z = np.nan_to_num(z, nan=0.0).clip(-30, 30)
    p = 1.0 / (1.0 + np.exp(-z))
    # A floor keeps a single modality from ever vetoing a class outright.
    p = 0.05 + 0.9 * p
    if kind == "ground":
        p = 1.0 - p
    p = np.where(np.isnan(ndsm), 0.5, p)
    return p.astype("float32")


def fuse(scores: np.ndarray, ndsm: np.ndarray, class_names: list[str], spec: dict,
         weight: float, threshold_m: float | None = None) -> np.ndarray:
    """Return the fused (C, h, w) posterior; ``scores`` is not modified."""
    if weight <= 0 or ndsm is None:
        return scores
    kinds = spec.get("classes", {})
    threshold = float(threshold_m if threshold_m is not None else spec.get("elevated_threshold_m", 2.0))
    softness = float(spec.get("softness_m", 0.75))
    fused = scores.copy()
    for i, name in enumerate(class_names):
        kind = kinds.get(name, "any")
        if kind == "any":
            continue
        lik = height_likelihood(ndsm, kind, threshold, softness)
        fused[i] *= lik ** weight
    total = fused.sum(axis=0, keepdims=True)
    valid = total[0] > 0
    fused[:, valid] /= total[:, valid]
    return fused
