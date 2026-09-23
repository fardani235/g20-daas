"""HTTP layer: sandbox path confinement and error mapping."""

import asyncio
import os

import pytest
from fastapi import HTTPException

from app import main as app_main
from app import sandbox
from tests.conftest import make_package

OK_PLUGIN = (
    "import sys, json, shutil\nreq = json.load(open(sys.argv[1]))\n"
    "shutil.copyfile(req['inputs']['raster'], req['output_path'])\n"
)


@pytest.fixture
def confined(tmp_path, monkeypatch):
    """Point SANDBOX_DIR at tmp_path so requests inside it are accepted."""
    monkeypatch.setattr(sandbox, "SANDBOX_DIR", os.path.realpath(str(tmp_path)))
    return tmp_path


def _req(tmp_path, dem, run_dir, **over):
    body = {
        "package_path": make_package(tmp_path / "p.zip", {"main.py": OK_PLUGIN}),
        "inputs": {"raster": dem},
        "params": {},
        "output_path": os.path.join(run_dir, "output.tif"),
        "output_kind": "raster",
        "run_dir": run_dir,
    }
    body.update(over)
    return app_main.RunRequest(**body)


def test_health_reports_sandbox(confined):
    res = asyncio.run(app_main.health())
    assert res["status"] == "ok"
    assert res["sandbox_dir"] == sandbox.SANDBOX_DIR


def test_run_inside_sandbox_succeeds(confined, dem, run_dir):
    res = asyncio.run(app_main.run(_req(confined, dem, run_dir)))
    assert res["metadata"]["width"] == 16


def test_rejects_paths_outside_sandbox(confined, dem, run_dir):
    with pytest.raises(HTTPException) as e:
        asyncio.run(app_main.run(_req(confined, dem, run_dir, output_path="/etc/out.tif")))
    assert e.value.status_code == 400
    assert "outside the sandbox" in e.value.detail

    with pytest.raises(HTTPException) as e:
        asyncio.run(app_main.run(_req(confined, dem, run_dir, inputs={"raster": "/etc/passwd"})))
    assert e.value.status_code == 400


def test_rejects_relative_paths(confined, dem, run_dir):
    with pytest.raises(HTTPException) as e:
        asyncio.run(app_main.run(_req(confined, dem, run_dir, output_path="output.tif")))
    assert e.value.status_code == 400
    assert "absolute" in e.value.detail


def test_missing_package_is_404(confined, dem, run_dir):
    with pytest.raises(HTTPException) as e:
        asyncio.run(app_main.run(_req(confined, dem, run_dir, package_path=str(confined / "nope.zip"))))
    assert e.value.status_code == 404


def test_plugin_failure_is_422(confined, dem, run_dir):
    bad = make_package(confined / "bad.zip", {"main.py": "raise SystemExit(3)\n"})
    with pytest.raises(HTTPException) as e:
        asyncio.run(app_main.run(_req(confined, dem, run_dir, package_path=bad)))
    assert e.value.status_code == 422
    assert "status 3" in e.value.detail


def test_model_output_kind_and_context_are_accepted(confined, dem, run_dir):
    req = _req(confined, dem, run_dir, output_kind="model", context={"task": {"name": "T1"}})
    assert req.output_kind == "model" and req.context == {"task": {"name": "T1"}}
    with pytest.raises(HTTPException) as e:
        asyncio.run(app_main.run(_req(confined, dem, run_dir, output_kind="table")))
    assert e.value.status_code == 400 and "output_kind" in e.value.detail
