import json
import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from meister.worker import execute_task_file, read_atomic_json, write_atomic_json


def _write_task(tmp_path: Path, cli: Path) -> tuple[Path, Path]:
    task_file = tmp_path / "task.json"
    result_file = tmp_path / "result.json"
    write_atomic_json(
        str(task_file),
        {
            "task_id": "instant-exit",
            "model": "tier_1c",
            "task": "Instant exit task",
            "cwd": str(tmp_path),
            "result_file": str(result_file),
            "timeout": 20,
        },
    )
    return task_file, result_file


def _write_cli(tmp_path: Path, body: str) -> Path:
    cli = tmp_path / "instant_cli.sh"
    cli.write_text("#!/bin/bash\n" + body, encoding="utf-8")
    cli.chmod(cli.stat().st_mode | stat.S_IEXEC)
    return cli


def _failing_cli(tmp_path: Path) -> Path:
    return _write_cli(tmp_path, "echo 'Error: auth failed' >&2\nexit 1\n")


def test_instant_exit_reports_real_error_even_when_getpgid_fails(tmp_path, monkeypatch):
    cli = _failing_cli(tmp_path)
    task_file, result_file = _write_task(tmp_path, cli)

    getpgid_calls = []

    def no_such_process(pid):
        getpgid_calls.append(pid)
        raise ProcessLookupError(3, "No such process")

    monkeypatch.setattr(os, "getpgid", no_such_process)
    with patch("meister.worker.find_cli_binary", return_value=str(cli)):
        with pytest.raises(RuntimeError, match="failed with exit code 1"):
            execute_task_file(str(task_file))

    assert getpgid_calls == [], "pgid must be the harness pid (session leader), not os.getpgid"
    saved = read_atomic_json(str(result_file))
    assert saved is not None
    assert saved["status"] == "error"
    assert "failed with exit code 1" in saved["error"]
    assert "No such process" not in saved["error"]


def test_instant_exit_reports_real_error_repeatedly(tmp_path):
    cli = _failing_cli(tmp_path)
    for attempt in range(25):
        run_dir = tmp_path / f"run-{attempt}"
        run_dir.mkdir()
        task_file, result_file = _write_task(run_dir, cli)
        with patch("meister.worker.find_cli_binary", return_value=str(cli)):
            with pytest.raises(RuntimeError, match="failed with exit code 1"):
                execute_task_file(str(task_file))

        saved = read_atomic_json(str(result_file))
        assert saved is not None, f"attempt {attempt}: no result file"
        assert "failed with exit code 1" in saved["error"], f"attempt {attempt}"
        assert "No such process" not in saved["error"], f"attempt {attempt}"


def test_pid_file_failure_does_not_abort_successful_task(tmp_path):
    cli = _write_cli(tmp_path, "echo 'ok'\nexit 0\n")
    task_file, result_file = _write_task(tmp_path, cli)

    def broken_signature(_pid):
        raise OSError("ps unavailable")

    with patch("meister.worker.find_cli_binary", return_value=str(cli)), patch(
        "meister.worker.process_start_signature", side_effect=broken_signature
    ):
        res = execute_task_file(str(task_file))

    assert res["status"] == "done"
    assert res["exit_code"] == 0
    saved = read_atomic_json(str(result_file))
    assert saved is not None and saved["status"] == "done"
    assert not (tmp_path / "task.harness.json").exists()


def test_normal_run_records_pid_equal_to_pgid(tmp_path):
    pid_snapshot = tmp_path / "pid-snapshot.json"
    pid_file = tmp_path / "task.harness.json"
    cli = _write_cli(
        tmp_path,
        f"sleep 0.5\ncp '{pid_file}' '{pid_snapshot}'\necho 'ok'\nexit 0\n",
    )
    task_file, _ = _write_task(tmp_path, cli)

    with patch("meister.worker.find_cli_binary", return_value=str(cli)):
        res = execute_task_file(str(task_file))

    assert res["exit_code"] == 0
    recorded = json.loads(pid_snapshot.read_text(encoding="utf-8"))
    assert isinstance(recorded["pid"], int) and recorded["pid"] > 0
    assert recorded["pgid"] == recorded["pid"]
    assert not pid_file.exists()
