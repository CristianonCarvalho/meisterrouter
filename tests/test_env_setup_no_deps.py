import json
import subprocess
import types

import pytest

from meister import env_setup
from meister.config import MeisterConfig


def _project(tmp_path, package=None, raw=None, **files):
    if raw is not None:
        (tmp_path / "package.json").write_text(raw, encoding="utf-8")
    elif package is not None:
        (tmp_path / "package.json").write_text(json.dumps(package), encoding="utf-8")
    for name, content in files.items():
        (tmp_path / name.replace("__", ".")).write_text(content, encoding="utf-8")
    return str(tmp_path)


@pytest.fixture
def installs(monkeypatch):
    calls = []

    class Result:
        returncode = 0
        stdout = "ok"
        stderr = ""

    def fake_run(command, **kwargs):
        calls.append(command)
        return Result()

    # só o `subprocess` visto pelo env_setup: trocar o `subprocess.run` global enganaria o guard de isolamento do conftest
    monkeypatch.setattr(
        env_setup,
        "subprocess",
        types.SimpleNamespace(run=fake_run, TimeoutExpired=subprocess.TimeoutExpired),
    )
    return calls


def test_package_json_without_dependencies_installs_nothing(tmp_path, installs):
    path = _project(tmp_path, {"name": "wrapper", "bin": {"x": "bin/cli.js"}, "scripts": {"start": "node x"}})
    assert env_setup.prepare_environment(path, MeisterConfig()) == (True, "")
    assert installs == []


@pytest.mark.parametrize("key", ["dependencies", "devDependencies", "optionalDependencies"])
def test_package_json_with_dependencies_still_installs(tmp_path, installs, key):
    path = _project(tmp_path, {"name": "app", key: {"left-pad": "1.3.0"}})
    ok, _ = env_setup.prepare_environment(path, MeisterConfig())
    assert ok and installs == [["npm", "install"]]


def test_empty_dependency_objects_count_as_no_dependencies(tmp_path, installs):
    path = _project(tmp_path, {"name": "app", "dependencies": {}, "devDependencies": {}})
    assert env_setup.prepare_environment(path, MeisterConfig()) == (True, "")
    assert installs == []


def test_workspaces_still_install(tmp_path, installs):
    path = _project(tmp_path, {"name": "mono", "workspaces": ["packages/*"]})
    ok, _ = env_setup.prepare_environment(path, MeisterConfig())
    assert ok and installs == [["npm", "install"]]


def test_unreadable_package_json_keeps_the_previous_behaviour(tmp_path, installs):
    path = _project(tmp_path, raw="{not json")
    ok, message = env_setup.prepare_environment(path, MeisterConfig())
    assert ok is False and "Node" in message
    assert installs == []


def test_an_explicit_gate_install_command_runs_even_without_dependencies(tmp_path, installs):
    path = _project(tmp_path, {"name": "wrapper"})
    config = MeisterConfig()
    config.gate.install = "make deps"
    ok, _ = env_setup.prepare_environment(path, config)
    assert ok and installs == [["make", "deps"]]


@pytest.mark.parametrize("signal", ["pnpm-lock.yaml", "package-lock.json", "yarn.lock", "pnpm-workspace.yaml"])
def test_a_lockfile_or_pnpm_workspace_means_install_even_without_declared_dependencies(
    tmp_path, installs, signal
):
    """Monorepos pnpm declaram os pacotes em pnpm-workspace.yaml, não no package.json raiz."""
    path = _project(tmp_path, {"name": "root"})
    (tmp_path / signal).write_text("x", encoding="utf-8")
    ok, _ = env_setup.prepare_environment(path, MeisterConfig())
    assert ok and len(installs) == 1
