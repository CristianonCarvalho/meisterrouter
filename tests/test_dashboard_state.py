from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from meister.dashboard.state import (
    ServerState,
    is_alive,
    read_state,
    remove_state,
    write_state,
)


def test_read_state_missing_file_returns_none(tmp_path: Path):
    project = tmp_path / "project"
    project.mkdir()
    assert read_state(project) is None


@pytest.mark.parametrize(
    "bad_content",
    [
        "",  # empty
        "{invalid json",  # malformed
        "[]",  # json list instead of dict
        json.dumps({"pid": 123}),  # missing fields
        json.dumps({"pid": "not-an-int", "port": 5050, "url": "http://127.0.0.1:5050", "started_at": "x", "last_seen": "y"}),
    ],
)
def test_read_state_corrupted_file_returns_none(tmp_path: Path, bad_content: str):
    project = tmp_path / "project"
    meister_dir = project / ".meister"
    meister_dir.mkdir(parents=True)
    (meister_dir / "dashboard.json").write_text(bad_content, encoding="utf-8")
    assert read_state(project) is None


def test_write_state_and_read_state_roundtrip(tmp_path: Path):
    project = tmp_path / "project"
    state = ServerState(
        pid=12345,
        port=5050,
        url="http://127.0.0.1:5050",
        started_at="2026-10-08T08:00:00Z",
        last_seen="2026-10-08T08:05:00Z",
    )
    write_state(project, state)
    loaded = read_state(project)
    assert loaded is not None
    assert loaded.pid == 12345
    assert loaded.port == 5050
    assert loaded.url == "http://127.0.0.1:5050"
    assert loaded.started_at == "2026-10-08T08:00:00Z"
    assert loaded.last_seen == "2026-10-08T08:05:00Z"


def test_write_state_is_atomic(tmp_path: Path, monkeypatch):
    project = tmp_path / "project"
    state = ServerState(
        pid=100,
        port=5050,
        url="http://127.0.0.1:5050",
        started_at="2026-10-08T08:00:00Z",
        last_seen="2026-10-08T08:00:00Z",
    )
    replaces: list[tuple[str, str]] = []
    real_replace = os.replace

    def tracked_replace(src: str, dst: str):
        replaces.append((str(src), str(dst)))
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", tracked_replace)
    write_state(project, state)

    assert len(replaces) == 1
    src, dst = replaces[0]
    assert dst == str(project / ".meister" / "dashboard.json")
    # Verify temp file was placed in the same directory for atomic cross-device replace
    assert str(project / ".meister") in src
    assert read_state(project) == state


def test_is_alive_false_for_stale_last_seen():
    now = datetime(2026, 10, 8, 12, 0, 10, tzinfo=timezone.utc)
    old_last_seen = (now - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
    state = ServerState(
        pid=os.getpid(),
        port=5050,
        url="http://127.0.0.1:5050",
        started_at="2026-10-08T11:00:00Z",
        last_seen=old_last_seen,
    )
    assert not is_alive(state, now=now, max_age_s=5.0)


def test_is_alive_false_for_dead_pid(monkeypatch):
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    state = ServerState(
        pid=99999999,
        port=5050,
        url="http://127.0.0.1:5050",
        started_at="2026-10-08T11:00:00Z",
        last_seen=recent,
    )

    def fake_kill(pid, sig):
        raise ProcessLookupError

    monkeypatch.setattr(os, "kill", fake_kill)
    assert not is_alive(state, now=now, max_age_s=5.0)


def test_is_alive_false_when_meta_endpoint_fails(monkeypatch):
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    state = ServerState(
        pid=os.getpid(),
        port=5050,
        url="http://127.0.0.1:5050",
        started_at="2026-10-08T11:00:00Z",
        last_seen=recent,
    )

    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    import urllib.request

    def fake_urlopen(req, timeout=1.0):
        raise OSError("Connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
    assert not is_alive(state, now=now, max_age_s=5.0)


def test_is_alive_true_when_recent_and_meta_responds(monkeypatch):
    now = datetime(2026, 10, 8, 12, 0, 0, tzinfo=timezone.utc)
    recent = (now - timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
    state = ServerState(
        pid=os.getpid(),
        port=5050,
        url="http://127.0.0.1:5050",
        started_at="2026-10-08T11:00:00Z",
        last_seen=recent,
    )

    monkeypatch.setattr(os, "kill", lambda pid, sig: None)

    import urllib.request

    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = json.dumps({"project": {"name": "test"}}).encode("utf-8")
    mock_resp.__enter__.return_value = mock_resp

    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=1.0: mock_resp)
    assert is_alive(state, now=now, max_age_s=5.0)


def test_remove_state(tmp_path: Path):
    project = tmp_path / "project"
    state = ServerState(
        pid=123,
        port=5050,
        url="http://127.0.0.1:5050",
        started_at="2026-10-08T08:00:00Z",
        last_seen="2026-10-08T08:00:00Z",
    )
    write_state(project, state)
    target = project / ".meister" / "dashboard.json"
    assert target.is_file()

    remove_state(project)
    assert not target.exists()
    # Idempotent: removing again does not raise
    remove_state(project)


def test_two_projects_independent_states(tmp_path: Path):
    proj_a = tmp_path / "proj_a"
    proj_b = tmp_path / "proj_b"
    state_a = ServerState(pid=101, port=5001, url="http://127.0.0.1:5001", started_at="2026-10-08T08:00:00Z", last_seen="2026-10-08T08:00:00Z")
    state_b = ServerState(pid=102, port=5002, url="http://127.0.0.1:5002", started_at="2026-10-08T08:01:00Z", last_seen="2026-10-08T08:01:00Z")

    write_state(proj_a, state_a)
    write_state(proj_b, state_b)

    loaded_a = read_state(proj_a)
    loaded_b = read_state(proj_b)
    assert loaded_a == state_a
    assert loaded_b == state_b
    assert loaded_a != loaded_b

