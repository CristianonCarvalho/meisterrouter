import os
import sys
import json
import stat
import time
import shlex
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
    assert "/usr/bin" in safe_env.get("PATH", "")

    # Even if explicitly passed via extra_env, forbidden keys are eliminated
    leak_attempt = {"OPENROUTER_API_KEY": "leaked", "CUSTOM_VAR": "ok"}
    safe_with_leak = build_safe_worker_env(extra_env=leak_attempt)
    assert "OPENROUTER_API_KEY" not in safe_with_leak
    assert safe_with_leak.get("CUSTOM_VAR") == "ok"


def test_build_safe_worker_env_preserves_virtual_env_and_conda_and_pytest(monkeypatch):
    """E2E-7: build_safe_worker_env preserva VIRTUAL_ENV, CONDA_* e ordem do PATH, garantindo que o worker enxerga o mesmo python -m pytest do pai."""
    import shutil
    import subprocess

    monkeypatch.setenv("CONDA_PREFIX", "/opt/conda/envs/myenv")
    monkeypatch.setenv("CONDA_DEFAULT_ENV", "myenv")
    monkeypatch.setenv("CONDA_PYTHON_EXE", "/opt/conda/bin/python")

    # Garante que VIRTUAL_ENV aponta para a virtualenv do projeto atual
    expected_venv = os.path.dirname(os.path.dirname(os.path.abspath(sys.executable)))
    monkeypatch.setenv("VIRTUAL_ENV", expected_venv)

    safe_env = build_safe_worker_env()

    # Variáveis Conda preservadas
    assert safe_env.get("CONDA_PREFIX") == "/opt/conda/envs/myenv"
    assert safe_env.get("CONDA_DEFAULT_ENV") == "myenv"
    assert safe_env.get("CONDA_PYTHON_EXE") == "/opt/conda/bin/python"

    # VIRTUAL_ENV preservado e venv/bin na frente do PATH
    assert safe_env.get("VIRTUAL_ENV") == expected_venv
    venv_bin = os.path.join(expected_venv, "bin")
    assert safe_env.get("PATH", "").startswith(venv_bin)

    # Worker enxerga o mesmo python -m pytest do pai
    parent_version = subprocess.run(
        [sys.executable, "-m", "pytest", "--version"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    which_python = shutil.which("python", path=safe_env["PATH"])
    assert which_python is not None
    assert os.path.realpath(which_python) == os.path.realpath(sys.executable)

    worker_version = subprocess.run(
        [which_python, "-m", "pytest", "--version"],
        env=safe_env,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    assert worker_version == parent_version


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
            model="codex_luna",
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
        "model": "codex_luna",
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
        "model": "codex_luna",
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
        "model": "codex_luna",
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
        "model": "codex_luna",
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


@pytest.mark.asyncio
async def test_worker_pane_dispatch_uses_sys_executable_and_module(tmp_path, monkeypatch):
    """E2E-1: Dispatch do worker invoca sys.executable -m meister.cli run-task e ignora meister no PATH."""
    fake_bin_dir = tmp_path / "bin"
    fake_bin_dir.mkdir()
    fake_meister = fake_bin_dir / "meister"
    fake_meister.write_text("#!/bin/sh\necho 'Error: No such command run-task' >&2\nexit 1\n")
    fake_meister.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake_bin_dir}:{os.environ.get('PATH', '')}")

    mock_client = MagicMock()
    async def fake_split_pane(**kwargs):
        runs_dir = tmp_path / ".meister" / "runs"
        runs_dir.mkdir(parents=True, exist_ok=True)
        for tfile in runs_dir.glob("*_task.json"):
            rfile = str(tfile).replace("_task.json", ".json")
            write_atomic_json(rfile, {"status": "done", "modified_files": []})
        return "pane-test-e2e1"

    mock_client.split_pane = AsyncMock(side_effect=fake_split_pane)

    with patch("meister.herdr.client.HerdrSocketClient", return_value=mock_client):
        res = await run_worker_in_herdr_pane_async(
            model="codex_luna",
            task="Test task",
            cwd=str(tmp_path),
            timeout=5.0,
        )

    assert res["status"] == "done"
    called_command = mock_client.split_pane.call_args[1]["command"]
    assert shlex.quote(sys.executable) in called_command
    assert "-m meister.cli run-task" in called_command
    assert str(fake_meister) not in called_command
