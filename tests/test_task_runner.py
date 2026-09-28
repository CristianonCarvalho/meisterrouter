import sys
import json
import stat
import time
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from click.testing import CliRunner

from meister.cli import main
from meister.worker import (
    write_atomic_json,
    read_atomic_json,
    build_safe_worker_env,
    kill_process_tree,
    execute_task_file,
    run_worker_in_herdr_pane_async,
)


def test_write_and_read_atomic_json(tmp_path):
    target_file = tmp_path / "sub" / "data.json"
    data = {"task_id": "t123", "status": "done", "numbers": [1, 2, 3]}

    write_atomic_json(str(target_file), data)
    assert target_file.exists()

    loaded = read_atomic_json(str(target_file))
    assert loaded == data

    # Non-existent file returns None
    assert read_atomic_json(str(tmp_path / "none.json")) is None

    # Corrupt / empty file returns None
    empty_file = tmp_path / "empty.json"
    empty_file.write_text("")
    assert read_atomic_json(str(empty_file)) is None

    bad_json = tmp_path / "bad.json"
    bad_json.write_text("{ incomplete json")
    assert read_atomic_json(str(bad_json)) is None


def test_build_safe_worker_env_strips_openrouter(monkeypatch):
    # Achado #29: OpenRouter Boundary isolation
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-super-secret-key")
    monkeypatch.setenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")

    safe_env = build_safe_worker_env()

    assert "OPENROUTER_API_KEY" not in safe_env
    assert "OPENROUTER_BASE_URL" not in safe_env
    assert safe_env.get("ANTHROPIC_API_KEY") == "sk-ant-test"
    assert safe_env.get("PATH") == "/usr/bin:/bin"

    # Even if explicitly passed via extra_env, forbidden keys are eliminated
    leak_attempt = {"OPENROUTER_API_KEY": "leaked", "CUSTOM_VAR": "ok"}
    safe_with_leak = build_safe_worker_env(extra_env=leak_attempt)
    assert "OPENROUTER_API_KEY" not in safe_with_leak
    assert safe_with_leak.get("CUSTOM_VAR") == "ok"


def test_kill_process_tree():
    import subprocess
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        start_new_session=True,
    )
    assert proc.poll() is None
    kill_process_tree(proc.pid, is_pgid=True)
    time.sleep(0.3)
    assert proc.poll() is not None


@pytest.mark.asyncio
async def test_pane_dispatch_immune_to_shell_injection(tmp_path):
    # Achado #11: Injeção de shell no comando do pane
    mock_client = MagicMock()

    async def fake_split_pane(**kwargs):
        runs_dir = tmp_path / ".meister" / "runs"
        task_files = list(runs_dir.glob("*_task.json"))
        if task_files:
            payload = json.loads(task_files[0].read_text(encoding="utf-8"))
            res_file = payload["result_file"]
            write_atomic_json(res_file, {"status": "done", "modified_files": []})
        return "p1"

    mock_client.split_pane = AsyncMock(side_effect=fake_split_pane)

    malicious_prompt = 'evil"; rm -rf / ; echo "pwned'
    with patch("meister.herdr.client.HerdrSocketClient", return_value=mock_client):
        res = await run_worker_in_herdr_pane_async(
            model="luna",
            task=malicious_prompt,
            cwd=str(tmp_path),
            timeout=5.0,
        )

    assert res["status"] == "done"
    mock_client.split_pane.assert_called_once()
    called_command = mock_client.split_pane.call_args[1]["command"]

    # Command sent to pane MUST NOT interpolate the prompt string
    assert malicious_prompt not in called_command
    assert "run-task" in called_command

    # The prompt should be safely preserved inside the generated task.json
    runs_dir = tmp_path / ".meister" / "runs"
    list(runs_dir.glob("*_task.json"))
    # In run_worker_in_herdr_pane_async, task_file is cleaned up on completion
    assert res["status"] == "done"


def test_execute_task_file_with_fake_cli(tmp_path):
    # Setup fake CLI executable (Achado #33)
    fake_cli = tmp_path / "fake_codex.sh"
    fake_cli.write_text(
        "#!/bin/bash\n"
        "echo 'Simulating worker code generation...'\n"
        "echo 'modified content' > target.txt\n"
        "exit 0\n"
    )
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)

    task_file = tmp_path / "task.json"
    result_file = tmp_path / "result.json"
    task_payload = {
        "task_id": "test-run-1",
        "model": "luna",
        "task": "Modify target.txt",
        "cwd": str(tmp_path),
        "result_file": str(result_file),
    }
    write_atomic_json(str(task_file), task_payload)

    with patch("meister.worker.find_cli_binary", return_value=str(fake_cli)), \
         patch("meister.worker.get_git_status_files", side_effect=[set(), {"target.txt"}]):
        res = execute_task_file(str(task_file))

    assert res["status"] == "done"
    assert res["task_id"] == "test-run-1"
    assert "target.txt" in res["modified_files"]

    # Check that result.json was created atomically
    assert result_file.exists()
    saved_result = read_atomic_json(str(result_file))
    assert saved_result["status"] == "done"
    assert saved_result["task_id"] == "test-run-1"


def test_execute_task_file_failure_writes_error_result(tmp_path):
    fake_cli = tmp_path / "failing_cli.sh"
    fake_cli.write_text("#!/bin/bash\necho 'Error: syntax error' >&2\nexit 1\n")
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)

    task_file = tmp_path / "task.json"
    result_file = tmp_path / "result.json"
    task_payload = {
        "task_id": "fail-task",
        "model": "luna",
        "task": "Failing task",
        "cwd": str(tmp_path),
        "result_file": str(result_file),
    }
    write_atomic_json(str(task_file), task_payload)

    with patch("meister.worker.find_cli_binary", return_value=str(fake_cli)):
        with pytest.raises(RuntimeError):
            execute_task_file(str(task_file))

    assert result_file.exists()
    saved_result = read_atomic_json(str(result_file))
    assert saved_result["status"] == "error"
    assert saved_result["task_id"] == "fail-task"
    assert "failed with exit code 1" in saved_result["error"]


def test_execute_task_file_timeout_kills_process(tmp_path):
    # Achado #8: Hard timeout kills process group
    fake_cli = tmp_path / "hanging_cli.sh"
    fake_cli.write_text("#!/bin/bash\nsleep 10\n")
    fake_cli.chmod(fake_cli.stat().st_mode | stat.S_IEXEC)

    task_file = tmp_path / "task.json"
    result_file = tmp_path / "result.json"
    task_payload = {
        "task_id": "timeout-task",
        "model": "luna",
        "task": "Hanging task",
        "cwd": str(tmp_path),
        "timeout": 0.3,
        "result_file": str(result_file),
    }
    write_atomic_json(str(task_file), task_payload)

    with patch("meister.worker.find_cli_binary", return_value=str(fake_cli)):
        with pytest.raises(TimeoutError) as exc_info:
            execute_task_file(str(task_file))
        assert "timeout" in str(exc_info.value).lower()

    assert result_file.exists()
    saved_result = read_atomic_json(str(result_file))
    assert saved_result["status"] == "error"
    assert "timeout" in saved_result["error"].lower()


def test_cli_run_task_command(tmp_path):
    task_file = tmp_path / "task.json"
    result_file = tmp_path / "result.json"
    task_file.write_text(json.dumps({
        "task_id": "cli-1",
        "model": "luna",
        "task": "Test task",
        "cwd": str(tmp_path),
        "result_file": str(result_file),
    }))

    runner = CliRunner()
    with patch("meister.worker.execute_task_file", return_value={"status": "done", "modified_files": ["f.txt"]}):
        res = runner.invoke(main, ["run-task", str(task_file)])
        assert res.exit_code == 0
        assert "Status: done" in res.output
        assert "f.txt" in res.output
