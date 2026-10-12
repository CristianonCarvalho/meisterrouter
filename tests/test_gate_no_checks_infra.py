"""Gate sem testes nem linters detectados é erro de infraestrutura, não reparo (BACKLOG I8)."""

import os
import subprocess

import pytest

from meister.config import load_config
from meister.engine.bridge import is_infrastructure_error
from meister.gate import DeterministicGate
from meister.i18n import reset_language_cache
from meister.worktree import CodedMessage, IntegrationPipeline, WorktreeManager


@pytest.fixture(autouse=True)
def english(monkeypatch):
    monkeypatch.setenv("MEISTER_LANG", "en")
    reset_language_cache()
    yield
    reset_language_cache()


def _git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _config(tmp_path, allow_unverified):
    path = tmp_path / f"config_{allow_unverified}.yaml"
    flag = "true" if allow_unverified else "false"
    path.write_text(
        "environment: {install_dependencies: false}\n"
        f"gate: {{allow_unverified: {flag}, cache: false}}\n"
    )
    return load_config(str(path))


def _project(tmp_path, files):
    project = tmp_path / "project"
    project.mkdir()
    for name, content in files.items():
        (project / name).write_text(content)
    return project


def test_no_checks_is_infrastructure_error(tmp_path):
    project = _project(tmp_path, {"app.py": "print('hi')\n"})
    result = DeterministicGate(str(project), _config(tmp_path, False)).run_verification_ex()
    assert result.passed is False
    assert result.infrastructure_error is True
    assert "project owner" in result.output


def test_allow_unverified_still_passes(tmp_path):
    project = _project(tmp_path, {"app.py": "print('hi')\n"})
    result = DeterministicGate(str(project), _config(tmp_path, True)).run_verification_ex()
    assert result.passed is True
    assert result.infrastructure_error is False
    assert "[UNVERIFIED]" in result.output


def test_detected_pytest_does_not_hit_no_checks_branch(tmp_path):
    project = _project(tmp_path, {"app.py": "x = 1\n", "conftest.py": ""})
    gate = DeterministicGate(str(project), _config(tmp_path, False))
    assert gate.detect_test_runner() == "pytest"
    result = gate.run_verification_ex()
    assert "project owner" not in result.output


def test_no_checks_becomes_gate_infrastructure_without_repair(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "test@example.invalid")
    _git(repo, "config", "user.name", "Tests")
    (repo / "app.py").write_text("base\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "init")

    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "wts"))
    gate = DeterministicGate(str(repo), _config(tmp_path, False))
    pipeline = IntegrationPipeline(manager, gate=gate)
    integration = pipeline.start_integration("no-checks")
    worker = manager.create_worktree("worker", base_ref=integration.branch_name)
    with open(os.path.join(worker.worktree_path, "app.py"), "a") as output:
        output.write("worker change\n")

    ok, message = pipeline.integrate_subtask(worker, target_files=["app.py"])
    assert not ok
    assert isinstance(message, CodedMessage)
    assert message.code == "gate_infrastructure"
    assert message.startswith("GATE INFRASTRUCTURE ERROR:")
    assert "project owner" in message
    assert is_infrastructure_error(message)
    preserved_sha = _git(repo, "rev-parse", "meister/worktree/worker").stdout.strip()
    assert _git(repo, "show", f"{preserved_sha}:app.py").stdout.endswith("worker change\n")
