import json
import subprocess
import types

import pytest

from meister import env_setup
from meister.config import MeisterConfig


def _write(root, relative, content="{}"):
    path = root.joinpath(*relative.split("/"))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _package(root, relative="package.json", **body):
    _write(root, relative, json.dumps({"name": "pkg", **body}))


@pytest.fixture
def installs(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append((command, kwargs["cwd"]))
        return Result()

    # só o `subprocess` visto pelo env_setup, como em test_env_setup_no_deps.py
    monkeypatch.setattr(
        env_setup,
        "subprocess",
        types.SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired),
    )
    return calls


def test_root_with_a_subpackage_that_has_its_own_lockfile_installs_both(tmp_path, installs):
    _package(tmp_path, dependencies={"left-pad": "1.3.0"})
    _package(tmp_path, "backend/package.json")
    _write(tmp_path, "backend/package-lock.json")

    ok, _ = env_setup.prepare_environment(str(tmp_path), MeisterConfig())

    assert ok
    assert installs == [
        (["npm", "install"], str(tmp_path)),
        (["npm", "ci"], str(tmp_path / "backend")),
    ]


def test_subpackage_without_lockfile_is_ignored(tmp_path, installs):
    _package(tmp_path, dependencies={"left-pad": "1.3.0"})
    _package(tmp_path, "frontend/package.json")

    ok, _ = env_setup.prepare_environment(str(tmp_path), MeisterConfig())

    assert ok
    assert installs == [(["npm", "install"], str(tmp_path))]


@pytest.mark.parametrize("ignored", ["node_modules", ".git", ".venv", "venv", "dist", "build", ".claude", ".meister"])
def test_ignored_directories_are_not_scanned(tmp_path, installs, ignored):
    _package(tmp_path, dependencies={"left-pad": "1.3.0"})
    _package(tmp_path, f"{ignored}/app/package.json")
    _write(tmp_path, f"{ignored}/app/package-lock.json")

    ok, _ = env_setup.prepare_environment(str(tmp_path), MeisterConfig())

    assert ok
    assert installs == [(["npm", "install"], str(tmp_path))]


def test_subpackages_are_found_up_to_three_levels_deep(tmp_path, installs):
    _package(tmp_path, dependencies={"left-pad": "1.3.0"})
    for relative in ("a/b/c", "a/b/c/d"):
        _package(tmp_path, f"{relative}/package.json")
        _write(tmp_path, f"{relative}/yarn.lock")

    ok, _ = env_setup.prepare_environment(str(tmp_path), MeisterConfig())

    assert ok
    assert installs == [
        (["npm", "install"], str(tmp_path)),
        (["yarn", "install", "--frozen-lockfile"], str(tmp_path / "a" / "b" / "c")),
    ]


def test_subpackages_install_in_sorted_path_order(tmp_path, installs):
    _package(tmp_path, dependencies={"left-pad": "1.3.0"})
    for relative in ("zeta", "alpha", "mid/inner"):
        _package(tmp_path, f"{relative}/package.json")
        _write(tmp_path, f"{relative}/package-lock.json")

    ok, _ = env_setup.prepare_environment(str(tmp_path), MeisterConfig())

    assert ok
    assert [cwd for _, cwd in installs[1:]] == [
        str(tmp_path / "alpha"),
        str(tmp_path / "mid" / "inner"),
        str(tmp_path / "zeta"),
    ]


def test_root_with_workspaces_does_not_install_subpackages(tmp_path, installs):
    _package(tmp_path, workspaces=["backend"])
    _package(tmp_path, "backend/package.json")
    _write(tmp_path, "backend/package-lock.json")

    ok, _ = env_setup.prepare_environment(str(tmp_path), MeisterConfig())

    assert ok
    assert installs == [(["npm", "install"], str(tmp_path))]


def test_root_with_pnpm_workspace_does_not_install_subpackages(tmp_path, installs):
    _package(tmp_path, dependencies={"left-pad": "1.3.0"})
    _write(tmp_path, "pnpm-workspace.yaml", "packages:\n  - backend\n")
    _package(tmp_path, "backend/package.json")
    _write(tmp_path, "backend/pnpm-lock.yaml")

    ok, _ = env_setup.prepare_environment(str(tmp_path), MeisterConfig())

    assert ok
    assert [cwd for _, cwd in installs] == [str(tmp_path)]


def test_gate_install_does_not_install_subpackages(tmp_path, installs):
    _package(tmp_path, dependencies={"left-pad": "1.3.0"})
    _package(tmp_path, "backend/package.json")
    _write(tmp_path, "backend/package-lock.json")
    config = MeisterConfig()
    config.gate.install = "make deps"

    ok, _ = env_setup.prepare_environment(str(tmp_path), config)

    assert ok
    assert installs == [(["make", "deps"], str(tmp_path))]


def test_subpackage_failure_reports_command_and_directory(tmp_path, monkeypatch):
    _package(tmp_path, dependencies={"left-pad": "1.3.0"})
    _package(tmp_path, "backend/package.json")
    _write(tmp_path, "backend/package-lock.json")

    class Result:
        stdout = ""
        stderr = "boom"

        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(command, **kwargs):
        return Result(1 if kwargs["cwd"].endswith("backend") else 0)

    monkeypatch.setattr(
        env_setup,
        "subprocess",
        types.SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired),
    )

    ok, message = env_setup.prepare_environment(str(tmp_path), MeisterConfig())

    assert ok is False
    assert message.startswith("backend: ")
    assert "npm ci" in message and "rc=1" in message and "boom" in message
