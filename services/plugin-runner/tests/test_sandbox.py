"""Sandbox executor: contract, failure modes, limits, cleanup."""

import json
import os
import zipfile

import pytest
import rasterio

from app import sandbox
from tests.conftest import make_package

# A plugin that copies its input to the output and echoes params as metadata.
COPY_PLUGIN = """
import json, shutil, sys
req = json.load(open(sys.argv[1]))
shutil.copyfile(req["inputs"]["raster"], req["output_path"])
json.dump({"metadata": {"params": req["params"]}}, open(req["result_path"], "w"))
"""


def _run(pkg, dem, run_dir, out=None, **kw):
    out = out or os.path.join(run_dir, "output.tif")
    return sandbox.run_package(pkg, {"raster": dem}, kw.pop("params", {"a": 1}), out, run_dir, **kw)


def test_example_plugin_runs_end_to_end(example_package, dem, run_dir):
    out = os.path.join(run_dir, "output.tif")
    result = sandbox.run_package(
        example_package, {"raster": dem}, {"threshold": 120, "mode": "above"}, out, run_dir,
        output_kind="raster", timeout_seconds=60,
    )
    assert result["output_path"] == out
    md = result["metadata"]
    # Plugin-reported numbers...
    assert md["masked_pixels"] == 64
    assert md["mode"] == "above"
    # ...plus the georeferencing the runner adds for the map.
    assert md["epsg"] == 32633
    assert md["extent"]["type"] == "Polygon"
    assert len(md["bounds_4326"]) == 4
    with rasterio.open(out) as ds:
        assert ds.read(1)[8, 8] == 1
    # Only the output is left behind for Frappe to collect.
    assert sorted(os.listdir(run_dir)) == ["output.tif"]


def test_metadata_and_params_roundtrip(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": COPY_PLUGIN})
    result = _run(pkg, dem, run_dir, params={"threshold": 3, "mode": "x"})
    assert result["metadata"]["params"] == {"threshold": 3, "mode": "x"}
    assert result["metadata"]["width"] == 16


def test_sibling_module_imports_work(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {
        "main.py": "import helper, sys, json\nhelper.go(json.load(open(sys.argv[1])))\n",
        "helper.py": "import shutil\ndef go(req):\n    shutil.copyfile(req['inputs']['raster'], req['output_path'])\n",
    })
    assert _run(pkg, dem, run_dir)["metadata"]["band_count"] == 1


def test_nonzero_exit_reports_stderr(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": "raise RuntimeError('kaboom')\n"})
    with pytest.raises(sandbox.PluginError) as e:
        _run(pkg, dem, run_dir)
    assert "status 1" in str(e.value)
    assert "kaboom" in str(e.value)


def test_missing_output_is_an_error(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": "print('did nothing')\n"})
    with pytest.raises(sandbox.PluginError, match="wrote no output"):
        _run(pkg, dem, run_dir)


def test_timeout_kills_the_plugin(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": "import time\ntime.sleep(30)\n"})
    with pytest.raises(sandbox.PluginError, match="timed out"):
        _run(pkg, dem, run_dir, timeout_seconds=1)
    assert os.listdir(run_dir) == []


def test_forked_daemon_does_not_outlive_the_run(tmp_path, dem, run_dir, monkeypatch):
    """A plugin that setsid()s a daemon then exits must not leave it running.

    Runs share the sandbox volume, so a survivor could read the next run's
    staged inputs (another organization's data). The runner must reap anything
    the plugin forked out of its process group on *success* too, not just on
    timeout.
    """
    if not sandbox._become_child_subreaper():
        pytest.skip("PR_SET_CHILD_SUBREAPER unavailable")
    # The default RLIMIT_NPROC (128) counts every process of the invoking uid;
    # a busy CI/dev host can already exceed it, which would make os.fork() fail
    # before the plugin gets to spawn its daemon. The limit is orthogonal to the
    # containment under test.
    monkeypatch.setattr(sandbox, "MAX_PROCESSES", 4096)

    pidfile = os.path.join(run_dir, "daemon.pid")
    pkg = make_package(tmp_path / "daemon.zip", {"main.py": (
        "import json, os, sys, time\n"
        "req = json.load(open(sys.argv[1]))\n"
        "open(req['output_path'], 'wb').write(b'x')\n"
        "pid = os.fork()\n"
        "if pid == 0:\n"
        "    os.setsid()\n"
        "    time.sleep(60)\n"
        "    os._exit(0)\n"
        "open(" + repr(pidfile) + ", 'w').write(str(pid))\n"
        "os._exit(0)\n"
    )})
    sandbox.run_package(
        pkg, {"raster": dem}, {}, os.path.join(run_dir, "output.dat"), run_dir, output_kind="other"
    )

    pid = int(open(pidfile).read())
    try:
        os.waitpid(pid, 0)  # reap it if it was reparented to the test process
    except ChildProcessError:
        pass
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_plugin_environment_is_scrubbed(tmp_path, dem, run_dir, monkeypatch):
    monkeypatch.setenv("SUPER_SECRET", "hunter2")
    pkg = make_package(tmp_path / "p.zip", {"main.py": (
        "import os, sys, json\n"
        "assert 'SUPER_SECRET' not in os.environ, 'env leaked'\n"
        "req = json.load(open(sys.argv[1]))\n"
        "open(req['output_path'], 'wb').write(b'x')\n"
    )})
    _run(pkg, dem, run_dir, output_kind="other")


def test_unreadable_raster_output_is_a_plugin_error(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": (
        "import sys, json\nreq = json.load(open(sys.argv[1]))\n"
        "open(req['output_path'], 'wb').write(b'not a tiff')\n"
    )})
    with pytest.raises(sandbox.PluginError, match="not a readable raster"):
        _run(pkg, dem, run_dir, output_kind="raster")


def test_vector_output_gets_bounds(tmp_path, dem, run_dir):
    fc = {"type": "FeatureCollection", "features": [
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [10.0, 50.0]}},
        {"type": "Feature", "properties": {}, "geometry": {"type": "Point", "coordinates": [11.0, 51.0]}},
    ]}
    pkg = make_package(tmp_path / "p.zip", {"main.py": (
        "import sys, json\nreq = json.load(open(sys.argv[1]))\n"
        f"json.dump({json.dumps(fc)}, open(req['output_path'], 'w'))\n"
    )})
    out = os.path.join(run_dir, "output.geojson")
    result = _run(pkg, dem, run_dir, out=out, output_kind="vector")
    assert result["metadata"]["feature_count"] == 2
    assert result["metadata"]["bounds_4326"] == [10.0, 50.0, 11.0, 51.0]


def test_missing_input_is_a_sandbox_error(tmp_path, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": ""})
    with pytest.raises(sandbox.SandboxError, match="not found"):
        sandbox.run_package(pkg, {"raster": "/nope.tif"}, {}, os.path.join(run_dir, "o"), run_dir)


# ---- package validation --------------------------------------------------

def test_rejects_path_traversal(tmp_path, dem, run_dir):
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("plugin.json", "{}")
        zf.writestr("../escape.py", "")
    with pytest.raises(sandbox.PluginError, match="unsafe path"):
        _run(str(path), dem, run_dir)


def test_rejects_symlink_member(tmp_path, dem, run_dir):
    path = tmp_path / "evil.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("plugin.json", "{}")
        info = zipfile.ZipInfo("link")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(sandbox.PluginError, match="symlink"):
        _run(str(path), dem, run_dir)


def test_rejects_missing_manifest_and_entrypoint(tmp_path, dem, run_dir):
    path = tmp_path / "no-manifest.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("main.py", "")
    with pytest.raises(sandbox.PluginError, match="plugin.json"):
        _run(str(path), dem, run_dir)

    pkg = make_package(tmp_path / "no-entry.zip", {"other.py": ""})
    with pytest.raises(sandbox.PluginError, match="entrypoint not found"):
        _run(pkg, dem, run_dir)


def test_rejects_entrypoint_outside_package(tmp_path, dem, run_dir):
    pkg = make_package(tmp_path / "p.zip", {"main.py": ""},
                       manifest={"entrypoint": "../../etc/passwd"})
    with pytest.raises(sandbox.PluginError, match="entrypoint"):
        _run(pkg, dem, run_dir)


def test_rejects_not_a_zip(tmp_path, dem, run_dir):
    path = tmp_path / "junk.zip"
    path.write_bytes(b"definitely not a zip")
    with pytest.raises(sandbox.PluginError, match="not a valid zip"):
        _run(str(path), dem, run_dir)
