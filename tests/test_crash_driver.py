"""
tests/test_crash_driver.py — Unit tests for crash_driver helpers and state persistence.

Verifies:
1. FakeHerdrState sanity (_save + _load round-trip).
2. _atomic_write_json success and cleanup.
3. FakeHerdrState._save atomic behavior under mid-write failures (old state preserved, no .tmp leftover).
4. _atomic_write_json atomic behavior under mid-write failures.
5. FakeHerdrState._load remains strict and does not mask JSONDecodeError on corrupted files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

try:
    import tests.crash_driver as crash_driver
    from tests.crash_driver import FakeHerdrState, _atomic_write_json
except ImportError:
    import crash_driver  # type: ignore[no-redef]
    from crash_driver import FakeHerdrState, _atomic_write_json  # type: ignore[no-redef]


def test_fake_herdr_state_sanity(tmp_path):
    """Sanity: _save normal + reload returns identical data and functions correctly."""
    state_file = str(tmp_path / "herdr_state.json")
    state = FakeHerdrState(state_file)

    tab_id, pane_id = state.create_tab("worker_1")
    state.add_notification("Task started")

    assert os.path.exists(state_file)
    assert state.get_open_tabs() == [tab_id]
    assert state.get_open_panes() == [pane_id]

    state.close_tab(tab_id)
    assert state.get_open_tabs() == []

    # Reload from disk
    loaded = FakeHerdrState(state_file)
    assert loaded.data == state.data
    assert loaded.get_open_tabs() == []
    assert len(loaded.data["notifications"]) == 1
    assert loaded.data["notifications"][0]["msg"] == "Task started"


def test_atomic_write_json_success(tmp_path):
    """_atomic_write_json writes valid JSON and leaves no temporary files."""
    target_dir = tmp_path / "sub"
    target_file = str(target_dir / "state.json")
    payload = {"hello": "world", "num": 123, "nested": [1, 2, 3]}

    _atomic_write_json(target_file, payload)

    assert os.path.exists(target_file)
    with open(target_file, "r", encoding="utf-8") as f:
        loaded = json.load(f)
    assert loaded == payload

    # No leftover .tmp files
    tmp_files = [f for f in os.listdir(target_dir) if f.endswith(".tmp")]
    assert tmp_files == []


def test_fake_herdr_state_atomic_save_failure_preserves_old_state(tmp_path, monkeypatch):
    """
    When json.dump fails midway during FakeHerdrState._save:
    - The old state file remains intact and loadable.
    - FakeHerdrState(path) succeeds with the previous valid content.
    - No leftover .tmp files remain.
    """
    state_file = str(tmp_path / "herdr_state.json")
    state = FakeHerdrState(state_file)

    tab_id, pane_id = state.create_tab("initial_tab")
    state.add_notification("Initial note")

    # Verify initial state on disk
    with open(state_file, "r", encoding="utf-8") as f:
        initial_data = json.load(f)
    assert tab_id in initial_data["tabs"]

    # Monkeypatch json.dump to simulate failure midway through writing
    def broken_dump(obj, fp, *args, **kwargs):
        fp.write('{"tabs": {"corrupted": ')
        fp.flush()
        raise RuntimeError("Crash mid-dump simulation")

    monkeypatch.setattr(crash_driver.json, "dump", broken_dump)

    # Attempt an update that triggers _save(); it must raise RuntimeError
    with pytest.raises(RuntimeError, match="Crash mid-dump simulation"):
        state.create_tab("second_tab")

    # Restore monkeypatch before reloading
    monkeypatch.undo()

    # The existing file must NOT be empty or corrupted
    assert os.path.exists(state_file)
    assert os.path.getsize(state_file) > 0

    # Reloading state must succeed and match initial state
    loaded = FakeHerdrState(state_file)
    assert loaded.data == initial_data
    assert "second_tab" not in str(loaded.data)

    # No leftover .tmp files in directory
    tmp_files = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert tmp_files == []


def test_atomic_write_json_failure_preserves_old_file_and_cleans_tmp(tmp_path, monkeypatch):
    """
    When _atomic_write_json fails midway:
    - Previous file content is unaffected.
    - Temporary file is cleaned up.
    """
    target_file = str(tmp_path / "atomic.json")
    initial_content = {"version": 1, "status": "ok"}
    _atomic_write_json(target_file, initial_content)

    def broken_dump(obj, fp, *args, **kwargs):
        fp.write('{"version": 2, "partial": ')
        fp.flush()
        raise IOError("Disk write error")

    monkeypatch.setattr(crash_driver.json, "dump", broken_dump)

    with pytest.raises(IOError, match="Disk write error"):
        _atomic_write_json(target_file, {"version": 2, "status": "updated"})

    monkeypatch.undo()

    with open(target_file, "r", encoding="utf-8") as f:
        content = json.load(f)
    assert content == initial_content

    tmp_files = [f for f in os.listdir(tmp_path) if f.endswith(".tmp")]
    assert tmp_files == []


def test_fake_herdr_state_load_remains_strict_on_corrupt_file(tmp_path):
    """_load must remain strict: empty or corrupted state files must raise JSONDecodeError."""
    empty_file = str(tmp_path / "empty_state.json")
    Path(empty_file).write_text("")

    with pytest.raises(json.JSONDecodeError):
        FakeHerdrState(empty_file)

    corrupt_file = str(tmp_path / "corrupt_state.json")
    Path(corrupt_file).write_text('{"tabs": { invalid json')

    with pytest.raises(json.JSONDecodeError):
        FakeHerdrState(corrupt_file)
