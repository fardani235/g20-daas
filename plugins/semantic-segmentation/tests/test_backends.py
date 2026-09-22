import numpy as np
import pytest

from segplugin import fusion
from segplugin.backends.geomorphon import geomorphons
from segplugin.backends.height_classes import excess_green
from segplugin.params import Params


def test_geomorphons_classify_peak_pit_slope_and_flat():
    n = 61
    yy, xx = np.mgrid[0:n, 0:n]
    r = np.hypot(yy - 30, xx - 30)
    peak = np.exp(-(r / 8) ** 2).astype("float32") * 20          # 20 m hill
    forms = geomorphons(peak, 1.0, search_m=15, skip_m=0, flatness_deg=1)
    assert forms[30, 30] == 2                                     # peak
    assert forms[30, 40] == 5                                     # convex flank of a cone: spur
    assert forms[2, 2] == 1                                       # flat far away

    plane = (0.2 * xx).astype("float32")                          # uniform 11° slope
    assert geomorphons(plane, 1.0, 15, 0, 1)[30, 30] == 6         # slope

    pit = -peak
    assert geomorphons(pit, 1.0, 15, 0, 1)[30, 30] == 10          # pit

    flat = np.zeros((n, n), "float32")
    flat[5, 5] = np.nan
    f = geomorphons(flat, 1.0, 15, 0, 1)
    assert (f[np.isfinite(flat)] == 1).all() and f[5, 5] == 0


def test_geomorphons_are_gsd_invariant():
    n = 61
    yy, xx = np.mgrid[0:n, 0:n]
    z = np.exp(-(np.hypot(yy - 30, xx - 30) / 8) ** 2).astype("float32") * 20
    coarse = geomorphons(z, 1.0, 15, 0, 1)
    # Same terrain sampled twice as fine: same landform at the same location.
    fine = np.kron(z, np.ones((2, 2), "float32"))
    fine_forms = geomorphons(fine, 0.5, 15, 0, 1)
    assert fine_forms[60, 60] == coarse[30, 30] == 2
    assert fine_forms[60, 80] == coarse[30, 40] == 5


def test_excess_green_separates_vegetation():
    rgb = np.zeros((3, 1, 3), "float32")
    rgb[:, 0, 0] = (0.12, 0.43, 0.12)     # green
    rgb[:, 0, 1] = (0.5, 0.5, 0.5)        # grey
    rgb[:, 0, 2] = (0.0, 0.0, 0.0)        # black
    exg = excess_green(rgb)
    assert exg[0, 0] > 0.3 and abs(exg[0, 1]) < 1e-6 and abs(exg[0, 2]) < 1e-6


def test_height_fusion_moves_probability_with_height():
    names = ["background", "building", "road"]
    spec = {"elevated_threshold_m": 2.0, "softness_m": 0.5,
            "classes": {"building": "elevated", "road": "ground", "background": "any"}}
    scores = np.zeros((3, 1, 3), "float32")
    scores[1] = 0.5
    scores[2] = 0.5                                       # RGB undecided
    ndsm = np.array([[8.0, 0.0, np.nan]], "float32")
    fused = fusion.fuse(scores, ndsm, names, spec, weight=1.0)
    assert fused[1, 0, 0] > 0.9                           # tall -> building
    assert fused[2, 0, 1] > 0.9                           # flat -> road
    assert fused[1, 0, 2] == pytest.approx(0.5)           # unknown height -> unchanged
    assert np.allclose(fused.sum(axis=0), 1.0)
    assert fusion.fuse(scores, ndsm, names, spec, weight=0.0) is scores
    # Stronger weight sharpens; a strong RGB opinion is not vetoed outright.
    strong = np.zeros((3, 1, 1), "float32")
    strong[2] = 0.99
    strong[1] = 0.01
    out = fusion.fuse(strong, np.array([[8.0]], "float32"), names, spec, weight=1.0)
    assert 0.05 < out[1, 0, 0] < 0.9


def test_params_parse_and_validate():
    p = Params.from_request({"model": "geomorphons", "resolution_m": "0.5", "tile_size": 256, "bogus": 1})
    assert p.model == "geomorphons" and p.resolution_m == 0.5 and p.tile_size == 256
    assert p.unknown == {"bogus": 1}
    assert Params.from_request(None).model == "auto"
    from segplugin.errors import ParameterError
    with pytest.raises(ParameterError):
        Params.from_request({"tile_size": 10})
    with pytest.raises(ParameterError):
        Params.from_request({"resolution_m": "fast"})
    assert Params.from_request({"class_filter": "a, b;c"}).class_filter_names() == ["a", "b", "c"]
