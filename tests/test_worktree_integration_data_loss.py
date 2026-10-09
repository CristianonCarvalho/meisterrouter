"""Caracteriza a perda de trabalho nos caminhos de integração do worktree.

Propriedade verificada: nenhum commit, ou arquivo, que exista só no worktree ou no branch
afetado deixa de ser alcançável (por branch, por ref refs/meister/archive/* ou por arquivo
preservado) depois de cada caminho testado.

Os cenários que perdiam trabalho antes da Tarefa 2 agora arquivam antes de qualquer comando
destrutivo (refs/meister/archive/*); os demais são guardas. Nenhum teste usa xfail.
"""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from meister.gate import VerificationResult
from meister.herdr.bridge import extract_rejection_reason
from meister.worktree import CodedMessage, IntegrationPipeline, WorktreeManager


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
    )


def git_out(repo, *args):
    return git(repo, *args).stdout.decode().strip()


def make_repo(path):
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / ".gitignore").write_text("ignored.txt\n")
    (path / "tracked.txt").write_text("original\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "initial")
    return path


def archive_refs(repo):
    return git_out(repo, "for-each-ref", "--format=%(refname)", "refs/meister/archive").splitlines()


def reachable(repo, sha):
    """Verdadeiro se o commit é alcançável a partir de algum ref (branch, tag, archive, HEAD)."""
    return sha in git_out(repo, "rev-list", "--all").split()


def loose_preserved(repo, wt_path, rel, content):
    """Conteúdo sobrevive no disco do worktree ou dentro de um snapshot em refs/meister/archive/*."""
    on_disk = Path(wt_path) / rel
    if on_disk.is_file() and on_disk.read_bytes() == content:
        return True
    for ref in archive_refs(repo):
        try:
            if git(repo, "show", f"{ref}:{rel}").stdout == content:
                return True
        except subprocess.CalledProcessError:
            continue
    return False


def make_exclusive_branch(repo, branch, message="exclusive work"):
    """Cria um branch apontando para um commit próprio, que não pertence a main."""
    tree = git_out(repo, "rev-parse", "HEAD^{tree}")
    head = git_out(repo, "rev-parse", "HEAD")
    sha = git_out(repo, "commit-tree", tree, "-p", head, "-m", message)
    git(repo, "branch", branch, sha)
    return sha


def commit_in_worktree(wt_path, rel, content, message):
    (Path(wt_path) / rel).write_text(content)
    git(wt_path, "add", rel)
    git(wt_path, "commit", "-m", message)
    return git_out(wt_path, "rev-parse", "HEAD")


# ---------------------------------------------------------------------------
# 1. WorktreeManager.rollback_merge
# ---------------------------------------------------------------------------


@pytest.fixture
def merged_integration(tmp_path):
    repo = make_repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    worker = manager.create_worktree("worker")
    worker_sha = commit_in_worktree(worker.worktree_path, "worker.txt", "from worker\n", "worker work")
    integration = manager.create_worktree("integration", branch_name="meister/integration/rollback")
    merged, rollback_sha = manager.merge_branch_into(worker.branch_name, integration.worktree_path)
    assert merged is True
    return SimpleNamespace(
        repo=repo,
        manager=manager,
        worker_sha=worker_sha,
        integration_path=integration.worktree_path,
        rollback_sha=rollback_sha,
    )


def test_rollback_merge_returns_to_previous_sha_and_keeps_worker_commit(merged_integration):
    ctx = merged_integration

    ctx.manager.rollback_merge(ctx.integration_path, ctx.rollback_sha)

    assert git_out(ctx.integration_path, "rev-parse", "HEAD") == ctx.rollback_sha
    assert reachable(ctx.repo, ctx.worker_sha)


def test_rollback_merge_loose_tracked_change_is_preserved(merged_integration):
    ctx = merged_integration
    (Path(ctx.integration_path) / "tracked.txt").write_text("loose tracked edit\n")

    ctx.manager.rollback_merge(ctx.integration_path, ctx.rollback_sha)

    assert loose_preserved(ctx.repo, ctx.integration_path, "tracked.txt", b"loose tracked edit\n")


def test_rollback_merge_loose_untracked_file_is_preserved(merged_integration):
    ctx = merged_integration
    (Path(ctx.integration_path) / "scratch.txt").write_bytes(b"loose untracked\n")

    ctx.manager.rollback_merge(ctx.integration_path, ctx.rollback_sha)

    assert loose_preserved(ctx.repo, ctx.integration_path, "scratch.txt", b"loose untracked\n")


# ---------------------------------------------------------------------------
# 2. IntegrationPipeline.start_integration (reuso do branch com histórico)
# ---------------------------------------------------------------------------


RUN_ID = "reuse-run"
INTEGRATION_BRANCH = f"meister/integration/{RUN_ID}"


@pytest.fixture
def integration_with_history(tmp_path):
    repo = make_repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    pipeline = IntegrationPipeline(manager, gate=SimpleNamespace())
    info = pipeline.start_integration(RUN_ID)
    history_sha = commit_in_worktree(info.worktree_path, "history.txt", "integrated\n", "integrated work")
    return SimpleNamespace(
        repo=repo,
        manager=manager,
        pipeline=pipeline,
        wt_path=info.worktree_path,
        history_sha=history_sha,
    )


def test_start_integration_reuse_keeps_branch_commits(integration_with_history):
    ctx = integration_with_history

    ctx.pipeline.start_integration(RUN_ID)

    assert reachable(ctx.repo, ctx.history_sha)
    assert git_out(ctx.repo, "rev-parse", INTEGRATION_BRANCH) == ctx.history_sha


def test_start_integration_reuse_registered_loose_tracked_change_is_preserved(integration_with_history):
    ctx = integration_with_history
    (Path(ctx.wt_path) / "tracked.txt").write_text("loose tracked edit\n")

    ctx.pipeline.start_integration(RUN_ID)

    assert loose_preserved(ctx.repo, ctx.wt_path, "tracked.txt", b"loose tracked edit\n")


def test_start_integration_reuse_registered_loose_untracked_file_is_preserved(integration_with_history):
    ctx = integration_with_history
    (Path(ctx.wt_path) / "scratch.txt").write_bytes(b"loose untracked\n")

    ctx.pipeline.start_integration(RUN_ID)

    assert loose_preserved(ctx.repo, ctx.wt_path, "scratch.txt", b"loose untracked\n")


def _unregister_worktree(manager, path):
    """Remove o registro do worktree no git e recria o diretório como pasta comum."""
    manager._run_git(["worktree", "remove", "--force", path])
    os.makedirs(path)


def test_start_integration_reuse_unregistered_keeps_branch_commits(integration_with_history):
    ctx = integration_with_history
    _unregister_worktree(ctx.manager, ctx.wt_path)

    ctx.pipeline.start_integration(RUN_ID)

    assert reachable(ctx.repo, ctx.history_sha)
    assert git_out(ctx.repo, "rev-parse", INTEGRATION_BRANCH) == ctx.history_sha


def test_start_integration_reuse_unregistered_loose_tracked_change_is_preserved(integration_with_history):
    ctx = integration_with_history
    _unregister_worktree(ctx.manager, ctx.wt_path)
    (Path(ctx.wt_path) / "tracked.txt").write_text("loose tracked edit\n")

    ctx.pipeline.start_integration(RUN_ID)

    assert loose_preserved(ctx.repo, ctx.wt_path, "tracked.txt", b"loose tracked edit\n")


def test_start_integration_reuse_unregistered_loose_untracked_file_is_preserved(integration_with_history):
    ctx = integration_with_history
    _unregister_worktree(ctx.manager, ctx.wt_path)
    (Path(ctx.wt_path) / "scratch.txt").write_bytes(b"loose untracked\n")

    ctx.pipeline.start_integration(RUN_ID)

    assert loose_preserved(ctx.repo, ctx.wt_path, "scratch.txt", b"loose untracked\n")


# ---------------------------------------------------------------------------
# 3. WorktreeManager.create_worktree com falha transitória de `git worktree add`
# ---------------------------------------------------------------------------


def test_create_worktree_transient_retry_keeps_same_name_branch_commit(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    branch = "meister/worktree/retry-task"
    exclusive_sha = make_exclusive_branch(repo, branch)

    real_run_git = manager._run_git
    state = {"failed_once": False}

    def flaky_run_git(args, cwd=None, env=None):
        if args[:2] == ["worktree", "add"] and not state["failed_once"]:
            state["failed_once"] = True
            raise RuntimeError("fatal: could not lock: Unable to create '.git/worktrees/x/index.lock': File exists.")
        return real_run_git(args, cwd=cwd, env=env)

    monkeypatch.setattr(manager, "_run_git", flaky_run_git)

    info = manager.create_worktree("retry-task")

    assert state["failed_once"] is True
    assert os.path.isdir(info.worktree_path)
    assert reachable(repo, exclusive_sha)


# ---------------------------------------------------------------------------
# 4. IntegrationPipeline.merge_prepared: rollback bloqueado não pode virar 'gate'
# ---------------------------------------------------------------------------


def _integrate_failing_gate(ctx, task_id, rel="worker-change.txt"):
    """Integra um worker cujo gate de integração reprova; o primeiro gate (do worker) passa."""
    calls = {"n": 0}

    def gate(repo_path, docs_only=False):
        calls["n"] += 1
        if calls["n"] == 1:
            return VerificationResult(passed=True, output="worker ok")
        return VerificationResult(passed=False, output="integration gate failed")

    ctx.pipeline.gate.run_verification_ex = gate
    worker = ctx.manager.create_worktree(task_id)
    (Path(worker.worktree_path) / rel).write_text("worker change\n")
    git(worker.worktree_path, "add", rel)
    git(worker.worktree_path, "commit", "-m", f"{task_id} work")
    return ctx.pipeline.integrate_subtask(worker, target_files=[rel])


def test_rollback_blocked_returns_integration_code_and_keeps_failing_merge(integration_with_history, monkeypatch):
    ctx = integration_with_history
    monkeypatch.setattr(ctx.manager, "_archive_uncommitted", lambda *args, **kwargs: (False, None))
    head_before = git_out(ctx.wt_path, "rev-parse", "HEAD")

    ok, err = _integrate_failing_gate(ctx, "blocked-task")

    assert ok is False
    assert isinstance(err, CodedMessage)
    assert err.code == "integration"
    assert "blocked-task" in err
    assert extract_rejection_reason(err) == "integration"
    head_after = git_out(ctx.wt_path, "rev-parse", "HEAD")
    assert head_after != head_before, "o merge reprovado deve permanecer na branch de integração"
    assert (Path(ctx.wt_path) / "worker-change.txt").read_text() == "worker change\n"


def test_rollback_archive_ok_still_resets_and_keeps_gate_code(integration_with_history):
    ctx = integration_with_history
    head_before = git_out(ctx.wt_path, "rev-parse", "HEAD")

    ok, err = _integrate_failing_gate(ctx, "rollback-task")

    assert ok is False
    assert isinstance(err, CodedMessage)
    assert err.code == "gate"
    assert git_out(ctx.wt_path, "rev-parse", "HEAD") == head_before
    assert not (Path(ctx.wt_path) / "worker-change.txt").exists()
