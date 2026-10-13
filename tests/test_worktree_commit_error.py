import stat
import subprocess

import pytest

from meister import worktree
from meister.worktree import WorktreeCommitError, WorktreeManager

HOOK_PHRASE = "hook rejeitou o commit de teste"


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_commit_failure_message_includes_hook_output(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "dev@example.com")
    _git(repo, "config", "user.name", "Dev")
    (repo / "README.md").write_text("base\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "base")

    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{HOOK_PHRASE}'\nexit 1\n", encoding="utf-8")
    hook.chmod(hook.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    (repo / "feature.txt").write_text("change\n", encoding="utf-8")

    # Remove --no-verify so the repository's pre-commit hook actually runs.
    monkeypatch.setattr(worktree, "INTERNAL_COMMIT_ARGS", [])

    manager = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "wt"))
    with pytest.raises(WorktreeCommitError) as excinfo:
        manager.commit_worktree(str(repo), "subtask(x): change")

    err = excinfo.value
    assert isinstance(err, RuntimeError)
    assert err.returncode == 1
    assert HOOK_PHRASE in str(err)
    assert HOOK_PHRASE in err.message
    assert HOOK_PHRASE in err.stderr
    assert "exit code 1" in str(err)
