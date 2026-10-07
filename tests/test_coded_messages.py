"""Tests for CodedMessage semantic reason codes across pipeline and bridge."""

import subprocess
from pathlib import Path
import pytest

from meister.worktree import (
    CodedMessage,
    GATE_INFRASTRUCTURE_PREFIX,
    WorktreeManager,
    IntegrationPipeline,
)
from meister.gate import VerificationResult
from meister.herdr.bridge import (
    extract_rejection_reason,
    is_infrastructure_error,
)


def test_coded_message_str_semantics():
    msg = CodedMessage("Violação de escopo: app.py", code="scope")

    # Inherits from str
    assert isinstance(msg, str)
    assert msg == "Violação de escopo: app.py"
    assert msg.code == "scope"

    # String methods behave identically
    assert msg.startswith("Violação")
    assert "escopo" in msg
    assert msg.strip() == "Violação de escopo: app.py"

    # Concatenation produces a string with same text
    concatenated = msg + " - detalhe"
    assert concatenated == "Violação de escopo: app.py - detalhe"

    # Default code is 'integration'
    default_msg = CodedMessage("Falha generica")
    assert default_msg.code == "integration"


class FakeGate:
    def __init__(self, passed=True, output="ok", infrastructure_error=False):
        self.passed = passed
        self.output = output
        self.infrastructure_error = infrastructure_error
        self.calls = []

    def run_verification_ex(self, repo_path, docs_only=False):
        self.calls.append((repo_path, docs_only))
        return VerificationResult(
            passed=self.passed,
            output=self.output,
            infrastructure_error=self.infrastructure_error,
        )


@pytest.fixture
def temp_git_repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.local"], cwd=repo, check=True)

    readme = repo / "README.md"
    readme.write_text("# Test Repo\n", encoding="utf-8")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True)

    wt_dir = tmp_path / "worktrees"
    wt_dir.mkdir()
    return repo, wt_dir


def test_pipeline_failure_codes(temp_git_repo):
    repo, wt_dir = temp_git_repo
    gate = FakeGate(passed=True, output="ok")
    wt_mgr = WorktreeManager(repo_root=str(repo), worktrees_dir=str(wt_dir))
    pipeline = IntegrationPipeline(worktree_manager=wt_mgr, gate=gate)

    # 1. Uninitialized pipeline
    wt_worker = wt_mgr.create_worktree("t_uninit")
    ok, err = pipeline.integrate_subtask(wt_worker)
    assert not ok
    assert isinstance(err, CodedMessage)
    assert err.code == "integration"
    wt_mgr.cleanup_worktree("t_uninit", force=True)

    # Initialize integration pipeline
    pipeline.start_integration("run_test")

    # 2. Scope violation -> code: 'scope'
    wt_scope = wt_mgr.create_worktree("t_scope")
    out_file = Path(wt_scope.worktree_path) / "unexpected.txt"
    out_file.write_text("rogue file\n", encoding="utf-8")
    ok, err = pipeline.integrate_subtask(wt_scope, target_files=["expected.txt"])
    assert not ok
    assert isinstance(err, CodedMessage)
    assert err.code == "scope"
    wt_mgr.cleanup_worktree("t_scope", force=True)

    # 3. No changes -> code: 'no_changes'
    wt_no_changes = wt_mgr.create_worktree("t_no_changes")
    ok, err = pipeline.integrate_subtask(wt_no_changes, target_files=["expected.txt"])
    assert not ok
    assert isinstance(err, CodedMessage)
    assert err.code == "no_changes"
    wt_mgr.cleanup_worktree("t_no_changes", force=True)

    # 4. Worker gate failure -> code: 'gate'
    gate.passed = False
    gate.output = "Unit test failed"
    gate.infrastructure_error = False
    wt_gate = wt_mgr.create_worktree("t_gate")
    (Path(wt_gate.worktree_path) / "expected.txt").write_text("change", encoding="utf-8")
    ok, err = pipeline.integrate_subtask(wt_gate, target_files=["expected.txt"])
    assert not ok
    assert isinstance(err, CodedMessage)
    assert err.code == "gate"
    wt_mgr.cleanup_worktree("t_gate", force=True)

    # 5. Worker gate infrastructure error -> code: 'gate_infrastructure'
    gate.passed = False
    gate.output = "Docker daemon not reachable"
    gate.infrastructure_error = True
    wt_infra = wt_mgr.create_worktree("t_infra")
    (Path(wt_infra.worktree_path) / "expected.txt").write_text("change", encoding="utf-8")
    ok, err = pipeline.integrate_subtask(wt_infra, target_files=["expected.txt"])
    assert not ok
    assert isinstance(err, CodedMessage)
    assert err.code == "gate_infrastructure"
    wt_mgr.cleanup_worktree("t_infra", force=True)

    # 6. Integration gate failure after merge -> code: 'gate'
    # Worker gate passes, but integration gate fails
    call_count = [0]
    def alternating_gate(repo_path, docs_only=False):
        call_count[0] += 1
        if call_count[0] == 1:
            # Worker gate passes
            return VerificationResult(passed=True, output="worker ok")
        else:
            # Integration gate fails
            return VerificationResult(passed=False, output="integration gate failed")

    pipeline.gate.run_verification_ex = alternating_gate
    wt_post_gate = wt_mgr.create_worktree("t_post_gate")
    (Path(wt_post_gate.worktree_path) / "expected.txt").write_text("change2", encoding="utf-8")
    ok, err = pipeline.integrate_subtask(wt_post_gate, target_files=["expected.txt"])
    assert not ok
    assert isinstance(err, CodedMessage)
    assert err.code == "gate"
    wt_mgr.cleanup_worktree("t_post_gate", force=True)

    # 7. validate_final_integration
    pipeline.gate.run_verification_ex = lambda repo_path, docs_only=False: VerificationResult(
        passed=False, output="final failed", infrastructure_error=False
    )
    ok_fin, err_fin = pipeline.validate_final_integration()
    assert not ok_fin
    assert isinstance(err_fin, CodedMessage)
    assert err_fin.code == "gate"

    pipeline.gate.run_verification_ex = lambda repo_path, docs_only=False: VerificationResult(
        passed=False, output="runner timeout", infrastructure_error=True
    )
    ok_fin_inf, err_fin_inf = pipeline.validate_final_integration()
    assert not ok_fin_inf
    assert isinstance(err_fin_inf, CodedMessage)
    assert err_fin_inf.code == "gate_infrastructure"


def test_bridge_coded_messages_english_text():
    """Verify that CodedMessage with English text (no Portuguese keywords) produces identical reasons."""
    # 1. no_changes
    msg_no_changes = CodedMessage("Worker modified zero expected files", code="no_changes")
    assert extract_rejection_reason(msg_no_changes) == "no_changes"

    # 2. scope
    msg_scope = CodedMessage("Strict boundary check failed for target files", code="scope")
    assert extract_rejection_reason(msg_scope) == "scope"

    # 3. gate
    msg_gate = CodedMessage("Deterministic verification failed with code 1", code="gate")
    assert extract_rejection_reason(msg_gate) == "gate"

    # 4. merge
    msg_merge = CodedMessage("Git branch integration failed due to conflict", code="merge")
    assert extract_rejection_reason(msg_merge) == "merge"

    # 5. integration
    msg_integration = CodedMessage("Pipeline state invalid", code="integration")
    assert extract_rejection_reason(msg_integration) == "integration"

    # 6. gate_infrastructure
    msg_infra = CodedMessage("Environment setup crashed: network failure", code="gate_infrastructure")
    assert is_infrastructure_error(msg_infra) is True


def test_bridge_legacy_text_fallback():
    """Verify that legacy string messages without .code fall back to text heuristics."""
    # 1. no_changes
    assert extract_rejection_reason("Subtarefa sem alterações no repositório") == "no_changes"

    # 2. scope
    assert extract_rejection_reason("Violação de escopo no worktree: [other.py]") == "scope"
    assert extract_rejection_reason("scope violation: out of bounds") == "scope"

    # 3. gate
    assert extract_rejection_reason("Portão determinístico falhou no worker") == "gate"
    assert extract_rejection_reason("Deterministic quality gate failed") == "gate"

    # 4. merge
    assert extract_rejection_reason("Falha/conflito no merge com a branch") == "merge"

    # 5. integration
    assert extract_rejection_reason("Falha desconhecida no pipeline") == "integration"

    # 6. infrastructure error with prefix
    assert is_infrastructure_error(f"{GATE_INFRASTRUCTURE_PREFIX} erro no ambiente") is True
    assert is_infrastructure_error("Erro de código normal") is False


def test_mutation_ignoring_code_fails_on_english_message():
    """Mutation: if a naive implementation ignored .code and checked only Portuguese words,
    an English message would fail to be properly classified.
    """
    english_msg = CodedMessage("Unauthorized files modified: src/util.py", code="scope")

    # Correct implementation using code
    assert extract_rejection_reason(english_msg) == "scope"

    # Naive implementation checking only Portuguese words
    def naive_text_only(err_str):
        lower = str(err_str).lower()
        if "escopo" in lower:
            return "scope"
        return "integration"

    # Naive text-only check misclassifies as 'integration'!
    assert naive_text_only(english_msg) == "integration"
