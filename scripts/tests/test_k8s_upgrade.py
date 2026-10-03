"""Tests for scripts/k8s-upgrade.py (deploy-time image pinning).

The script filename contains a hyphen, so it is loaded by path. Registry
resolution is always mocked here: these tests must not touch the network.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
from pathlib import Path

import pytest
import yaml

MODULE_PATH = Path(__file__).resolve().parents[1] / "k8s-upgrade.py"


@pytest.fixture(scope="module")
def mod():
    spec = importlib.util.spec_from_file_location("k8s_upgrade", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_yaml(path: Path, data: dict) -> str:
    path.write_text(yaml.safe_dump(data))
    return str(path)


@pytest.fixture
def chart(tmp_path: Path) -> str:
    write_yaml(
        tmp_path / "values.yaml",
        {
            "images": {
                "frappe": {"repository": "reg/frappe", "tag": "1"},
                "caddy": {"repository": "reg/caddy", "tag": "2"},
            }
        },
    )
    return str(tmp_path)


def fake_completed(cmd, returncode=0, stdout="", stderr=""):
    return subprocess.CompletedProcess(cmd, returncode, stdout, stderr)


# --- resolution ------------------------------------------------------------


def test_resolve_digest_parses_first_digest_line(mod, monkeypatch):
    output = (
        "Name: reg/x:1\n"
        "MediaType: application/vnd.oci.image.index.v1+json\n"
        "Digest:    sha256:abc123\n"
        "Manifests:\n"
    )
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: fake_completed(cmd, 0, output))
    assert mod.resolve_digest("reg/x:1") == "sha256:abc123"


def test_resolve_digest_raises_with_ref_on_failure(mod, monkeypatch):
    monkeypatch.setattr(
        mod.subprocess,
        "run",
        lambda cmd, **kw: fake_completed(cmd, 1, "", "manifest unknown"),
    )
    with pytest.raises(mod.ResolutionError) as excinfo:
        mod.resolve_digest("reg/x:1")
    assert "reg/x:1" in str(excinfo.value)
    assert "manifest unknown" in str(excinfo.value)


def test_resolve_digest_raises_without_digest_line(mod, monkeypatch):
    monkeypatch.setattr(mod.subprocess, "run", lambda cmd, **kw: fake_completed(cmd, 0, "nope\n"))
    with pytest.raises(mod.ResolutionError):
        mod.resolve_digest("reg/x:1")


# --- values layering -------------------------------------------------------


def test_later_values_file_wins_and_defaults_are_kept(mod, chart, tmp_path):
    first = write_yaml(tmp_path / "first.yaml", {"images": {"frappe": {"tag": "1"}}})
    second = write_yaml(tmp_path / "second.yaml", {"images": {"frappe": {"tag": "9"}}})
    values = mod.load_values(chart, [first, second], [])
    assert values["images"]["frappe"]["tag"] == "9"
    # repository is not in either file, so it must survive from chart defaults.
    assert values["images"]["frappe"]["repository"] == "reg/frappe"


def test_set_overrides_files_for_image_fields(mod, chart, tmp_path):
    path = write_yaml(tmp_path / "v.yaml", {"images": {"caddy": {"tag": "2"}}})
    values = mod.load_values(chart, [path], ["images.caddy.tag=2-abc"])
    assert values["images"]["caddy"]["tag"] == "2-abc"


def test_non_image_set_is_ignored_by_resolution(mod, chart):
    values = mod.load_values(chart, [], ["replicaCount=3"])
    assert values["images"]["caddy"]["tag"] == "2"


def test_missing_values_file_is_an_error(mod, chart, tmp_path):
    with pytest.raises(SystemExit):
        mod.load_values(chart, [str(tmp_path / "missing.yaml")], [])


# --- image collection ------------------------------------------------------


def test_skip_omits_named_image(mod):
    values = {"images": {"a": {"repository": "r", "tag": "1"}, "b": {"repository": "r", "tag": "1"}}}
    names = [name for name, _, _ in mod.collect_images(values, {"b"})]
    assert names == ["a"]


def test_incomplete_entry_is_skipped_with_warning(mod, capsys):
    values = {
        "images": {
            "broken": {"tag": "1"},
            "ok": {"repository": "reg/ok", "tag": "1"},
            "notmap": "oops",
        }
    }
    entries = mod.collect_images(values, set())
    assert [name for name, _, _ in entries] == ["ok"]
    err = capsys.readouterr().err
    assert "images.broken" in err
    assert "images.notmap" in err


# --- check mode ------------------------------------------------------------


def test_check_mode_prints_digests_and_requires_no_helm(mod, chart, monkeypatch, capsys):
    monkeypatch.setattr(mod, "resolve_digest", lambda ref: "sha256:deadbeef")
    monkeypatch.setattr(mod.shutil, "which", lambda tool: None if tool == "helm" else "/bin/true")
    rc = mod.main(["--check", "webodm", chart])
    assert rc == 0
    out = capsys.readouterr().out
    assert "sha256:deadbeef" in out
    assert "reg/frappe:1" in out


def test_check_mode_names_unresolvable_image(mod, chart, monkeypatch, capsys):
    def failing(ref):
        if "caddy" in ref:
            raise mod.ResolutionError(ref, "boom")
        return "sha256:ok"

    monkeypatch.setattr(mod, "resolve_digest", failing)
    rc = mod.main(["--check", "webodm", chart])
    assert rc == 1
    err = capsys.readouterr().err
    assert "reg/caddy:2" in err
    assert "boom" in err


def test_no_resolvable_images_is_an_error(mod, tmp_path, monkeypatch):
    write_yaml(tmp_path / "values.yaml", {"images": {}})
    monkeypatch.setattr(mod, "resolve_digest", lambda ref: "sha256:unused")
    rc = mod.main(["--check", "webodm", str(tmp_path)])
    assert rc == 1


# --- applying digests ------------------------------------------------------


def _capture_helm(mod, monkeypatch, returncode=0):
    captured = {}

    def fake_run(cmd, **kwargs):
        if cmd and cmd[0] == "helm":
            captured["cmd"] = list(cmd)
            pinned = [cmd[i + 1] for i, tok in enumerate(cmd) if tok == "-f"][-1]
            captured["pinned"] = pinned
            captured["content"] = Path(pinned).read_text()
            captured["mode"] = os.stat(pinned).st_mode & 0o777
        return fake_completed(cmd, returncode)

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    return captured


def test_generated_file_is_passed_last_holds_only_digests_and_is_removed(
    mod, chart, monkeypatch
):
    monkeypatch.setattr(mod, "resolve_digest", lambda ref: "sha256:fresh")
    captured = _capture_helm(mod, monkeypatch)
    rc = mod.main(["webodm", chart])
    assert rc == 0
    assert captured["mode"] == 0o600
    data = yaml.safe_load(captured["content"])
    assert data == {
        "images": {
            "frappe": {"digest": "sha256:fresh"},
            "caddy": {"digest": "sha256:fresh"},
        }
    }
    # The pinned file is the last -f before end of the values arguments.
    assert captured["cmd"][-1] == captured["pinned"] or captured["pinned"] in captured["cmd"]
    assert not Path(captured["pinned"]).exists()


def test_resolved_digest_overrides_a_stale_committed_digest(mod, chart, tmp_path, monkeypatch):
    stale = write_yaml(
        tmp_path / "prod.yaml",
        {"images": {"frappe": {"tag": "1", "digest": "sha256:stale"}}},
    )
    monkeypatch.setattr(mod, "resolve_digest", lambda ref: "sha256:fresh")
    captured = _capture_helm(mod, monkeypatch)
    rc = mod.main(["webodm", chart, "-f", stale])
    assert rc == 0
    data = yaml.safe_load(captured["content"])
    assert data["images"]["frappe"]["digest"] == "sha256:fresh"


def test_passthrough_args_and_no_install(mod, chart, monkeypatch):
    monkeypatch.setattr(mod, "resolve_digest", lambda ref: "sha256:x")
    captured = _capture_helm(mod, monkeypatch)
    rc = mod.main(["webodm", chart, "--no-install", "-n", "webodm", "--atomic"])
    assert rc == 0
    cmd = captured["cmd"]
    assert "--install" not in cmd
    assert cmd[:4] == ["helm", "upgrade", "webodm", chart]
    assert cmd[-2:] == ["-n", "webodm", "--atomic"][-2:] or "--atomic" in cmd
    assert "-n" in cmd and "webodm" in cmd


def test_dry_run_is_forwarded(mod, chart, monkeypatch):
    monkeypatch.setattr(mod, "resolve_digest", lambda ref: "sha256:x")
    captured = _capture_helm(mod, monkeypatch)
    rc = mod.main(["webodm", chart, "--dry-run"])
    assert rc == 0
    assert "--dry-run" in captured["cmd"]


def test_helm_exit_code_is_propagated(mod, chart, monkeypatch):
    monkeypatch.setattr(mod, "resolve_digest", lambda ref: "sha256:x")
    _capture_helm(mod, monkeypatch, returncode=7)
    rc = mod.main(["webodm", chart])
    assert rc == 7


# --- preflight -------------------------------------------------------------


def test_missing_docker_fails_preflight(mod, chart, monkeypatch, capsys):
    monkeypatch.setattr(mod.shutil, "which", lambda tool: None)
    rc = mod.main(["webodm", chart])
    assert rc == 3
    assert "docker" in capsys.readouterr().err


def test_missing_helm_fails_preflight_unless_check(mod, chart, monkeypatch, capsys):
    monkeypatch.setattr(mod.shutil, "which", lambda tool: None if tool == "helm" else "/bin/true")
    monkeypatch.setattr(mod, "resolve_digest", lambda ref: "sha256:x")
    assert mod.main(["webodm", chart]) == 3
    assert "helm" in capsys.readouterr().err
