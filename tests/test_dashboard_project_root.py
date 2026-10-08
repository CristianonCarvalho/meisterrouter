from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from meister.dashboard.launcher import ensure_dashboard, resolve_project_root
from meister.dashboard.state import ServerState, format_iso_utc, read_state, write_state


@pytest.fixture
def git_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(tmp_path))


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "git",
            "-c",
            "user.name=t",
            "-c",
            "user.email=t@t",
            "-C",
            str(repo),
            *args,
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )


def _create_repo(repo: Path) -> None:
    repo.mkdir()
    _git(repo, "init")
    (repo / "initial.txt").write_text("initial\n", encoding="utf-8")
    _git(repo, "add", "initial.txt")
    _git(repo, "commit", "-m", "initial")


def test_resolve_project_root_from_repository_subdirectory(
    git_environment: None,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    _create_repo(repo)
    nested = repo / "src" / "nested"
    nested.mkdir(parents=True)

    assert resolve_project_root(nested) == repo.resolve()


def test_resolve_project_root_from_worktree_returns_main_repository(
    git_environment: None,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "second-worktree"
    _create_repo(repo)
    _git(repo, "worktree", "add", "-b", "second", str(worktree))
    nested = worktree / "src"
    nested.mkdir()

    assert resolve_project_root(nested) == repo.resolve()


def test_resolve_project_root_uses_ancestor_meister_directory(
    git_environment: None,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    (project / ".meister").mkdir()

    assert resolve_project_root(nested) == project.resolve()


def test_resolve_project_root_uses_ancestor_config_file(
    git_environment: None,
    tmp_path: Path,
) -> None:
    project = tmp_path / "project"
    nested = project / "a" / "b"
    nested.mkdir(parents=True)
    (project / "meister.config.yaml").write_text("dashboard: {}\n", encoding="utf-8")

    assert resolve_project_root(nested) == project.resolve()


def test_resolve_project_root_without_markers_returns_path(
    git_environment: None,
    tmp_path: Path,
) -> None:
    nested = tmp_path / "plain" / "a"
    nested.mkdir(parents=True)

    assert resolve_project_root(nested) == nested.resolve()


@pytest.mark.parametrize("marker", [".meister", "meister.config.yaml", None])
def test_resolve_project_root_falls_back_when_git_is_unavailable(
    git_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    marker: str | None,
) -> None:
    project = tmp_path / "project"
    nested = project / "nested"
    nested.mkdir(parents=True)
    if marker == ".meister":
        (project / marker).mkdir()
    elif marker is not None:
        (project / marker).write_text("dashboard: {}\n", encoding="utf-8")
    monkeypatch.setenv("PATH", "")

    expected = project if marker is not None else nested
    assert resolve_project_root(nested) == expected.resolve()


def test_ensure_dashboard_shares_state_between_worktrees(
    git_environment: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    worktree = tmp_path / "second-worktree"
    _create_repo(repo)
    _git(repo, "worktree", "add", "-b", "second", str(worktree))
    main_subdir = repo / "src"
    worktree_subdir = worktree / "src"
    main_subdir.mkdir()
    worktree_subdir.mkdir()

    monkeypatch.setenv("SSH_CLIENT", "127.0.0.1 50000 22")
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    def fake_urlopen(request: Any, timeout: float = 1.0) -> MagicMock:
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps({"project": {"name": "test"}}).encode("utf-8")
        response.__enter__.return_value = response
        return response

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    real_popen = subprocess.Popen
    server_cwds: list[Path] = []

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        command = args[0] if args else kwargs.get("args")
        if isinstance(command, (list, tuple)) and command and command[0] == "git":
            return real_popen(*args, **kwargs)
        if "dashboard" in command:
            cwd = Path(kwargs["cwd"])
            server_cwds.append(cwd)
            timestamp = format_iso_utc(datetime.now(timezone.utc))
            write_state(
                cwd,
                ServerState(
                    pid=12345,
                    port=5050,
                    url="http://127.0.0.1:5050",
                    started_at=timestamp,
                    last_seen=timestamp,
                ),
            )
        return MagicMock()

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    first = ensure_dashboard(main_subdir)
    second = ensure_dashboard(worktree_subdir)

    assert first.url == "http://127.0.0.1:5050/timeline"
    assert second.url == first.url
    assert server_cwds == [repo.resolve()]
    assert read_state(repo) is not None
    assert not (worktree / ".meister" / "dashboard.json").exists()
