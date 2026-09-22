"""Backend contract.

A backend turns one tile of aligned inputs into per-class scores. It declares
which data it wants (``needs``), how much context beyond the tile it needs
(``context`` pixels, e.g. for a lookup radius) and whether it requires a fixed
tile size (``fixed_size``, e.g. an ONNX graph with static shapes).
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class TileData:
    """Aligned inputs for one (possibly context-padded) tile.

    Arrays are ``None`` when the corresponding dataset was not selected or the
    backend did not ask for it. ``rgb`` is (3, h, w) float32 in [0, 1] with
    ``rgb_valid`` (h, w) bool; ``elevation``/``ndsm`` are (h, w) float32 with
    NaN for nodata. ``pixel_size_m`` is the processing resolution.
    """
    rgb: np.ndarray | None
    rgb_valid: np.ndarray | None
    elevation: np.ndarray | None
    ndsm: np.ndarray | None
    pixel_size_m: float


class Backend:
    #: subset of {"rgb", "elevation", "ndsm"} the backend reads
    needs: frozenset = frozenset()
    #: extra pixels to read around each tile (cropped from the result)
    context: int = 0
    #: required tile side (None = any size)
    fixed_size: int | None = None
    #: default tile size when the run does not set one
    default_tile: int = 512

    def __init__(self, card, params, progress):
        self.card = card
        self.params = params
        self.progress = progress
        self.class_ids = card.class_ids
        self.index_of = {cid: i for i, cid in enumerate(self.class_ids)}

    def predict(self, tile: TileData) -> tuple[np.ndarray, np.ndarray]:
        """Return (scores (C, h, w) float32, valid (h, w) bool) for the tile.

        ``scores`` sum to 1 over classes where ``valid``; pixels where the
        backend has no data are ``valid == False`` and get no vote.
        """
        raise NotImplementedError

    def one_hot(self, labels: np.ndarray, valid: np.ndarray) -> np.ndarray:
        """(C, h, w) one-hot scores from a (h, w) array of class *ids*."""
        scores = np.zeros((len(self.class_ids),) + labels.shape, dtype="float32")
        for cid, i in self.index_of.items():
            scores[i][(labels == cid) & valid] = 1.0
        return scores

    def describe(self) -> dict:
        return {"backend": self.card.backend}
