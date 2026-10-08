from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from meister.config import DashboardConfig, DashboardWindowConfig
import meister.dashboard.launcher as launcher_mod
from meister.dashboard.launcher import Outcome, ensure_dashboard
from meister.dashboard.state import ServerState, format_iso_utc, read_state, write_state

_REAL_POPEN = subprocess.Popen


def _patch_popen(monkeypatch: pytest.MonkeyPatch, handler: Callable[..., Any]) -> None:
    def wrapper(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, (list, tuple)) and cmd and cmd[0] == "git":
            return _REAL_POPEN(*args, **kwargs)
        return handler(*args, **kwargs)

    monkeypatch.setattr(subprocess, "Popen", wrapper)


def _mock_meta_response(payload: dict[str, Any] | None = None) -> MagicMock:
    if payload is None:
        payload = {"project": {"name": "test"}}
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps(payload).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp
    return mock_resp


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    monkeypatch.setenv("HOME", str(home))
    # Remove CI/SSH variables so has_display defaults to True on non-Linux or with DISPLAY
    for var in (
        "CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "BUILD_NUMBER",
        "SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    return home


def test_ensure_dashboard_open_never_returns_disabled(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    popen_called = False

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        nonlocal popen_called
        popen_called = True
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    cfg = DashboardConfig(open="never")
    outcome = ensure_dashboard(project, cfg)

    assert outcome.kind == "disabled"
    assert outcome.url is None
    assert not popen_called


def test_ensure_dashboard_open_window_false_returns_disabled(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    popen_called = False

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        nonlocal popen_called
        popen_called = True
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    cfg = DashboardConfig(open="auto")
    outcome = ensure_dashboard(project, cfg, open_window=False)

    assert outcome.kind == "disabled"
    assert outcome.url is None
    assert not popen_called


def test_ensure_dashboard_no_display_returns_url_only(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request
    import webbrowser

    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setenv("SSH_CLIENT", "127.0.0.1 50000 22")
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    opened_tabs: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_tabs.append(url))

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        cwd = kwargs.get("cwd") or str(project)
        if "dashboard" in cmd:
            now_str = format_iso_utc(datetime.now(timezone.utc))
            write_state(
                cwd,
                ServerState(
                    pid=12345,
                    port=5050,
                    url="http://127.0.0.1:5050",
                    started_at=now_str,
                    last_seen=now_str,
                ),
            )
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(project, DashboardConfig())

    assert outcome.kind == "url_only"
    assert outcome.url == "http://127.0.0.1:5050/timeline"
    assert not opened_tabs


def test_ensure_dashboard_without_chromium_falls_back_to_tab(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request
    import webbrowser

    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    opened_tabs: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_tabs.append(url))

    browser_popen_calls: list[list[str]] = []

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        cwd = kwargs.get("cwd") or str(project)
        if "dashboard" in cmd:
            now_str = format_iso_utc(datetime.now(timezone.utc))
            write_state(
                cwd,
                ServerState(
                    pid=12345,
                    port=5050,
                    url="http://127.0.0.1:5050",
                    started_at=now_str,
                    last_seen=now_str,
                ),
            )
        else:
            browser_popen_calls.append(cmd)
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(project, DashboardConfig(open="auto"))

    assert outcome.kind == "tab"
    assert outcome.url == "http://127.0.0.1:5050/timeline"
    assert opened_tabs == ["http://127.0.0.1:5050/timeline"]
    assert not browser_popen_calls


def test_ensure_dashboard_server_alive_and_window_alive_opens_nothing(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request
    import webbrowser

    project = tmp_path / "project"
    project.mkdir()

    now = datetime.now(timezone.utc)
    recent = format_iso_utc(now - timedelta(seconds=1))
    write_state(
        project,
        ServerState(
            pid=12345,
            port=5050,
            url="http://127.0.0.1:5050",
            started_at=recent,
            last_seen=recent,
        ),
    )

    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: ["/bin/chromium"])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    opened_tabs: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_tabs.append(url))

    popen_called = False

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        nonlocal popen_called
        popen_called = True
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(project, DashboardConfig(open="auto"))

    assert outcome.kind == "window"
    assert outcome.url == "http://127.0.0.1:5050/timeline"
    assert not popen_called
    assert not opened_tabs


def test_ensure_dashboard_server_alive_and_window_dead_opens_only_window(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request
    import webbrowser

    project = tmp_path / "project"
    project.mkdir()

    now = datetime.now(timezone.utc)
    old = format_iso_utc(now - timedelta(seconds=15))
    write_state(
        project,
        ServerState(
            pid=12345,
            port=5050,
            url="http://127.0.0.1:5050",
            started_at=old,
            last_seen=old,
        ),
    )

    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: ["/bin/chromium"])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    opened_tabs: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_tabs.append(url))

    server_popen_called = False
    browser_calls: list[list[str]] = []

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        nonlocal server_popen_called
        cmd = args[0] if args else kwargs.get("args")
        if "dashboard" in cmd:
            server_popen_called = True
        else:
            browser_calls.append(cmd)
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    cfg = DashboardConfig(
        open="auto",
        window=DashboardWindowConfig(width=1440, height=900),
    )
    outcome = ensure_dashboard(project, cfg)

    assert outcome.kind == "window"
    assert outcome.url == "http://127.0.0.1:5050/timeline"
    assert not server_popen_called
    assert len(browser_calls) == 1
    assert "--app=http://127.0.0.1:5050/timeline" in browser_calls[0]
    assert "--window-size=1440,900" in browser_calls[0]
    assert not opened_tabs


def test_ensure_dashboard_concurrent_calls_spawn_only_one_server(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: ["/bin/chromium"])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    server_spawn_lock = threading.Lock()
    server_spawns = 0

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        nonlocal server_spawns
        cmd = args[0] if args else kwargs.get("args")
        cwd = kwargs.get("cwd") or str(project)
        if "dashboard" in cmd:
            with server_spawn_lock:
                server_spawns += 1
            # Simulate a brief delay during server spin-up
            time.sleep(0.05)
            now_str = format_iso_utc(datetime.now(timezone.utc))
            write_state(
                cwd,
                ServerState(
                    pid=12345,
                    port=5050,
                    url="http://127.0.0.1:5050",
                    started_at=now_str,
                    last_seen=now_str,
                ),
            )
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    results: list[Outcome] = []

    def worker() -> None:
        outcome = ensure_dashboard(project, DashboardConfig())
        results.append(outcome)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    assert results[0].url == "http://127.0.0.1:5050/timeline"
    assert results[1].url == "http://127.0.0.1:5050/timeline"
    assert server_spawns == 1


def test_ensure_dashboard_replaces_dead_pid_state(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    project = tmp_path / "project"
    project.mkdir()

    dead_pid = 9999999
    now = datetime.now(timezone.utc)
    old = format_iso_utc(now - timedelta(seconds=2))
    write_state(
        project,
        ServerState(
            pid=dead_pid,
            port=5001,
            url="http://127.0.0.1:5001",
            started_at=old,
            last_seen=old,
        ),
    )

    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: ["/bin/chromium"])

    def fake_kill(pid: int, sig: int) -> None:
        if pid == dead_pid:
            raise ProcessLookupError
        return None

    monkeypatch.setattr(os, "kill", fake_kill)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        cwd = kwargs.get("cwd") or str(project)
        if "dashboard" in cmd:
            now_str = format_iso_utc(datetime.now(timezone.utc))
            write_state(
                cwd,
                ServerState(
                    pid=22222,
                    port=5002,
                    url="http://127.0.0.1:5002",
                    started_at=now_str,
                    last_seen=now_str,
                ),
            )
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(project, DashboardConfig())

    assert outcome.kind == "window"
    assert outcome.url == "http://127.0.0.1:5002/timeline"

    current_state = read_state(project)
    assert current_state is not None
    assert current_state.pid == 22222
    assert current_state.port == 5002


def test_ensure_dashboard_two_projects_independent_states_and_urls(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    proj_a = tmp_path / "proj_a"
    proj_b = tmp_path / "proj_b"
    proj_a.mkdir()
    proj_b.mkdir()

    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: ["/bin/chromium"])
    monkeypatch.setattr(os, "kill", lambda pid, sig: None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        cwd = kwargs.get("cwd") or ""
        if "dashboard" in cmd:
            now_str = format_iso_utc(datetime.now(timezone.utc))
            port = 5001 if "proj_a" in str(cwd) else 5002
            pid = 11111 if "proj_a" in str(cwd) else 22222
            write_state(
                cwd,
                ServerState(
                    pid=pid,
                    port=port,
                    url=f"http://127.0.0.1:{port}",
                    started_at=now_str,
                    last_seen=now_str,
                ),
            )
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome_a = ensure_dashboard(proj_a, DashboardConfig())
    outcome_b = ensure_dashboard(proj_b, DashboardConfig())

    assert outcome_a.url == "http://127.0.0.1:5001/timeline"
    assert outcome_b.url == "http://127.0.0.1:5002/timeline"
    assert outcome_a.url != outcome_b.url

    state_a = read_state(proj_a)
    state_b = read_state(proj_b)
    assert state_a is not None and state_a.port == 5001
    assert state_b is not None and state_b.port == 5002


def test_ensure_dashboard_popen_exception_returns_failed_without_propagating(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        raise OSError("Permission denied")

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(project, DashboardConfig())

    assert outcome.kind == "failed"
    assert outcome.url is None


def test_ensure_dashboard_respects_two_second_timeout_when_state_never_appears(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        # Does not write any state
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    t0 = time.monotonic()
    outcome = ensure_dashboard(project, DashboardConfig())
    elapsed = time.monotonic() - t0

    assert outcome.kind == "failed"
    assert outcome.url is None
    assert 1.8 <= elapsed <= 2.5
