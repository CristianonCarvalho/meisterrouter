"""A mensagem de rejeição do gate do worker começa pelos testes que falharam."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from meister.gate import VerificationResult, summarize_gate_failure
from meister.i18n import CATALOGS, t
from meister.worktree import IntegrationPipeline, WorktreeManager

ALEMBIC_NOISE = (
    "INFO  [alembic.runtime.migration] Context impl SQLiteImpl.\n"
    "INFO  [alembic.runtime.migration] Will assume non-transactional DDL.\n"
)


class _FakeGate:
    def __init__(self, output: str, passed: bool = False) -> None:
        self._result = VerificationResult(passed, output)

    def run_verification_ex(self, repo_path: str, docs_only: bool = False) -> VerificationResult:
        return self._result


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    path = tmp_path / "repo"
    path.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "tester@test.local"], cwd=path, check=True)
    (path / "base.txt").write_text("initial\n")
    subprocess.run(["git", "add", "base.txt"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    return path


def _reject(repo: Path, gate_output: str) -> str:
    wt_mgr = WorktreeManager(repo_root=str(repo), worktrees_dir=str(repo.parent / "wts"))
    pipeline = IntegrationPipeline(wt_mgr, gate=_FakeGate(gate_output))
    pipeline.start_integration("run-gate-msg", base_ref="main")
    subtask_wt = wt_mgr.create_worktree(task_id="sub1", base_ref="main")
    (Path(subtask_wt.worktree_path) / "a.py").write_text("x = 1\n")
    prepared = pipeline.prepare_subtask(subtask_wt, target_files=["a.py"], task_id="sub1")
    ok, message = prepared.early
    assert ok is False
    return str(message)


def test_gate_rejection_starts_with_failed_tests(repo: Path) -> None:
    gate_output = (
        ALEMBIC_NOISE
        + "FAILED tests/a.py::t1 - AssertionError: boom\n"
        + "=========================== 1 failed in 0.10s ===========================\n"
    )
    message = _reject(repo, gate_output)

    first_line = message.splitlines()[0]
    assert "tests/a.py::t1" in first_line
    assert summarize_gate_failure(message)["failed_tests"] == (
        summarize_gate_failure(gate_output)["failed_tests"]
    )


def test_gate_rejection_lists_three_tests_and_counts_the_rest(repo: Path) -> None:
    gate_output = "".join(f"FAILED tests/a.py::t{i}\n" for i in range(5))
    first_line = _reject(repo, gate_output).splitlines()[0]

    assert "tests/a.py::t0, tests/a.py::t1, tests/a.py::t2" in first_line
    assert "t3" not in first_line
    assert first_line.endswith("(+2)")


VITEST_GATE_OUTPUT = (
    " ❯ src/app.test.tsx (2 tests | 2 failed) 45ms\n"
    " FAIL  src/app.test.tsx > App > renders title\n"
    "AssertionError: expected 'Hello' to be 'Hi'\n"
    " FAIL  src/app.test.tsx > App > shows save button\n"
    "TestingLibraryElementError: Unable to find an element with the text: Save\n"
    " Test Files  1 failed (1)\n"
    "      Tests  2 failed (2)\n"
)


def test_vitest_rejection_lists_failing_tests_in_header(repo: Path) -> None:
    first_line = _reject(repo, VITEST_GATE_OUTPUT).splitlines()[0]

    assert first_line == t(
        "engine.worktree.worker_gate_failed_tests",
        tests="src/app.test.tsx > App > renders title, src/app.test.tsx > App > shows save button",
        more="",
    )


def test_pytest_rejection_header_is_unchanged(repo: Path) -> None:
    gate_output = "FAILED tests/a.py::t1 - AssertionError: boom\n"
    message = _reject(repo, gate_output)

    assert message.splitlines()[0] == t(
        "engine.worktree.worker_gate_failed_tests",
        tests="tests/a.py::t1",
        more="",
    )


def test_unrecognized_rejection_has_no_test_header(repo: Path) -> None:
    gate_output = "build exploded in an unknown way\n"
    message = _reject(repo, gate_output)

    assert message == t("engine.worktree.worker_gate_failed", output=gate_output)


def test_lint_only_rejection_message_is_unchanged(repo: Path) -> None:
    gate_output = "[RUFF]\na.py:1:1: F401 `sys` imported but unused\n"
    message = _reject(repo, gate_output)

    assert message == t("engine.worktree.worker_gate_failed", output=gate_output)


def test_failed_tests_key_exists_in_both_catalogs() -> None:
    key = "engine.worktree.worker_gate_failed_tests"
    assert key in CATALOGS["en"]
    assert key in CATALOGS["pt-BR"]
    assert CATALOGS["en"][key].format(tests="x", more="") == "Failing tests: x"
    assert CATALOGS["pt-BR"][key].format(tests="x", more="") == "Testes que falharam: x"
