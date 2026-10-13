import subprocess
from types import SimpleNamespace

from meister.worktree import IntegrationPipeline, WorktreeCommitError, WorktreeManager


def git(repo, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
    )


def make_repo(path):
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "tracked.txt").write_text("original\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "initial")
    return path


def archive_refs(repo):
    return git(repo, "for-each-ref", "--format=%(refname)", "refs/meister/archive").stdout.decode().splitlines()


def test_commit_exception_becomes_infrastructure_error_without_repair(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    pipeline = IntegrationPipeline(manager)
    integration = pipeline.start_integration("commit-infra-run")
    worker = manager.create_worktree("commit-infra-worker", base_ref=integration.branch_name)
    (tmp_path / "worktrees" / "commit-infra-worker" / "tracked.txt").write_text("worker change\n")

    pipeline._run_gate = lambda *_args, **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        passed=True,
        infrastructure_error=False,
        output="ok",
    )

    def failing_commit(*_args, **_kwargs):
        raise WorktreeCommitError("commit falhou (simulado)", 1, "", "pre-commit: boom")

    monkeypatch.setattr(manager, "commit_worktree", failing_commit)

    prepared = pipeline.prepare_subtask(worker, target_files=["tracked.txt"])

    assert prepared.early is not None
    passed, message = prepared.early
    assert passed is False
    assert getattr(message, "code", None) == "gate_infrastructure"
    assert "commit falhou (simulado)" in message
    assert "não foi reprovado" in message or "not rejected" in message

    refs = [ref for ref in archive_refs(repo) if ref.endswith("-uncommitted")]
    assert len(refs) == 1
    assert git(repo, "show", f"{refs[0]}:tracked.txt").stdout == b"worker change\n"

    pipeline.abort_integration()
