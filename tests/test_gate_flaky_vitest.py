"""Detecção de teste instável no gate para vitest: reexecuta só os arquivos que falharam."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List

import pytest

from meister.config import GateCommand, MeisterConfig
from meister.gate import DeterministicGate

FLAKY_ITEM = "src/flaky.test.ts > suite > flaky"
FLAKY_FILE = "src/flaky.test.ts"
FAIL_LINE = f"FAIL  {FLAKY_ITEM}"

pytestmark = pytest.mark.skipif(os.name == "nt", reason="shell fake vitest is POSIX-only")


def _fake_vitest_source(log: Path, seen: Path, rerun_exit: int) -> str:
    return (
        "#!/bin/sh\n"
        f"echo \"CI=$CI $*\" >> '{log}'\n"
        'if [ "$#" -gt 1 ]; then\n'
        f"  exit {rerun_exit}\n"
        "fi\n"
        f"if [ -f '{seen}' ]; then\n"
        "  echo 'Tests  2 passed'\n"
        "  exit 0\n"
        "fi\n"
        f"touch '{seen}'\n"
        f"echo '{FAIL_LINE}'\n"
        "echo 'AssertionError: boom'\n"
        "echo 'Tests  1 failed | 1 passed'\n"
        "exit 1\n"
    )


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def _make_repo(root: Path, files: Dict[str, str]) -> Path:
    repo = root / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.email", "flaky-vitest@meisterrouter.local")
    _git(repo, "config", "user.name", "Meister Flaky Vitest Test")
    for name, content in files.items():
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "initial")
    return repo


def _install_fake_vitest(repo: Path, log: Path, rerun_exit: int = 0) -> Path:
    binary = repo / "node_modules" / ".bin" / "vitest"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text(_fake_vitest_source(log, log.with_suffix(".seen"), rerun_exit), encoding="utf-8")
    binary.chmod(0o755)
    return binary


def _gate(repo: Path, *, retries: int = 2, commands=None) -> DeterministicGate:
    config = MeisterConfig()
    config.environment.install_dependencies = False
    config.gate.cache = False
    config.gate.flaky_retries = retries
    config.gate.commands = [] if commands is None else commands
    return DeterministicGate(str(repo), config=config)


def _count(path: Path, marker: str) -> int:
    return path.read_text(encoding="utf-8").count(marker) if path.exists() else 0


def _invocations(log: Path) -> List[str]:
    return log.read_text(encoding="utf-8").splitlines() if log.exists() else []


def _reruns(log: Path) -> List[str]:
    return [line for line in _invocations(log) if not line.endswith(" run")]


def _full_runs(log: Path) -> int:
    return len([line for line in _invocations(log) if line.endswith(" run")])


@pytest.fixture
def js_rerun_calls(monkeypatch) -> List[List[str]]:
    calls: List[List[str]] = []
    original = DeterministicGate._rerun_failed_js_files

    def spy(self, path, files):
        calls.append(list(files))
        return original(self, path, files)

    monkeypatch.setattr(DeterministicGate, "_rerun_failed_js_files", spy)
    return calls


@pytest.fixture
def py_rerun_calls(monkeypatch) -> List[List[str]]:
    calls: List[List[str]] = []
    original = DeterministicGate._rerun_failed_tests

    def spy(self, path, python, ids):
        calls.append(list(ids))
        return original(self, path, python, ids)

    monkeypatch.setattr(DeterministicGate, "_rerun_failed_tests", spy)
    return calls


def _vitest_config_repo(tmp_path: Path, log: Path, rerun_exit: int) -> Path:
    repo = _make_repo(tmp_path, {"vitest.config.ts": "export default {};\n", "src/app.txt": "x\n"})
    _install_fake_vitest(repo, log, rerun_exit=rerun_exit)
    return repo


def test_flaky_vitest_file_passes_on_rerun_and_approves_with_flaky_tests(tmp_path, js_rerun_calls):
    log = tmp_path / "vitest.log"
    repo = _vitest_config_repo(tmp_path, log, rerun_exit=0)
    gate = _gate(repo)

    result = gate.run_verification_ex()

    assert result.passed
    assert result.flaky_tests == [FLAKY_ITEM]
    assert js_rerun_calls == [[FLAKY_FILE]]
    assert "[vitest]" in result.output, "approved result must carry the full gate output"
    assert _full_runs(log) == 2, "full gate must run again after the isolated rerun"
    assert _reruns(log) == [f"CI=1 run {FLAKY_FILE}"]


def test_rerun_keeps_original_rejection_when_still_failing(tmp_path, js_rerun_calls):
    log = tmp_path / "vitest.log"
    repo = _vitest_config_repo(tmp_path, log, rerun_exit=1)
    gate = _gate(repo, retries=2)

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.flaky_tests == []
    assert FAIL_LINE in result.output
    assert "AssertionError: boom" in result.output
    assert js_rerun_calls == [[FLAKY_FILE], [FLAKY_FILE]]
    assert _full_runs(log) == 1
    assert _reruns(log) == [f"CI=1 run {FLAKY_FILE}"] * 2


def test_missing_local_vitest_binary_means_no_rerun(tmp_path, js_rerun_calls):
    repo = _make_repo(tmp_path, {"app.txt": "x\n"})
    failing = GateCommand(
        name="vitest",
        run=[
            sys.executable,
            "-c",
            f"print({FAIL_LINE!r}); print('AssertionError: boom'); raise SystemExit(1)",
        ],
        timeout_seconds=30,
        required=True,
    )
    gate = _gate(repo, commands=[failing])

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.flaky_tests == []
    assert FAIL_LINE in result.output
    assert js_rerun_calls == []


def test_zero_flaky_retries_disables_vitest_rerun(tmp_path, js_rerun_calls):
    log = tmp_path / "vitest.log"
    repo = _vitest_config_repo(tmp_path, log, rerun_exit=0)
    gate = _gate(repo, retries=0)

    result = gate.run_verification_ex()

    assert not result.passed
    assert result.flaky_tests == []
    assert js_rerun_calls == []
    assert _full_runs(log) == 1
    assert _reruns(log) == []


def test_rerun_command_carries_only_failed_files_without_repetition(tmp_path, monkeypatch):
    log = tmp_path / "vitest.log"
    repo = _make_repo(tmp_path, {"app.txt": "x\n"})
    _install_fake_vitest(repo, log, rerun_exit=0)
    failing = GateCommand(
        name="vitest",
        run=[
            sys.executable,
            "-c",
            "print('FAIL  a.test.ts > s > one'); print('FAIL  a.test.ts > s > two');"
            " print('FAIL  b.test.ts > t > three'); print('FAIL  c.test.ts > u > ok');"
            " raise SystemExit(1)",
        ],
        timeout_seconds=30,
        required=True,
    )
    gate = _gate(repo, retries=1, commands=[failing])

    seen: List[List[str]] = []
    original = DeterministicGate._rerun_failed_js_files

    def spy(self, path, files):
        seen.append(list(files))
        return original(self, path, files)

    monkeypatch.setattr(DeterministicGate, "_rerun_failed_js_files", spy)

    gate.run_verification_ex()

    assert seen == [["a.test.ts", "b.test.ts", "c.test.ts"]]
    assert _reruns(log) == ["CI=1 run a.test.ts b.test.ts c.test.ts"]


def test_pytest_output_keeps_pytest_rerun_path(tmp_path, py_rerun_calls, js_rerun_calls):
    repo = _make_repo(
        tmp_path,
        {
            "test_always.py": (
                "def test_always():\n"
                "    assert False, 'real failure'\n"
            ),
        },
    )
    config = MeisterConfig()
    config.environment.install_dependencies = False
    config.gate.python = sys.executable
    config.gate.cache = False
    config.gate.flaky_retries = 2
    config.gate.commands = [
        GateCommand(
            name="pytest",
            run="{python} -m pytest -q -p no:cacheprovider",
            timeout_seconds=120,
            required=True,
        )
    ]
    gate = DeterministicGate(str(repo), config=config)

    result = gate.run_verification_ex()

    assert not result.passed
    assert py_rerun_calls == [["test_always.py::test_always"], ["test_always.py::test_always"]]
    assert js_rerun_calls == []
