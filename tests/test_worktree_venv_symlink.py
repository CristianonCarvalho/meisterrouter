import os
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


def make_repo(path, gitignore_line):
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "Test")
    git(path, "config", "user.email", "test@example.invalid")
    (path / "tracked.txt").write_text("original\n")
    if gitignore_line is not None:
        (path / ".gitignore").write_text(f"{gitignore_line}\n")
    git(path, "add", ".")
    git(path, "commit", "-m", "initial", "--no-verify")
    return path


def add_venv(worktree, tmp_path, kind):
    if kind == "symlink":
        external = tmp_path / "external-venv"
        (external / "bin").mkdir(parents=True)
        (external / "bin" / "python").write_text("#!/bin/sh\n")
        os.symlink(external, worktree / ".venv")
    else:
        (worktree / ".venv" / "bin").mkdir(parents=True)
        (worktree / ".venv" / "bin" / "python").write_text("#!/bin/sh\n")


@pytest.mark.parametrize("gitignore_line", [".venv/", ".venv", None])
@pytest.mark.parametrize("kind", ["symlink", "folder"])
def test_venv_is_not_in_modified_files_nor_commit(tmp_path, gitignore_line, kind):
    repo = make_repo(tmp_path / "repo", gitignore_line)
    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    info = manager.create_worktree("venv-task")
    worktree = tmp_path / "worktrees" / "venv-task"

    add_venv(worktree, tmp_path, kind)
    (worktree / "new.txt").write_text("new\n")

    assert manager.get_modified_files(info.worktree_path, base_ref=info.base_commit) == ["new.txt"]

    sha = manager.commit_worktree(info.worktree_path, "subtask(venv-task): add new file")

    assert sha is not None
    files = git(info.worktree_path, "ls-tree", "-r", "--name-only", sha).stdout.split()
    assert "new.txt" in files
    assert not any(name == ".venv" or name.startswith(".venv/") for name in files)
