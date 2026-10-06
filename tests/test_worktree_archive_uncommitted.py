import os
import subprocess
from types import SimpleNamespace

from meister.worktree import IntegrationPipeline, WorktreeManager


def git(repo, *args, check=True, **kwargs):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=check,
        capture_output=True,
        **kwargs,
    )


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
    return git(
        repo,
        "for-each-ref",
        "--format=%(refname)",
        "refs/meister/archive",
    ).stdout.decode().splitlines()


def test_cleanup_archives_tracked_and_untracked_without_mutating_worktree_state(tmp_path):
    repo = make_repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    worktree = manager.create_worktree("worker")
    (tmp_path / "worktrees" / "worker" / "tracked.txt").write_text("changed\nwith multiple lines\n")
    (tmp_path / "worktrees" / "worker" / "new.bin").write_bytes(b"\x00binary\xffdata")

    main_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    main_index = (repo / ".git" / "index").read_bytes()
    task_branch = git(repo, "rev-parse", worktree.branch_name).stdout.strip()

    assert manager.cleanup_worktree(
        "worker",
        delete_branch=False,
        archive_unmerged=True,
        force=True,
    )

    refs = [ref for ref in archive_refs(repo) if ref.endswith("-uncommitted")]
    assert len(refs) == 1
    assert git(repo, "show", f"{refs[0]}:tracked.txt").stdout == b"changed\nwith multiple lines\n"
    assert git(repo, "show", f"{refs[0]}:new.bin").stdout == b"\x00binary\xffdata"
    assert not os.path.exists(worktree.worktree_path)
    assert git(repo, "rev-parse", worktree.branch_name).stdout.strip() == task_branch
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == main_head
    assert (repo / ".git" / "index").read_bytes() == main_index
    assert git(repo, "status", "--porcelain").stdout == b""


def test_snapshot_excludes_ignored_files_and_venv_symlink(tmp_path):
    repo = make_repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    worktree = manager.create_worktree("filtered")
    wt_path = tmp_path / "worktrees" / "filtered"
    (wt_path / "tracked.txt").write_text("updated\n")
    (wt_path / "ignored.txt").write_text("ignored content\n")
    venv_target = tmp_path / "venv-target"
    venv_target.mkdir()
    (venv_target / "secret").write_text("not archived\n")
    (wt_path / ".venv").symlink_to(venv_target, target_is_directory=True)

    assert manager.cleanup_worktree("filtered", force=True)
    ref = next(ref for ref in archive_refs(repo) if ref.endswith("-uncommitted"))
    assert git(repo, "show", f"{ref}:tracked.txt").stdout == b"updated\n"
    assert git(repo, "cat-file", "-e", f"{ref}:ignored.txt", check=False).returncode != 0
    assert git(repo, "cat-file", "-e", f"{ref}:.venv", check=False).returncode != 0
    assert not os.path.exists(worktree.worktree_path)


def test_clean_worktree_creates_no_archive_ref(tmp_path):
    repo = make_repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    worktree = manager.create_worktree("clean")
    refs_before = archive_refs(repo)

    assert manager.cleanup_worktree("clean", force=True)

    assert archive_refs(repo) == refs_before
    assert not os.path.exists(worktree.worktree_path)
    assert git(
        repo,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{worktree.branch_name}",
        check=False,
    ).returncode != 0


def test_archive_ref_failure_preserves_worktree_and_branch(tmp_path, monkeypatch):
    repo = make_repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    worktree = manager.create_worktree("failure")
    (tmp_path / "worktrees" / "failure" / "tracked.txt").write_text("preserve me\n")
    original_run_git = manager._run_git

    def fail_update_ref(args, *call_args, **call_kwargs):
        if args and args[0] == "update-ref" and args[1].endswith("-uncommitted"):
            raise RuntimeError("injected ref failure")
        return original_run_git(args, *call_args, **call_kwargs)

    monkeypatch.setattr(manager, "_run_git", fail_update_ref)

    assert manager.cleanup_worktree("failure", force=True) is False
    assert os.path.isdir(worktree.worktree_path)
    assert git(
        repo,
        "show-ref",
        "--verify",
        "--quiet",
        f"refs/heads/{worktree.branch_name}",
        check=False,
    ).returncode == 0
    assert archive_refs(repo) == []


def test_multiple_rejections_of_same_task_get_distinct_refs(tmp_path):
    repo = make_repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    worktree = manager.create_worktree("repeat")
    wt_path = tmp_path / "worktrees" / "repeat"
    (wt_path / "tracked.txt").write_text("first rejected version\n")
    index_path = git(wt_path, "rev-parse", "--git-path", "index").stdout.decode().strip()
    if not os.path.isabs(index_path):
        index_path = str(wt_path / index_path)
    worktree_index_before = open(index_path, "rb").read()

    first_ok, first_ref = manager._archive_uncommitted(str(wt_path), "repeat")
    assert open(index_path, "rb").read() == worktree_index_before
    (wt_path / "tracked.txt").write_text("second rejected version\n")
    second_ok, second_ref = manager._archive_uncommitted(str(wt_path), "repeat")
    assert open(index_path, "rb").read() == worktree_index_before

    assert first_ok and second_ok
    assert first_ref is not None and second_ref is not None and first_ref != second_ref
    assert git(repo, "show", f"{first_ref}:tracked.txt").stdout == b"first rejected version\n"
    assert git(repo, "show", f"{second_ref}:tracked.txt").stdout == b"second rejected version\n"
    assert git(worktree.worktree_path, "rev-parse", "HEAD").stdout.decode().strip() == worktree.base_commit
    assert git(repo, "rev-parse", worktree.branch_name).stdout.decode().strip() == worktree.base_commit


def test_rejected_worker_gate_changes_are_archived(tmp_path):
    repo = make_repo(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    pipeline = IntegrationPipeline(manager)
    integration = pipeline.start_integration("rejected-run")
    worktree = manager.create_worktree("rejected-worker", base_ref=integration.branch_name)
    worker_file = tmp_path / "worktrees" / "rejected-worker" / "tracked.txt"
    worker_file.write_text("gate-rejected content\n")
    pipeline._run_gate = lambda *_args, **_kwargs: SimpleNamespace(  # type: ignore[method-assign]
        passed=False,
        infrastructure_error=False,
        output="intentional gate failure",
    )

    prepared = pipeline.prepare_subtask(worktree, target_files=["tracked.txt"])
    assert prepared.early is not None
    assert prepared.early[0] is False
    assert "intentional gate failure" in prepared.early[1]

    assert manager.cleanup_worktree("rejected-worker", force=True)
    ref = next(ref for ref in archive_refs(repo) if ref.endswith("-uncommitted"))
    assert git(repo, "show", f"{ref}:tracked.txt").stdout == b"gate-rejected content\n"
    pipeline.abort_integration()
