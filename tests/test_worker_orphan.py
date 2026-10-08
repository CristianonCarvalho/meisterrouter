import json
import asyncio
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from meister.worker import (
    install_termination_handlers,
    process_start_signature,
    reap_harness,
    write_atomic_json,
)
from meister.config import load_config
from meister.herdr.bridge import HerdrEventBridge
from meister.state import StateManager
from unittest.mock import AsyncMock, patch

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _write_fake_harness(tmp_path: Path) -> tuple[Path, Path]:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    process_file = tmp_path / "harness-processes.json"
    harness = bin_dir / "codex"
    harness.write_text(
        f"#!{sys.executable}\n"
        "import json, os, subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        f"open({str(process_file)!r}, 'w').write(json.dumps("
        "{'pid': os.getpid(), 'child': child.pid}))\n"
        "if 'finish-normally' in ' '.join(sys.argv):\n"
        "    time.sleep(0.3)\n"
        "else:\n"
        "    time.sleep(60)\n",
        encoding="utf-8",
    )
    harness.chmod(0o755)
    return bin_dir, process_file


def _start_run_task(tmp_path: Path, finish_normally: bool = False):
    bin_dir, process_file = _write_fake_harness(tmp_path)
    config_file = tmp_path / "meister.config.yaml"
    config_file.write_text(
        "workers:\n"
        "  tier_order:\n"
        "    - name: orphan-test\n"
        "      harness: codex\n"
        "      model: default\n",
        encoding="utf-8",
    )
    task_file = tmp_path / "task.json"
    task_file.write_text(
        json.dumps(
            {
                "task_id": "orphan-test",
                "model": "orphan-test",
                "task": "finish-normally" if finish_normally else "wait",
                "cwd": str(tmp_path),
                "config_path": str(config_file),
                "timeout": 30,
            }
        ),
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(PROJECT_ROOT), env.get("PYTHONPATH", "")) if item
    )
    env["MEISTER_LOG_DIR"] = str(tmp_path / "logs")
    stdout_file = tmp_path / "run-task.stdout"
    stderr_file = tmp_path / "run-task.stderr"
    process = subprocess.Popen(
        [sys.executable, "-m", "meister.cli", "run-task", str(task_file)],
        cwd=tmp_path,
        env=env,
        stdout=stdout_file.open("wb"),
        stderr=stderr_file.open("wb"),
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not process_file.exists():
        if process.poll() is not None:
            break
        time.sleep(0.02)
    assert process_file.exists(), (
        "the fake harness did not start: "
        f"{stdout_file.read_text(errors='replace')}\n{stderr_file.read_text(errors='replace')}"
    )
    return process, process_file


def _process_is_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    try:
        result = subprocess.run(
            ["ps", "-o", "stat=", "-p", str(pid)],
            capture_output=True,
            text=True,
            timeout=2,
            check=False,
        )
        return bool(result.stdout.strip()) and "Z" not in result.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return True


def _terminate_test_group(process_file: Path) -> None:
    try:
        info = json.loads(process_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    pid = int(info["pid"])
    try:
        if os.getpgid(pid) == pid:
            os.killpg(pid, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass
    child = int(info["child"])
    try:
        os.kill(child, signal.SIGKILL)
    except (OSError, ProcessLookupError):
        pass


@pytest.mark.parametrize("sig", [signal.SIGTERM, signal.SIGHUP, signal.SIGINT])
def test_run_task_termination_does_not_orphan_harness_tree(tmp_path, sig):
    process, process_file = _start_run_task(tmp_path)
    try:
        info = json.loads(process_file.read_text(encoding="utf-8"))
        deadline = time.monotonic() + 5
        process.send_signal(sig)
        process.wait(timeout=max(0.0, deadline - time.monotonic()))
        while time.monotonic() < deadline and (
            _process_is_alive(info["pid"]) or _process_is_alive(info["child"])
        ):
            time.sleep(0.05)
        assert not _process_is_alive(info["pid"])
        assert not _process_is_alive(info["child"])
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        _terminate_test_group(process_file)


def test_normal_run_task_exit_does_not_leave_harness_descendants(tmp_path):
    process, process_file = _start_run_task(tmp_path, finish_normally=True)
    try:
        info = json.loads(process_file.read_text(encoding="utf-8"))
        assert process.wait(timeout=5) == 0
        pid_file = tmp_path / "task.harness.json"
        assert not pid_file.exists()
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and _process_is_alive(info["child"]):
            time.sleep(0.05)
        assert not _process_is_alive(info["pid"])
        assert not _process_is_alive(info["child"])
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        _terminate_test_group(process_file)


def test_sigkill_run_task_leaves_harness_for_bridge_reaper(tmp_path):
    process, process_file = _start_run_task(tmp_path)
    pid_file = tmp_path / "task.harness.json"
    try:
        info = json.loads(process_file.read_text(encoding="utf-8"))
        assert pid_file.exists()
        process.kill()
        process.wait(timeout=5)
        assert _process_is_alive(info["pid"])
        assert _process_is_alive(info["child"])
        assert reap_harness(str(pid_file)) == "killed"
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and (
            _process_is_alive(info["pid"]) or _process_is_alive(info["child"])
        ):
            time.sleep(0.05)
        assert not _process_is_alive(info["pid"])
        assert not _process_is_alive(info["child"])
        assert not pid_file.exists()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait(timeout=5)
        _terminate_test_group(process_file)


def test_reaper_rejects_pid_with_a_different_start_signature(tmp_path):
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    pid_file = tmp_path / "mismatch.harness.json"
    try:
        signature = process_start_signature(unrelated.pid)
        assert signature
        write_atomic_json(
            str(pid_file),
            {
                "pid": unrelated.pid,
                "pgid": unrelated.pid,
                "start": f"{signature} (different)",
                "cwd": str(tmp_path),
            },
        )
        assert reap_harness(str(pid_file)) == "mismatch"
        assert _process_is_alive(unrelated.pid)
        assert not pid_file.exists()
    finally:
        try:
            os.killpg(unrelated.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass
        unrelated.wait(timeout=5)


def test_reaper_reports_gone_and_ignores_missing_or_corrupt_files(tmp_path):
    completed = subprocess.run(
        [sys.executable, "-c", "pass"],
        check=True,
        capture_output=True,
    )
    gone_file = tmp_path / "gone.harness.json"
    write_atomic_json(
        str(gone_file),
        {
            "pid": completed.returncode + 2**30,
            "pgid": completed.returncode + 2**30,
            "start": "not-relevant-for-a-gone-pid",
            "cwd": str(tmp_path),
        },
    )
    assert reap_harness(str(gone_file)) == "gone"

    absent_file = tmp_path / "absent.harness.json"
    assert reap_harness(str(absent_file)) == "no_file"
    corrupt_file = tmp_path / "corrupt.harness.json"
    corrupt_file.write_text("{", encoding="utf-8")
    assert reap_harness(str(corrupt_file)) == "no_file"
    assert not corrupt_file.exists()


def test_termination_handlers_are_nested_restorable_and_safe_off_main_thread():
    original = signal.getsignal(signal.SIGTERM)
    with install_termination_handlers():
        installed = signal.getsignal(signal.SIGTERM)
        assert installed != original
        with install_termination_handlers():
            assert signal.getsignal(signal.SIGTERM) is installed
    assert signal.getsignal(signal.SIGTERM) is original

    errors = []

    def off_main_thread():
        try:
            with install_termination_handlers():
                pass
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=off_main_thread)
    thread.start()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors == []


def test_import_does_not_install_termination_handlers(tmp_path):
    env = os.environ.copy()
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import signal; before={s:signal.getsignal(s) for s in "
            "(signal.SIGTERM,signal.SIGHUP,signal.SIGINT)}; "
            "import meister.worker; "
            "after={s:signal.getsignal(s) for s in before}; "
            "assert before == after",
        ],
        cwd=tmp_path,
        env=env,
        check=True,
        timeout=5,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["timeout", "pane_lost"])
async def test_bridge_reaps_orphan_before_retry_and_logs_only_killed(
    tmp_path, monkeypatch, trigger
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "MEISTER_PANE_LIVENESS_INTERVAL",
        "0" if trigger == "timeout" else "0.001",
    )
    config_file = tmp_path / "bridge.yaml"
    config_file.write_text(
        "router:\n"
        "  mode: first\n"
        "retry:\n"
        "  pane_lost_attempts: 1\n"
        "  pane_lost_backoff_seconds: 0\n"
        "workers:\n"
        f"  idle_timeout_seconds: {0.05 if trigger == 'timeout' else 0}\n"
        "  max_runtime_seconds: 0\n"
        "  tier_order:\n"
        "    - name: orphan-test\n"
        "      harness: codex\n"
        "      model: default\n"
        "concurrency:\n"
        "  parallel_tasks: false\n"
        "  max_parallel_workers: 1\n"
        "  layout_strategy: tiled\n"
        "  isolation_mode: none\n",
        encoding="utf-8",
    )
    config = load_config(str(config_file))
    client = AsyncMock()
    client.read_pane.return_value = "no changes"
    if trigger == "pane_lost":
        client.pane_exists.return_value = False
    state = StateManager(":memory:")
    bridge = HerdrEventBridge(config=config, client=client, state_manager=state)
    spawned_groups = []
    event_order = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        attempt = len(spawned_groups) + 1
        if attempt == 1:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-c",
                    "import subprocess,sys,time; "
                    "child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
                    "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL); "
                    "print(child.pid,flush=True); time.sleep(60)",
                ],
                start_new_session=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
            child_pid = int(process.stdout.readline().strip())
            spawned_groups.append((process, child_pid))
            pid_file = f"{os.path.splitext(task_context['task_file'])[0]}.harness.json"
            write_atomic_json(
                pid_file,
                {
                    "pid": process.pid,
                    "pgid": process.pid,
                    "start": process_start_signature(process.pid),
                    "cwd": str(tmp_path),
                },
            )
        else:
            assert spawned_groups
            assert not _process_is_alive(spawned_groups[0][0].pid)
            assert not _process_is_alive(spawned_groups[0][1])
            event_order.append("retry_spawn")
            write_atomic_json(
                task_context["result_file"],
                {"status": "done", "modified_files": []},
            )
        return f"pane-{attempt}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    def capture_event(**fields):
        event_order.append(fields)

    try:
        with patch("meister.herdr.bridge.log_event", side_effect=capture_event):
            result = await asyncio.wait_for(
                bridge.execute_subtask(
                    {
                        "id": f"orphan-{trigger}",
                        "description": "exercise orphan cleanup",
                        "cwd": str(tmp_path),
                    }
                ),
                timeout=5,
            )
        assert result is True
        reaped = [
            item
            for item in event_order
            if isinstance(item, dict) and item.get("event_type") == "harness_reaped"
        ]
        assert len(reaped) == 1
        assert reaped[0]["status"] == "killed"
        assert "retry_spawn" in event_order
        assert event_order.index(reaped[0]) < event_order.index("retry_spawn")
    finally:
        for process, child in spawned_groups:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                os.kill(child, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.mark.asyncio
async def test_cleanup_run_panes_reaps_active_worker_harness(tmp_path):
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    task_file = tmp_path / "attempt_task.json"
    pid_file = tmp_path / "attempt_task.harness.json"
    write_atomic_json(
        str(pid_file),
        {
            "pid": process.pid,
            "pgid": process.pid,
            "start": process_start_signature(process.pid),
            "cwd": str(tmp_path),
        },
    )
    state = StateManager(":memory:")
    state.register_pane("worker-pane", run_id="run", subtask_id="task")
    client = AsyncMock()
    client.is_connected = True
    bridge = HerdrEventBridge(
        config=load_config(),
        client=client,
        state_manager=state,
    )
    bridge.active_workers["worker-pane"] = {
        "task_id": "task",
        "attempt": 2,
        "subtask": {"task_file": str(task_file)},
    }
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        try:
            await bridge.cleanup_run_panes("run")
            assert process.wait(timeout=5) < 0
            assert not _process_is_alive(process.pid)
            event = next(
                call.kwargs
                for call in log_event_mock.call_args_list
                if call.kwargs.get("event_type") == "harness_reaped"
            )
            assert event == {
                "event_type": "harness_reaped",
                "task_id": "task",
                "attempt": 2,
                "status": "killed",
            }
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            if process.poll() is None:
                process.wait(timeout=5)


@pytest.mark.asyncio
async def test_timeout_ignores_error_result_written_because_of_our_own_reap(tmp_path, monkeypatch):
    """O run-task morto pelo reap grava um resultado de erro (-9); o timeout manda: retry, não worker_error."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0")
    config_file = tmp_path / "bridge.yaml"
    config_file.write_text(
        "router:\n"
        "  mode: first\n"
        "retry:\n"
        "  pane_lost_attempts: 1\n"
        "  pane_lost_backoff_seconds: 0\n"
        "workers:\n"
        "  idle_timeout_seconds: 0.05\n"
        "  max_runtime_seconds: 0\n"
        "  tier_order:\n"
        "    - name: orphan-test\n"
        "      harness: codex\n"
        "      model: default\n"
        "concurrency:\n"
        "  parallel_tasks: false\n"
        "  max_parallel_workers: 1\n"
        "  layout_strategy: tiled\n"
        "  isolation_mode: none\n",
        encoding="utf-8",
    )
    config = load_config(str(config_file))
    client = AsyncMock()
    client.read_pane.return_value = "no changes"
    state = StateManager(":memory:")
    bridge = HerdrEventBridge(config=config, client=client, state_manager=state)
    contexts = []
    harnesses = []
    events = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        attempt = len(contexts) + 1
        contexts.append(task_context)
        if attempt == 1:
            process = subprocess.Popen(
                [sys.executable, "-c", "import time; time.sleep(60)"],
                start_new_session=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            harnesses.append(process)
            pid_file = f"{os.path.splitext(task_context['task_file'])[0]}.harness.json"
            write_atomic_json(
                pid_file,
                {
                    "pid": process.pid,
                    "pgid": process.pid,
                    "start": process_start_signature(process.pid),
                    "cwd": str(tmp_path),
                },
            )
        else:
            write_atomic_json(
                task_context["result_file"],
                {"status": "done", "modified_files": []},
            )
        return f"pane-{attempt}", bridge.spawner.get_tier(tier_name)

    async def interrupt_like_killed_run_task(pane_id):
        # No fluxo real, o run-task cujo harness acabou de ser morto grava este resultado antes do bridge lê-lo.
        if pane_id == "pane-1":
            write_atomic_json(
                contexts[0]["result_file"],
                {"status": "error", "error": "Harness codex failed with exit code -9", "exit_code": 1},
            )

    client.send_interrupt.side_effect = interrupt_like_killed_run_task
    bridge.spawner.spawn_worker_pane = fake_spawn

    try:
        with patch("meister.herdr.bridge.log_event", side_effect=lambda **fields: events.append(fields)):
            result = await asyncio.wait_for(
                bridge.execute_subtask(
                    {"id": "reap-error", "description": "timeout after reap", "cwd": str(tmp_path)}
                ),
                timeout=8,
            )
        assert result is True
        assert len(contexts) == 2
        types = [event.get("event_type") for event in events]
        assert "harness_reaped" in types
        assert "worker_retry" in types or "worker_timeout" in types
        assert not any(
            event.get("event_type") == "subtask_rejected" and event.get("reason") == "worker_error"
            for event in events
        )
    finally:
        for process in harnesses:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            if process.poll() is None:
                process.wait(timeout=5)
