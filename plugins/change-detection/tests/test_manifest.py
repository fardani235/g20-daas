"""The packages must stay consistent with what the platform's upload validation
(webodm_core.plugins.package) accepts and with the parameters the code reads."""

import json
import os
import re
import subprocess
import zipfile
from dataclasses import fields

import pytest

from cdplugin import __version__
from cdplugin.params import Params
from tests.conftest import PLUGIN_DIR

MANIFESTS = {"plugin.json": ("vector", "change"), "plugin-raster.json": ("raster", "dem")}
DATASETS = {"orthophoto", "dsm", "dtm", "point_cloud", "model"}


def _manifest(name):
    with open(os.path.join(PLUGIN_DIR, name), encoding="utf-8") as f:
        return json.load(f)


@pytest.mark.parametrize("name", MANIFESTS)
def test_manifest_shape_matches_platform_rules(name):
    m = _manifest(name)
    assert re.match(r"^[a-z0-9][a-z0-9-]{1,63}$", m["id"])
    assert m["version"] == __version__ and re.match(r"^\d+(\.\d+){0,3}$", m["version"])
    assert 0 < len(m["label"]) <= 140 and 0 < len(m["description"]) <= 2000
    assert m["entrypoint"] == "main.py" and os.path.isfile(os.path.join(PLUGIN_DIR, "main.py"))
    assert (m["output_kind"], m["render_kind"]) == MANIFESTS[name]
    assert 10 <= m["timeout_seconds"] <= 3600
    schema = m["params_schema"]
    assert schema["type"] == "object" and schema["required"] == []
    known = {f.name for f in fields(Params)} - {"unknown"}
    assert set(schema["properties"]) <= known
    for key, spec in schema["properties"].items():
        assert spec["type"] in ("string", "number", "boolean"), key
        assert "default" in spec and "title" in spec and "description" in spec, key
        # The schema default is the code default, so "nothing sent" and the dialog agree.
        assert Params.from_request({key: spec["default"]}).raw()[key] == Params().raw()[key], key
        if "enum" in spec:
            assert spec["type"] == "string" and spec["default"] in spec["enum"], key
            for value in spec["enum"]:
                Params.from_request({key: value})


def test_ids_differ_so_both_packages_can_be_installed():
    assert _manifest("plugin.json")["id"] == "change-detection"
    assert _manifest("plugin-raster.json")["id"] == "change-detection-raster"


@pytest.mark.parametrize("name", MANIFESTS)
def test_inputs_pair_this_survey_with_the_comparison_survey(name):
    inputs = {i["name"]: i for i in _manifest(name)["inputs"]}
    assert set(inputs) == {"surface", "reference_surface", "orthophoto", "reference_orthophoto"}
    for key, spec in inputs.items():
        assert re.match(r"^[a-z0-9][a-z0-9_-]{1,63}$", key)
        assert set(spec["datasets"]) <= DATASETS and 0 < len(spec["label"]) <= 140
        assert set(spec) <= {"name", "label", "datasets", "optional", "survey"}
        # Inputs named reference_* come from the second survey; the others from the run's own.
        assert spec.get("survey", "primary") == ("reference" if key.startswith("reference_") else "primary")
    assert inputs["surface"]["datasets"] == inputs["reference_surface"]["datasets"] == ["dsm", "dtm"]
    assert inputs["orthophoto"]["optional"] and inputs["reference_orthophoto"]["optional"]
    # The raster package cannot exist without heights; the regions package degrades to photos only.
    required = name == "plugin-raster.json"
    assert inputs["surface"].get("optional", False) is not required
    assert inputs["reference_surface"].get("optional", False) is not required


def test_raster_package_differs_only_where_it_must():
    a, b = _manifest("plugin.json"), _manifest("plugin-raster.json")
    pa, pb = a["params_schema"]["properties"], b["params_schema"]["properties"]
    assert set(pa) - set(pb) == {"appearance_change"} and set(pb) - set(pa) == {"mask_insignificant"}
    for key in set(pa) & set(pb):
        assert pa[key] == pb[key], key


def test_build_produces_two_installable_packages(tmp_path):
    res = subprocess.run([os.path.join(PLUGIN_DIR, "tools", "build.sh"), str(tmp_path)],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr
    for name, manifest in ((f"change-detection-{__version__}.zip", "plugin.json"),
                           (f"change-detection-raster-{__version__}.zip", "plugin-raster.json")):
        with zipfile.ZipFile(tmp_path / name) as zf:
            members = set(zf.namelist())
            packaged = json.loads(zf.read("plugin.json"))
        assert packaged == _manifest(manifest)
        assert {"plugin.json", "main.py", "cdplugin/pipeline.py", "cdplugin/align.py", "README.md"} <= members
        assert not any(m.startswith(("tests/", "tools/")) or "__pycache__" in m or m.endswith(".pyc")
                       for m in members)
        assert "plugin-raster.json" not in members
