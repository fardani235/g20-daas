"""The package must stay consistent: manifest ⇄ model cards, and the shape the
platform's upload validation (webodm_core.plugins.package) expects."""

import json
import os
import re
import subprocess
import sys

from detplugin.registry import load_cards
from tests.conftest import PLUGIN_DIR, SHIPPED_MODELS

MANIFEST = os.path.join(PLUGIN_DIR, "plugin.json")


def _manifest():
    with open(MANIFEST, encoding="utf-8") as f:
        return json.load(f)


def test_manifest_shape_matches_platform_rules():
    m = _manifest()
    assert re.match(r"^[a-z0-9][a-z0-9-]{1,63}$", m["id"]) and m["id"] == "object-detection"
    assert re.match(r"^\d+(\.\d+){0,3}$", m["version"])
    assert 0 < len(m["label"]) <= 140 and len(m["description"]) <= 2000
    assert m["entrypoint"] == "main.py" and os.path.isfile(os.path.join(PLUGIN_DIR, "main.py"))
    assert m["inputs"] == [{"name": "orthophoto", "label": "Orthophoto (RGB)", "datasets": ["orthophoto"]}]
    assert m["output_kind"] == "vector" and m["render_kind"] == "detections"
    assert 10 <= m["timeout_seconds"] <= 3600
    schema = m["params_schema"]
    assert schema["type"] == "object" and schema["required"] == []
    for name, spec in schema["properties"].items():
        assert spec["type"] in ("string", "number", "integer"), name
        assert "default" in spec and "title" in spec and "description" in spec, name
        if spec["type"] in ("number", "integer"):
            assert "minimum" in spec, name


def test_model_enum_lists_every_card_and_default():
    m = _manifest()
    model = m["params_schema"]["properties"]["model"]
    cards = load_cards(SHIPPED_MODELS, require_files=False)
    assert sorted(model["enum"]) == cards.all_ids
    assert model["default"] == model["enum"][0] == "deepforest-tree-crowns"
    assert all(cards[i].label in model["description"] for i in cards)


def test_update_manifest_check_passes():
    res = subprocess.run([sys.executable, os.path.join(PLUGIN_DIR, "tools", "update_manifest.py"), "--check"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stdout + res.stderr


def test_fetch_models_list_shows_licences():
    res = subprocess.run([sys.executable, os.path.join(PLUGIN_DIR, "tools", "fetch_models.py"), "--list"],
                         capture_output=True, text=True)
    assert res.returncode == 0, res.stderr
    assert "deepforest-tree-crowns" in res.stdout and "MIT" in res.stdout
    assert "visdrone-yolov11s" in res.stdout and "AGPL-3.0" in res.stdout


def test_no_weights_are_tracked_by_git():
    """Weights are fetched at build time, never committed (size, and AGPL for YOLO exports)."""
    res = subprocess.run(["git", "ls-files", "models"], cwd=PLUGIN_DIR, capture_output=True, text=True)
    if res.returncode != 0:  # not a git checkout (e.g. an extracted package)
        return
    tracked = [line for line in res.stdout.splitlines() if line.endswith((".onnx", ".pt", ".pth"))]
    assert tracked == [], tracked


def test_build_script_is_executable():
    build = os.path.join(PLUGIN_DIR, "tools", "build.sh")
    assert os.access(build, os.X_OK)
