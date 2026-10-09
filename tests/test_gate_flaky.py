"""Detecção de teste instável no gate: reexecuta só os testes que falharam antes de reprovar."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from meister.config import GateCommand, MeisterConfig, load_config, validate_config
from meister.gate import DeterministicGate, VerificationResult
from meister.logger import add_event_observer, remove_event_observer
from meister.worktree import IntegrationPipeline, WorktreeManager

PYTEST_COMMAND = "{python} -m pytest -q -p no:cacheprovider"
FLAKY_ID = "test_flaky.py::test_flaky"
ORDER_B_ID = "test_order.py::test_b"


def _flaky_source(log: Path, session: Path) -> str:
    return (
        "import os\n"
        "import pathlib\n"
        f"LOG = pathlib.Path({str(log)!r})\n"
        f"SESSION = pathlib.Path({str(session)!r})\n"
        "\n"
        "def test_session():\n"
        "    with SESSION.open('a', encoding='utf-8') as stream:\n"
        "        stream.write('s')\n"
        "\n"
        "def test_flaky():\n"
        "    runs = LOG.read_text(encoding='utf-8').count('x') if LOG.exists() else 0\n"
        "    with LOG.open('a', encoding='utf-8') as stream:\n"
        "        stream.write('x')\n"
        "    assert runs >= 1\n"
    )


def _always_source(log: Path) -> str:
    return (
        "import pathlib\n"
        f"LOG = pathlib.Path({str(log)!r})\n"
        "\n"
        "def test_always():\n"
        "    with LOG.open('a', encoding='utf-8') as stream:\n"
        "        stream.write('x')\n"
        "    assert False, 'real failure'\n"
    )


ORDER_SOURCE = (
    "import os\n"
    "\n"
    "def test_a():\n"
    "    os.environ['MEISTER_FLAKY_ORDER_A'] = '1'\n"
    "\n"
    "def test_b():\n"
    "    assert not os.environ.get('MEISTER_FLAKY_ORDER_A')\n"
)


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _make_repo(root: Path, files: Dict[str, str]) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "flaky-test@meisterrouter.local")
    _git(repo, "config", "user.name", "Meister Flaky Test")
    for name, content in files.items():
        (repo / name).write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _gate(repo: Path, *, retries: int = 2, commands=None, cache: bool = False) -> DeterministicGate:
    config = MeisterConfig()
    config.environment.install_dependencies = False
    config.gate.python = sys.executable
    config.gate.cache = cache
    config.gate.flaky_retries = retries
    if commands is None:
        commands = [GateCommand(name="pytest", run=PYTEST_COMMAND, timeout_seconds=120, required=True)]
    config.gate.commands = commands
    return DeterministicGate(str(repo), config=config)


def _count(path: Path, marker: str) -> int:
    return path.read_text(encoding="utf-8").count(marker) if path.exists() else 0


@pytest.fixture
def rerun_calls(monkeypatch) -> List[List[str]]:
    calls: List[List[str]] = []
    original = DeterministicGate._rerun_failed_tests

    def spy(self, path, python, ids):
        calls.append(list(ids))
        return original(self, path, python, ids)

    monkeypatch.setattr(DeterministicGate, "_rerun_failed_tests", spy)
    return calls


def test_flaky_test_passes_on_rerun_and_full_gate_runs_twice(tmp_path, rerun_calls):
    log = tmp_path / "flaky.log"
    session = tmp_path / "session.log"
    repo = _make_repo(tmp_path, {"test_flaky.py": _flaky_source(log, session)})
    gate = _gate(repo)

    result = gate.run_verification_ex()

    assert result.passed
    assert result.flaky_tests == [FLAKY_ID]
    assert rerun_calls == [[FLAKY_ID]]
    assert "[pytest]" in result.output, "approved result must carry the full gate output"
    assert _count(session, "s") == 2, "full gate must run again after the isolated rerun"


def test_real_failure_is_rejected_with_exactly_flaky_retries_reruns(tmp_path, rerun_calls):
    log = tmp_path / "always.log"
    repo = _make_repo(tmp_path, {"test_always.py": _always_source(log)})
    gate = _gate(repo, retries=2)

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.flaky_tests == []
    assert "FAILED test_always.py::test_always" in result.output
    assert "real failure" in result.output
    assert len(rerun_calls) == 2
    assert _count(log, "x") == 3, "one full run plus exactly two isolated reruns"


def test_isolated_pass_never_approves_alone_when_full_gate_fails(tmp_path, rerun_calls):
    repo = _make_repo(tmp_path, {"test_order.py": ORDER_SOURCE})
    gate = _gate(repo)

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.flaky_tests == []
    assert rerun_calls == [[ORDER_B_ID]], "isolated rerun passes, then the full gate rejects"


def test_no_failed_ids_means_no_rerun(tmp_path, rerun_calls):
    repo = _make_repo(tmp_path, {"test_broken.py": "def test_broken(:\n    pass\n"})
    gate = _gate(repo)

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.flaky_tests == []
    assert rerun_calls == []


def test_lint_failure_means_no_rerun(tmp_path, rerun_calls):
    lint = GateCommand(
        name="ruff",
        run=[
            sys.executable,
            "-c",
            "import sys; print('src/app.py:1:1: E501 line too long'); sys.exit(1)",
        ],
        timeout_seconds=30,
        required=True,
    )
    repo = _make_repo(tmp_path, {"app.txt": "x\n"})
    gate = _gate(repo, commands=[lint])

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.flaky_tests == []
    assert rerun_calls == []


def test_zero_flaky_retries_disables_rerun(tmp_path, rerun_calls):
    log = tmp_path / "always.log"
    repo = _make_repo(tmp_path, {"test_always.py": _always_source(log)})
    gate = _gate(repo, retries=0)

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.flaky_tests == []
    assert rerun_calls == []
    assert _count(log, "x") == 1


def test_infrastructure_error_is_never_rerun(tmp_path, rerun_calls):
    missing = GateCommand(
        name="pytest",
        run=["/nonexistent/meister-flaky-tool"],
        timeout_seconds=30,
        required=True,
    )
    repo = _make_repo(tmp_path, {"app.txt": "x\n"})
    gate = _gate(repo, commands=[missing])

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.infrastructure_error
    assert result.flaky_tests == []
    assert rerun_calls == []


def test_cache_hit_after_flaky_approval_reports_no_flaky_tests(tmp_path, rerun_calls):
    log = tmp_path / "flaky.log"
    session = tmp_path / "session.log"
    repo = _make_repo(tmp_path, {"test_flaky.py": _flaky_source(log, session)})
    gate = _gate(repo, cache=True)

    first = gate.run_verification_ex()
    second = gate.run_verification_ex()

    assert first.passed and first.flaky_tests == [FLAKY_ID]
    assert second.passed
    assert second.cached
    assert second.flaky_tests == []
    assert _count(session, "s") == 2, "cache hit must not run the gate again"
    assert len(rerun_calls) == 1


def test_pipeline_logs_gate_flaky_event_with_context(tmp_path):
    repo = _make_repo(tmp_path, {"a.txt": "x\n"})

    class _FlakyGate:
        def run_verification_ex(self, repo_path=None, docs_only=False):
            return VerificationResult(True, "", flaky_tests=[FLAKY_ID])

    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    pipeline = IntegrationPipeline(manager, gate=_FlakyGate())

    events: List[dict] = []
    add_event_observer(events.append)
    try:
        pipeline._run_gate(str(repo), task_id="task_9", attempt=2, tier="tier_2")
    finally:
        remove_event_observer(events.append)

    flaky = [event for event in events if event.get("event") == "gate_flaky"]
    assert len(flaky) == 1
    assert flaky[0]["task_id"] == "task_9"
    assert flaky[0]["attempt"] == 2
    assert flaky[0]["tier"] == "tier_2"
    assert flaky[0]["failed_tests"] == [FLAKY_ID]


@pytest.mark.parametrize("flaky_tests", [None, []])
def test_pipeline_emits_no_gate_flaky_event_without_flaky_tests(tmp_path, flaky_tests):
    repo = _make_repo(tmp_path, {"a.txt": "x\n"})

    class _Gate:
        def run_verification_ex(self, repo_path=None, docs_only=False):
            if flaky_tests is None:
                return VerificationResult(True, "")
            return VerificationResult(True, "", flaky_tests=list(flaky_tests))

    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    pipeline = IntegrationPipeline(manager, gate=_Gate())

    events: List[dict] = []
    add_event_observer(events.append)
    try:
        pipeline._run_gate(str(repo), task_id="task_9", attempt=1, tier="tier_1")
    finally:
        remove_event_observer(events.append)

    assert not [event for event in events if event.get("event") == "gate_flaky"]


def test_flaky_retries_default_is_two():
    assert MeisterConfig().gate.flaky_retries == 2


def test_flaky_retries_parsing_matches_repair_attempts_rules(tmp_path: Path):
    cfg_file = tmp_path / "custom.yaml"

    cfg_file.write_text("gate:\n  flaky_retries: 0\n")
    loaded = load_config(str(cfg_file))
    assert loaded.gate.flaky_retries == 0
    assert not any(i.path == "gate.flaky_retries" for i in loaded._parse_issues)

    for invalid in ("-1", "true", "'x'", "1.5"):
        cfg_file.write_text(f"gate:\n  flaky_retries: {invalid}\n")
        loaded = load_config(str(cfg_file))
        assert loaded.gate.flaky_retries == 2
        assert any(
            i.path == "gate.flaky_retries" and i.level == "error" for i in loaded._parse_issues
        ), invalid


def test_flaky_retries_validation(tmp_path: Path):
    cfg = MeisterConfig()
    for valid in (0, 1, 5):
        cfg.gate.flaky_retries = valid
        assert not any(i.path == "gate.flaky_retries" for i in validate_config(cfg))

    for invalid in (-1, True, False, "x", 1.5):
        cfg.gate.flaky_retries = invalid  # type: ignore
        issues = validate_config(cfg)
        assert any(
            i.path == "gate.flaky_retries" and i.level == "error" for i in issues
        ), f"Failed for {invalid}"
