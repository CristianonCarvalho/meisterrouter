from __future__ import annotations

import asyncio
import os
import pathlib
import shlex
import subprocess
import sys
from unittest.mock import AsyncMock, patch
import pytest

from meister import osops
from meister.config import load_config
from meister.engine.bridge import (
    HerdrEventBridge,
    _INTERPRETERS,
    _is_check_command,
    _is_interpreter,
    _worker_is_running_checks,
)
from meister.worker import write_atomic_json


def test_is_check_command_acceptance() -> None:
    # Explicit cases required by task
    assert _is_check_command(["python", "-m", "pytest", "tests/"]) is True
    assert _is_check_command(["/x/.venv/bin/pytest", "-q"]) is True
    assert _is_check_command(["npm", "test"]) is True
    assert _is_check_command(["ruff", "check", "."]) is True

    # Direct recognized commands
    for cmd in ["pytest", "py.test", "vitest", "jest", "ruff", "mypy", "tsc", "eslint"]:
        assert _is_check_command([cmd]) is True
        assert _is_check_command([f"/usr/local/bin/{cmd}", "-v"]) is True
        assert _is_check_command([f"{cmd}.exe"]) is True
        assert _is_check_command([f"C:\\Tools\\{cmd}.EXE", "arg"]) is True

    # -m pytest|ruff|mypy
    assert _is_check_command(["python", "-m", "pytest"]) is True
    assert _is_check_command(["python3", "-m", "ruff", "check"]) is True
    assert _is_check_command(["python.exe", "-m", "mypy", "meister"]) is True
    assert _is_check_command(["/venv/bin/python", "-u", "-m", "pytest", "tests/"]) is True

    # pairs: cargo test, npm test, mvn test, go test
    assert _is_check_command(["cargo", "test"]) is True
    assert _is_check_command(["cargo.exe", "test"]) is True
    assert _is_check_command(["npm", "test", "--", "-u"]) is True
    assert _is_check_command(["mvn", "test"]) is True
    assert _is_check_command(["go", "test", "./..."]) is True
    assert _is_check_command(["go.exe", "test"]) is True


def test_is_check_command_rejection() -> None:
    # Explicit rejection cases required by task
    assert _is_check_command(["claude", "-p", "rode o pytest aqui"]) is False
    assert _is_check_command(["node", "server.js"]) is False
    assert _is_check_command(["python", "-m", "http.server"]) is False

    # Other non-check commands
    assert _is_check_command([]) is False
    assert _is_check_command(["cargo", "build"]) is False
    assert _is_check_command(["npm", "start"]) is False
    assert _is_check_command(["mvn", "compile"]) is False
    assert _is_check_command(["go", "build"]) is False
    assert _is_check_command(["git", "checkout", "pytest"]) is False
    assert _is_check_command(["python", "-m", "unittest"]) is False
    assert _is_check_command(["python", "script.py", "pytest"]) is False


def test_worker_is_running_checks_executor_present(tmp_path: pathlib.Path) -> None:
    pid_file = os.path.join(str(tmp_path), "worker.harness.json")
    write_atomic_json(pid_file, {"pid": 1234, "pgid": 1234, "start": "sig"})

    with patch("meister.osops.descendant_commands", return_value=[["node", "server.js"], ["/venv/bin/pytest", "-q"]]):
        assert _worker_is_running_checks(pid_file) is True


def test_worker_is_running_checks_only_server_present(tmp_path: pathlib.Path) -> None:
    pid_file = os.path.join(str(tmp_path), "worker.harness.json")
    write_atomic_json(pid_file, {"pid": 1234, "pgid": 1234, "start": "sig"})

    with patch("meister.osops.descendant_commands", return_value=[["node", "server.js"], ["python", "-m", "http.server"]]):
        assert _worker_is_running_checks(pid_file) is False


def test_worker_is_running_checks_inspection_error(tmp_path: pathlib.Path) -> None:
    pid_file = os.path.join(str(tmp_path), "worker.harness.json")
    write_atomic_json(pid_file, {"pid": 1234, "pgid": 1234, "start": "sig"})

    with patch("meister.osops.descendant_commands", side_effect=osops.ProcessInspectionError("ps failed")):
        assert _worker_is_running_checks(pid_file) is False


def test_worker_is_running_checks_pid_file_missing_or_invalid(tmp_path: pathlib.Path) -> None:
    # Missing file
    missing = os.path.join(str(tmp_path), "missing.harness.json")
    assert _worker_is_running_checks(missing) is False
    assert _worker_is_running_checks("") is False

    # Invalid JSON
    corrupt = os.path.join(str(tmp_path), "corrupt.harness.json")
    with open(corrupt, "w", encoding="utf-8") as f:
        f.write("not valid json")
    assert _worker_is_running_checks(corrupt) is False

    # Missing pid
    no_pid = os.path.join(str(tmp_path), "nopid.harness.json")
    write_atomic_json(no_pid, {"pgid": 1234})
    assert _worker_is_running_checks(no_pid) is False

    # Invalid pid values
    for invalid_val in ["1234", -1, 0, True, False, None, [1234]]:
        inv_file = os.path.join(str(tmp_path), "inv.harness.json")
        write_atomic_json(inv_file, {"pid": invalid_val})
        assert _worker_is_running_checks(inv_file) is False


@pytest.mark.asyncio
async def test_bridge_idle_timeout_prevented_by_running_checks(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.01")
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        "router:\n  mode: first\n"
        "retry:\n  pane_lost_attempts: 0\n  pane_lost_backoff_seconds: 0\n"
        "workers:\n  idle_timeout_seconds: 0.4\n  max_runtime_seconds: 0\n"
        "  tier_order:\n    - name: A\n      harness: codex\n      model: A\n"
        "concurrency:\n  parallel_tasks: false\n  max_parallel_workers: 1\n  layout_strategy: tiled\n  isolation_mode: none\n"
    )
    config = load_config(str(cfg_file))
    client = AsyncMock()
    client.read_pane.return_value = "unchanged"
    bridge = HerdrEventBridge(config=config, client=client)

    polls = []

    async def fake_spawn_worker(tier_name: str, task_context: dict | None = None, **kwargs: object) -> tuple[str, object]:
        task_file = (task_context or {}).get("task_file")
        if task_file:
            pid_file = f"{os.path.splitext(task_file)[0]}.harness.json"
            write_atomic_json(pid_file, {"pid": 9999, "pgid": 9999, "start": "sig"})
        return "pane-1", bridge.spawner.get_tier(tier_name)

    monkeypatch.setattr(bridge.spawner, "spawn_worker_pane", fake_spawn_worker)

    def fake_descendant_commands(pid: int) -> list[list[str]]:
        polls.append(pid)
        if len(polls) <= 3:
            return [["pytest", "-q"]]
        return []

    with patch("meister.osops.descendant_commands", side_effect=fake_descendant_commands), \
         patch("meister.engine.bridge.log_event") as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask({"id": "task_check", "description": "test runner", "cwd": str(tmp_path)}),
            timeout=30,
        )

    assert result is False
    # Since idle_timeout is 0.4s and poll_interval is 0.2s, without reset it would time out around poll 2.
    # Because the first 3 polls returned pytest, last_activity was reset each time,
    # so timeout only occurred after the checks stopped.
    assert len(polls) >= 4
    events = [call.kwargs for call in log_event_mock.call_args_list]
    timeout_event = next(event for event in events if event.get("event_type") == "worker_timeout")
    assert timeout_event["kind"] == "idle"


@pytest.mark.asyncio
async def test_bridge_idle_timeout_fires_without_running_checks(tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.01")
    cfg_file = tmp_path / "cfg.yaml"
    cfg_file.write_text(
        "router:\n  mode: first\n"
        "retry:\n  pane_lost_attempts: 0\n  pane_lost_backoff_seconds: 0\n"
        "workers:\n  idle_timeout_seconds: 0.4\n  max_runtime_seconds: 0\n"
        "  tier_order:\n    - name: A\n      harness: codex\n      model: A\n"
        "concurrency:\n  parallel_tasks: false\n  max_parallel_workers: 1\n  layout_strategy: tiled\n  isolation_mode: none\n"
    )
    config = load_config(str(cfg_file))
    client = AsyncMock()
    client.read_pane.return_value = "unchanged"
    bridge = HerdrEventBridge(config=config, client=client)

    polls = []

    async def fake_spawn_worker(tier_name: str, task_context: dict | None = None, **kwargs: object) -> tuple[str, object]:
        task_file = (task_context or {}).get("task_file")
        if task_file:
            pid_file = f"{os.path.splitext(task_file)[0]}.harness.json"
            write_atomic_json(pid_file, {"pid": 9999, "pgid": 9999, "start": "sig"})
        return "pane-2", bridge.spawner.get_tier(tier_name)

    monkeypatch.setattr(bridge.spawner, "spawn_worker_pane", fake_spawn_worker)

    def fake_descendant_commands(pid: int) -> list[list[str]]:
        polls.append(pid)
        return [["node", "server.js"]]

    with patch("meister.osops.descendant_commands", side_effect=fake_descendant_commands), \
         patch("meister.engine.bridge.log_event") as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask({"id": "task_no_check", "description": "no checks", "cwd": str(tmp_path)}),
            timeout=30,
        )

    assert result is False
    # Without test executor, idle timeout fires quickly (poll count < 4)
    assert len(polls) < 4
    events = [call.kwargs for call in log_event_mock.call_args_list]
    timeout_event = next(event for event in events if event.get("event_type") == "worker_timeout")
    assert timeout_event["kind"] == "idle"


def test_is_interpreter() -> None:
    # Base interpreters in _INTERPRETERS
    for name in ["python", "python3", "pythonw", "node", "nodejs", "sh", "bash", "zsh"]:
        assert name in _INTERPRETERS
        assert _is_interpreter(name) is True
        assert _is_interpreter(f"/usr/bin/{name}") is True
        assert _is_interpreter(f"C:\\Tools\\{name}.exe") is True
        assert _is_interpreter(name.upper()) is True

    # python3.N and version variants
    assert _is_interpreter("python3.13") is True
    assert _is_interpreter("python3.11") is True
    assert _is_interpreter("python3.8") is True
    assert _is_interpreter("python3.12.1") is True
    assert _is_interpreter("python2.7") is True
    assert _is_interpreter("/usr/local/bin/python3.13") is True
    assert _is_interpreter("C:\\Python313\\python3.13.exe") is True

    # Negative cases
    assert _is_interpreter("claude") is False
    assert _is_interpreter("ruff") is False
    assert _is_interpreter("java") is False
    assert _is_interpreter("cargo") is False
    assert _is_interpreter("npm") is False
    assert _is_interpreter("git") is False
    assert _is_interpreter("server.py") is False
    assert _is_interpreter("python-foo") is False
    assert _is_interpreter("pythona") is False
    assert _is_interpreter("") is False


def test_is_check_command_script_after_interpreter() -> None:
    # Explicit acceptance cases from Global Constraints
    assert _is_check_command(["python3", "/x/.venv/bin/pytest", "-q"]) is True
    assert _is_check_command(["node", "/x/node_modules/.bin/vitest", "run"]) is True
    assert _is_check_command(["/bin/sh", "/x/pytest", "-q"]) is True

    # Additional interpreters and direct check scripts
    assert _is_check_command(["python", "/x/.venv/bin/pytest"]) is True
    assert _is_check_command(["pythonw", "C:\\venv\\Scripts\\pytest.exe"]) is True
    assert _is_check_command(["nodejs", "/x/node_modules/.bin/jest", "--watch"]) is True
    assert _is_check_command(["bash", "/usr/local/bin/ruff", "check", "."]) is True
    assert _is_check_command(["zsh", "/usr/local/bin/mypy", "meister"]) is True
    assert _is_check_command(["python3.13", "/x/bin/pytest", "-q"]) is True
    assert _is_check_command(["node", "/x/node_modules/.bin/tsc", "--noEmit"]) is True
    assert _is_check_command(["node", "/x/node_modules/.bin/eslint", "."]) is True
    assert _is_check_command(["python", "/x/py.test"]) is True


def test_is_check_command_script_after_interpreter_rejection() -> None:
    # Explicit rejection cases from Global Constraints
    assert _is_check_command(["claude", "-p", "rode o pytest"]) is False
    assert _is_check_command(["python", "server.py"]) is False

    # Item 0 is not an interpreter
    assert _is_check_command(["claude", "/x/pytest"]) is False
    assert _is_check_command(["java", "/x/pytest"]) is False
    assert _is_check_command(["cargo", "/x/pytest"]) is False

    # Options (starting with -) as argv[1] do not count as direct executor scripts
    assert _is_check_command(["python", "-u", "/x/pytest"]) is False
    assert _is_check_command(["python", "-c", "import pytest"]) is False
    assert _is_check_command(["sh", "-c", "pytest"]) is False
    assert _is_check_command(["bash", "-e", "/x/pytest"]) is False

    # Arguments deeper than item 1 do not count when item 1 is not a check command
    assert _is_check_command(["python", "server.py", "pytest"]) is False
    assert _is_check_command(["node", "server.js", "vitest"]) is False

    # Single argument without script
    assert _is_check_command(["python"]) is False
    assert _is_check_command(["node"]) is False
    assert _is_check_command(["bash"]) is False


@pytest.mark.skipif(os.name != "posix", reason="ps command requires POSIX")
def test_is_check_command_real_process(tmp_path: pathlib.Path) -> None:
    pytest_script = tmp_path / "pytest"
    pytest_script.write_text("import time\ntime.sleep(5)\n", encoding="utf-8")

    proc = subprocess.Popen([sys.executable, str(pytest_script)])
    try:
        out = subprocess.check_output(
            ["ps", "-ww", "-o", "command=", "-p", str(proc.pid)],
            text=True,
        ).strip()
        cmd_args = shlex.split(out)
        assert _is_check_command(cmd_args) is True
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()

