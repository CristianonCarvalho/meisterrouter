import os
import subprocess
import pytest

from meister.worktree import WorktreeManager, WorktreeInfo


@pytest.fixture
def git_repo(tmp_path):
    """Fixture that initializes a clean git repository in tmp_path."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    cwd = str(repo_dir)

    subprocess.run(["git", "init"], cwd=cwd, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@meisterrouter.local"], cwd=cwd, check=True)
    subprocess.run(["git", "config", "user.name", "Meister CI"], cwd=cwd, check=True)

    # Initial commit
    readme = repo_dir / "README.md"
    readme.write_text("# Project\n")
    app_file = repo_dir / "app.py"
    app_file.write_text("def run():\n    pass\n")

    subprocess.run(["git", "add", "."], cwd=cwd, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=cwd, check=True)

    return repo_dir


def test_worktree_create_and_cleanup_lifecycle(git_repo):
    repo_path = str(git_repo)
    manager = WorktreeManager(repo_root=repo_path)

    # 1. Create worktree
    info = manager.create_worktree("task-alpha")
    assert isinstance(info, WorktreeInfo)
    assert info.task_id == "task-alpha"
    assert info.branch_name == "meister/worktree/task-alpha"
    assert os.path.exists(info.worktree_path)

    # Metadata persisted
    active = manager.list_active_worktrees()
    assert len(active) == 1
    assert active[0].task_id == "task-alpha"

    # 2. Modify files in worktree
    wt_app = os.path.join(info.worktree_path, "app.py")
    with open(wt_app, "w", encoding="utf-8") as f:
        f.write("def run():\n    print('updated')\n")

    # Add an untracked file
    wt_helper = os.path.join(info.worktree_path, "helper.py")
    with open(wt_helper, "w", encoding="utf-8") as f:
        f.write("def help(): pass\n")

    # 3. Deterministic modified files check (Achado #21)
    mods = manager.get_modified_files(info.worktree_path, base_ref=info.base_commit)
    assert "app.py" in mods
    assert "helper.py" in mods

    # 4. Scope verification (Achado #2)
    # Target files allowed
    ok, out_of_scope = manager.verify_scope(info.worktree_path, target_files=["app.py", "helper.py"], base_ref=info.base_commit)
    assert ok is True
    assert out_of_scope == []

    # Target files violated
    ok_bad, out_of_scope_bad = manager.verify_scope(info.worktree_path, target_files=["app.py"], base_ref=info.base_commit)
    assert ok_bad is False
    assert "helper.py" in out_of_scope_bad

    # 5. Cleanup after merge
    success = manager.cleanup_worktree("task-alpha", delete_branch=True, force=True)
    assert success is True
    assert not os.path.exists(info.worktree_path)
    assert len(manager.list_active_worktrees()) == 0

    # Verify branch was deleted
    res = subprocess.run(["git", "branch", "--list", "meister/worktree/task-alpha"], cwd=repo_path, capture_output=True, text=True)
    assert "meister/worktree/task-alpha" not in res.stdout


def test_worktree_cleanup_on_failure_or_crash(git_repo):
    repo_path = str(git_repo)
    manager = WorktreeManager(repo_root=repo_path)

    info = manager.create_worktree("task-failing")
    assert os.path.exists(info.worktree_path)

    # Worker dirtied worktree with uncommitted changes
    dirty_file = os.path.join(info.worktree_path, "dirty.txt")
    with open(dirty_file, "w") as f:
        f.write("incomplete work")

    # Cleanup with force removes uncommitted changes cleanly
    success = manager.cleanup_worktree("task-failing", delete_branch=True, force=True)
    assert success is True
    assert not os.path.exists(info.worktree_path)


def test_worktree_cleanup_orphans_on_startup(git_repo):
    repo_path = str(git_repo)
    manager = WorktreeManager(repo_root=repo_path)

    # Create worktree
    info = manager.create_worktree("orphan-task")
    assert os.path.exists(info.worktree_path)

    # Simulate crash: overwrite metadata file with a dead PID (e.g. 99999999)
    meta_file = os.path.join(manager.metadata_dir, "orphan-task.json")
    with open(meta_file, "w", encoding="utf-8") as f:
        f.write('{"task_id": "orphan-task", "worktree_path": "' + info.worktree_path + '", "branch_name": "' + info.branch_name + '", "base_ref": "HEAD", "base_commit": "' + info.base_commit + '", "created_at": 0.0, "pid": 99999999, "status": "active"}')

    # Also simulate an orphan branch left without a worktree
    subprocess.run(["git", "branch", "meister/worktree/leftover-branch"], cwd=repo_path, check=True)

    cleaned = manager.cleanup_orphans()
    assert "orphan-task" in cleaned
    assert "leftover-branch" in cleaned
    assert not os.path.exists(info.worktree_path)


def test_worktree_isolated_outside_repo_root(git_repo, tmp_path, monkeypatch):
    repo_path = str(git_repo)
    custom_worktrees_base = tmp_path / "global_worktrees"
    monkeypatch.setenv("MEISTER_WORKTREES_DIR", str(custom_worktrees_base))

    # Initialize manager with default behavior using the env
    manager = WorktreeManager(repo_root=repo_path)

    # 1. Create worktree
    info = manager.create_worktree("isolated-task")
    assert os.path.exists(info.worktree_path)

    # 2. Verify worktree is strictly NOT inside repo_root
    real_repo = os.path.realpath(repo_path)
    real_wt = os.path.realpath(info.worktree_path)
    assert not real_wt.startswith(real_repo), f"Worktree {real_wt} must not be inside repo {real_repo}"

    # Verify that a worker doing relative traversal cannot hit repo_root
    from pathlib import Path
    with pytest.raises(ValueError):
        Path(real_wt).relative_to(Path(real_repo))

    # 3. Modify files and commit in isolated worktree
    app_wt = os.path.join(info.worktree_path, "app.py")
    with open(app_wt, "w", encoding="utf-8") as f:
        f.write("def run():\n    return 'isolated'\n")

    sha = manager.commit_worktree(info.worktree_path, "feat: isolated work")
    assert sha is not None

    # 4. Cleanup worktree
    success = manager.cleanup_worktree("isolated-task", delete_branch=True, force=True)
    assert success is True
    assert not os.path.exists(info.worktree_path)

    # 5. Verify orphan cleanup works outside repo
    info_orphan = manager.create_worktree("orphan-outside")
    meta_file = os.path.join(manager.metadata_dir, "orphan-outside.json")
    with open(meta_file, "w", encoding="utf-8") as f:
        f.write('{"task_id": "orphan-outside", "worktree_path": "' + info_orphan.worktree_path + '", "branch_name": "' + info_orphan.branch_name + '", "base_ref": "HEAD", "base_commit": "' + info_orphan.base_commit + '", "created_at": 0.0, "pid": 99999999, "status": "active"}')

    cleaned = manager.cleanup_orphans()
    assert "orphan-outside" in cleaned
    assert not os.path.exists(info_orphan.worktree_path)


def test_orphan_cleanup_archives_unmerged_commits(git_repo):
    repo_path = str(git_repo)
    manager = WorktreeManager(repo_root=repo_path)

    # 1. Orphan branch WITH unmerged commit
    info_work = manager.create_worktree("orphan-with-commits")
    work_file = os.path.join(info_work.worktree_path, "important.py")
    with open(work_file, "w", encoding="utf-8") as f:
        f.write("# valuable unmerged work\n")
    sha_work = manager.commit_worktree(info_work.worktree_path, "feat: important orphan work")
    assert sha_work is not None

    # Simulate crash: set dead PID
    meta_file = os.path.join(manager.metadata_dir, "orphan-with-commits.json")
    with open(meta_file, "w", encoding="utf-8") as f:
        f.write('{"task_id": "orphan-with-commits", "worktree_path": "' + info_work.worktree_path + '", "branch_name": "' + info_work.branch_name + '", "base_ref": "HEAD", "base_commit": "' + info_work.base_commit + '", "created_at": 0.0, "pid": 99999999, "status": "active"}')

    # 2. Orphan branch WITHOUT unmerged commit (clean branch pointing to HEAD)
    subprocess.run(["git", "branch", "meister/worktree/orphan-without-commits", "HEAD"], cwd=repo_path, check=True)

    # 3. Run orphan cleanup
    cleaned = manager.cleanup_orphans()
    assert "orphan-with-commits" in cleaned
    assert "orphan-without-commits" in cleaned

    # 4. Check git refs in refs/meister/archive/*
    refs_out = subprocess.run(
        ["git", "for-each-ref", "--format=%(refname) %(objectname)", "refs/meister/archive"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout

    # The orphan branch with commits must have an archive ref pointing to sha_work
    assert "refs/meister/archive/orphan-with-commits-" in refs_out
    matching_lines = [line for line in refs_out.splitlines() if "orphan-with-commits" in line]
    assert len(matching_lines) == 1
    archived_ref, archived_sha = matching_lines[0].split()
    assert archived_sha == sha_work

    # The orphan branch without commits must NOT produce an archive ref
    assert "orphan-without-commits" not in refs_out


def test_orphan_cleanup_failsafe_when_archive_fails(git_repo, monkeypatch):
    repo_path = str(git_repo)
    manager = WorktreeManager(repo_root=repo_path)

    # 1. Create worktree with unmerged commit
    info = manager.create_worktree("orphan-fail-archive")
    work_file = os.path.join(info.worktree_path, "important_code.py")
    with open(work_file, "w", encoding="utf-8") as f:
        f.write("# critical unmerged changes\n")
    sha = manager.commit_worktree(info.worktree_path, "feat: critical work")
    assert sha is not None

    # Simulate dead process / orphan metadata
    meta_file = os.path.join(manager.metadata_dir, "orphan-fail-archive.json")
    with open(meta_file, "w", encoding="utf-8") as f:
        f.write('{"task_id": "orphan-fail-archive", "worktree_path": "' + info.worktree_path + '", "branch_name": "' + info.branch_name + '", "base_ref": "HEAD", "base_commit": "' + info.base_commit + '", "created_at": 0.0, "pid": 99999999, "status": "active"}')

    # Force failure on update-ref via monkeypatch on _run_git
    orig_run_git = manager._run_git
    def mock_run_git(cmd, **kwargs):
        if len(cmd) > 0 and cmd[0] == "update-ref":
            raise RuntimeError("Simulated failure creating archive ref")
        return orig_run_git(cmd, **kwargs)

    monkeypatch.setattr(manager, "_run_git", mock_run_git)

    # 2. Run cleanup_orphans: archive fails, so branch deletion MUST be blocked
    cleaned = manager.cleanup_orphans()
    assert "orphan-fail-archive" not in cleaned

    # 3. Branch MUST still exist in git to prevent data loss
    branches = subprocess.run(
        ["git", "branch", "--list", "meister/worktree/orphan-fail-archive"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert "meister/worktree/orphan-fail-archive" in branches


