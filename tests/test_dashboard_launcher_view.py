from __future__ import annotations

import json
import subprocess
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from unittest.mock import MagicMock

import pytest

from meister import osops
from meister.config import DashboardConfig
import meister.dashboard.launcher as launcher_mod
from meister.dashboard.launcher import (
    _format_timeline_url,
    _format_view_url,
    ensure_dashboard,
)
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
    for var in (
        "CI", "GITHUB_ACTIONS", "GITLAB_CI", "JENKINS_URL", "BUILD_NUMBER",
        "SSH_CLIENT", "SSH_TTY", "SSH_CONNECTION",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("DISPLAY", ":0")
    return home


def test_format_view_url_and_timeline_url() -> None:
    assert _format_view_url("http://127.0.0.1:5050", "timeline") == "http://127.0.0.1:5050/timeline"
    assert _format_view_url("http://127.0.0.1:5050/", "timeline") == "http://127.0.0.1:5050/timeline"
    assert _format_view_url("http://127.0.0.1:5050/timeline", "timeline") == "http://127.0.0.1:5050/timeline"

    assert _format_view_url("http://127.0.0.1:5050", "config") == "http://127.0.0.1:5050/config"
    assert _format_view_url("http://127.0.0.1:5050/", "/config") == "http://127.0.0.1:5050/config"
    assert _format_view_url("http://127.0.0.1:5050/config", "config") == "http://127.0.0.1:5050/config"

    assert _format_timeline_url("http://127.0.0.1:5050") == "http://127.0.0.1:5050/timeline"
    assert _format_timeline_url("http://127.0.0.1:5050/timeline") == "http://127.0.0.1:5050/timeline"


def test_ensure_dashboard_view_config_url(
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
    monkeypatch.setattr(osops, "pid_alive", lambda pid: True)

    opened_tabs: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_tabs.append(url))

    browser_calls: list[list[str]] = []

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        browser_calls.append(cmd)
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(project, DashboardConfig(open="auto"), view="config")

    assert outcome.kind == "window"
    assert outcome.url == "http://127.0.0.1:5050/config"
    assert outcome.fresh is False
    assert len(browser_calls) == 1
    assert "--app=http://127.0.0.1:5050/config" in browser_calls[0]
    assert not opened_tabs


def test_ensure_dashboard_view_config_fallback_to_tab_without_chromium(
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

    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: None)
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())
    monkeypatch.setattr(osops, "pid_alive", lambda pid: True)

    opened_tabs: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_tabs.append(url))

    browser_calls: list[list[str]] = []

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        browser_calls.append(cmd)
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(project, DashboardConfig(open="auto"), view="config")

    assert outcome.kind == "tab"
    assert outcome.url == "http://127.0.0.1:5050/config"
    assert outcome.fresh is False
    assert not browser_calls
    assert opened_tabs == ["http://127.0.0.1:5050/config"]


def test_ensure_dashboard_window_already_open_still_opens_view_config(
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
    monkeypatch.setattr(osops, "pid_alive", lambda pid: True)

    opened_tabs: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_tabs.append(url))

    browser_calls: list[list[str]] = []

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        browser_calls.append(cmd)
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    # For timeline, window_alive is True and nothing is opened
    outcome_tl = ensure_dashboard(project, DashboardConfig(open="auto"), view="timeline")
    assert outcome_tl.kind == "window"
    assert outcome_tl.url == "http://127.0.0.1:5050/timeline"
    assert len(browser_calls) == 0

    # For view="config", the window is opened even though window_alive is True
    outcome_cfg = ensure_dashboard(project, DashboardConfig(open="auto"), view="config")
    assert outcome_cfg.kind == "window"
    assert outcome_cfg.url == "http://127.0.0.1:5050/config"
    assert outcome_cfg.fresh is False
    assert len(browser_calls) == 1
    assert "--app=http://127.0.0.1:5050/config" in browser_calls[0]
    assert not opened_tabs


def test_ensure_dashboard_different_code_starts_new_server_and_marks_fresh(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(launcher_mod, "current_code_id", lambda: "0.11.0+commit2")

    now = datetime.now(timezone.utc)
    recent = format_iso_utc(now - timedelta(seconds=1))
    write_state(
        project,
        ServerState(
            pid=11111,
            port=5001,
            url="http://127.0.0.1:5001",
            started_at=recent,
            last_seen=recent,
            code_id="0.10.0+commit1",
        ),
    )

    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: ["/bin/chromium"])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())
    monkeypatch.setattr(osops, "pid_alive", lambda pid: True)

    server_spawned = False
    browser_calls: list[list[str]] = []

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        nonlocal server_spawned
        cmd = args[0] if args else kwargs.get("args")
        cwd = kwargs.get("cwd") or str(project)
        if "dashboard" in cmd:
            server_spawned = True
            now_str = format_iso_utc(datetime.now(timezone.utc))
            write_state(
                cwd,
                ServerState(
                    pid=22222,
                    port=5002,
                    url="http://127.0.0.1:5002",
                    started_at=now_str,
                    last_seen=now_str,
                    code_id="0.11.0+commit2",
                ),
            )
        else:
            browser_calls.append(cmd)
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(
        project,
        DashboardConfig(open="auto"),
        view="config",
        require_current_code=True,
    )

    assert server_spawned is True
    assert outcome.kind == "window"
    assert outcome.url == "http://127.0.0.1:5002/config"
    assert outcome.fresh is True

    # State file on disk now points to the new server
    st = read_state(project)
    assert st is not None
    assert st.pid == 22222
    assert st.port == 5002
    assert st.code_id == "0.11.0+commit2"


def test_ensure_dashboard_equal_code_is_reused(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(launcher_mod, "current_code_id", lambda: "0.11.0+commit1")

    now = datetime.now(timezone.utc)
    recent = format_iso_utc(now - timedelta(seconds=1))
    write_state(
        project,
        ServerState(
            pid=11111,
            port=5001,
            url="http://127.0.0.1:5001",
            started_at=recent,
            last_seen=recent,
            code_id="0.11.0+commit1",
        ),
    )

    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: ["/bin/chromium"])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())
    monkeypatch.setattr(osops, "pid_alive", lambda pid: True)

    server_spawned = False

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        nonlocal server_spawned
        cmd = args[0] if args else kwargs.get("args")
        if "dashboard" in cmd:
            server_spawned = True
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(
        project,
        DashboardConfig(open="auto"),
        view="config",
        require_current_code=True,
    )

    assert server_spawned is False
    assert outcome.kind == "window"
    assert outcome.url == "http://127.0.0.1:5001/config"
    assert outcome.fresh is False


def test_ensure_dashboard_old_state_without_code_id_counts_as_different(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(launcher_mod, "current_code_id", lambda: "0.11.0+commit1")

    now = datetime.now(timezone.utc)
    recent = format_iso_utc(now - timedelta(seconds=1))
    write_state(
        project,
        ServerState(
            pid=11111,
            port=5001,
            url="http://127.0.0.1:5001",
            started_at=recent,
            last_seen=recent,
            code_id="",
        ),
    )

    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: ["/bin/chromium"])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())
    monkeypatch.setattr(osops, "pid_alive", lambda pid: True)

    server_spawned = False

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        nonlocal server_spawned
        cmd = args[0] if args else kwargs.get("args")
        cwd = kwargs.get("cwd") or str(project)
        if "dashboard" in cmd:
            server_spawned = True
            now_str = format_iso_utc(datetime.now(timezone.utc))
            write_state(
                cwd,
                ServerState(
                    pid=22222,
                    port=5002,
                    url="http://127.0.0.1:5002",
                    started_at=now_str,
                    last_seen=now_str,
                    code_id="0.11.0+commit1",
                ),
            )
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(
        project,
        DashboardConfig(open="auto"),
        view="config",
        require_current_code=True,
    )

    assert server_spawned is True
    assert outcome.url == "http://127.0.0.1:5002/config"
    assert outcome.fresh is True


def test_ensure_dashboard_url_only_does_not_open_browser(
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
    monkeypatch.setattr(osops, "pid_alive", lambda pid: True)

    opened_tabs: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_tabs.append(url))

    browser_calls: list[list[str]] = []

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        browser_calls.append(cmd)
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(
        project,
        DashboardConfig(open="auto"),
        view="config",
        url_only=True,
    )

    assert outcome.kind == "url_only"
    assert outcome.url == "http://127.0.0.1:5050/config"
    assert outcome.fresh is False
    assert not browser_calls
    assert not opened_tabs


def test_ensure_dashboard_open_never_with_view_config_returns_url_only(
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
    monkeypatch.setattr(osops, "pid_alive", lambda pid: True)

    opened_tabs: list[str] = []
    monkeypatch.setattr(webbrowser, "open", lambda url: opened_tabs.append(url))

    browser_calls: list[list[str]] = []

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        browser_calls.append(cmd)
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(
        project,
        DashboardConfig(open="never"),
        view="config",
    )

    assert outcome.kind == "url_only"
    assert outcome.url == "http://127.0.0.1:5050/config"
    assert outcome.fresh is False
    assert not browser_calls
    assert not opened_tabs


def test_ensure_dashboard_open_never_with_view_timeline_returns_disabled(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = tmp_path / "project"
    project.mkdir()

    outcome = ensure_dashboard(
        project,
        DashboardConfig(open="never"),
        view="timeline",
    )

    assert outcome.kind == "disabled"
    assert outcome.url is None


def test_ensure_dashboard_does_not_confuse_old_state_while_waiting_for_new_server(
    clean_env: Path,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import urllib.request

    project = tmp_path / "project"
    project.mkdir()

    monkeypatch.setattr(launcher_mod, "current_code_id", lambda: "0.11.0+commit2")
    monkeypatch.setattr(launcher_mod, "find_chromium", lambda platform, env: ["/bin/chromium"])
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: _mock_meta_response())
    monkeypatch.setattr(osops, "pid_alive", lambda pid: True)

    now = datetime.now(timezone.utc)
    recent = format_iso_utc(now - timedelta(seconds=1))
    write_state(
        project,
        ServerState(
            pid=11111,
            port=5001,
            url="http://127.0.0.1:5001",
            started_at=recent,
            last_seen=recent,
            code_id="0.10.0+old",
        ),
    )

    def fake_popen(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        cwd = kwargs.get("cwd") or str(project)
        if "dashboard" in cmd:
            # Simulate a delayed write of the new server's state in a thread
            def delayed_write() -> None:
                time.sleep(0.06)
                now_str = format_iso_utc(datetime.now(timezone.utc))
                write_state(
                    cwd,
                    ServerState(
                        pid=22222,
                        port=5002,
                        url="http://127.0.0.1:5002",
                        started_at=now_str,
                        last_seen=now_str,
                        code_id="0.11.0+commit2",
                    ),
                )

            threading.Thread(target=delayed_write).start()
        return MagicMock()

    _patch_popen(monkeypatch, fake_popen)

    outcome = ensure_dashboard(
        project,
        DashboardConfig(open="auto"),
        view="config",
        require_current_code=True,
    )

    assert outcome.kind == "window"
    assert outcome.url == "http://127.0.0.1:5002/config"
    assert outcome.fresh is True
