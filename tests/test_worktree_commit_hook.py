import os
import stat
import subprocess

import pytest

from meister.worktree import WorktreeManager


def git(cwd, *args, check=True):
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=check,
        capture_output=True,
        text=True,
    )


def make_repo_with_blocking_hook(path):
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "tracked.txt").write_text("original\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "initial", "--no-verify")

    hook = path / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return path


@pytest.mark.skipif(os.name == "nt", reason="hook script is a POSIX shell script")
def test_commit_worktree_bypasses_pre_commit_hook(tmp_path):
    repo = make_repo_with_blocking_hook(tmp_path / "repo")
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    info = manager.create_worktree("hooked")
    (tmp_path / "worktrees" / "hooked" / "new.txt").write_text("new\n")

    sha = manager.commit_worktree(info.worktree_path, "subtask(hooked): add new file")

    assert sha is not None
    assert git(info.worktree_path, "rev-parse", "HEAD").stdout.strip() == sha
    assert git(info.worktree_path, "log", "-1", "--format=%s").stdout.strip() == (
        "subtask(hooked): add new file"
    )


@pytest.mark.skipif(os.name == "nt", reason="hook script is a POSIX shell script")
def test_plain_git_commit_is_still_blocked_by_pre_commit_hook(tmp_path):
    repo = make_repo_with_blocking_hook(tmp_path / "repo")
    (repo / "manual.txt").write_text("manual\n")
    git(repo, "add", "manual.txt")

    result = git(repo, "commit", "-m", "manual commit", check=False)

    assert result.returncode != 0
    assert "manual commit" not in git(repo, "log", "--format=%s").stdout
