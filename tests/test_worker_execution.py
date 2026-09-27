import os
import pytest
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from meister.cli import main
from meister.worker import (
    resolve_worker_model,
    resolve_worker_harness_and_model,
    find_cli_binary,
    build_harness_command,
    parse_and_apply_file_edits,
    execute_worker_task,
    NativeWorker,
    HarnessWorker,
    HARNESS_CODEX,
    HARNESS_ANTIGRAVITY,
    HARNESS_CLAUDE,
)


def test_resolve_worker_model():
    assert resolve_worker_model("luna") == "gpt-6-luna"
    assert resolve_worker_model("gemini_flash") == "gemini-3.8-flash-high"
    assert resolve_worker_model("haiku") == "claude-3-5-haiku-20241022"
    assert resolve_worker_model("custom/model:free") == "custom/model:free"


def test_resolve_worker_harness_and_model():
    # Codex mappings
    h, m = resolve_worker_harness_and_model("luna")
    assert h == HARNESS_CODEX
    assert m == "gpt-6-luna"

    h, m = resolve_worker_harness_and_model("codex")
    assert h == HARNESS_CODEX

    # Antigravity mappings
    h, m = resolve_worker_harness_and_model("gemini_flash")
    assert h == HARNESS_ANTIGRAVITY
    assert m == "gemini-3.8-flash-high"

    h, m = resolve_worker_harness_and_model("antigravity")
    assert h == HARNESS_ANTIGRAVITY
    assert m == "gemini-3.8-flash-high"

    # Claude mappings
    h, m = resolve_worker_harness_and_model("haiku")
    assert h == HARNESS_CLAUDE
    assert m == "claude-3-5-haiku-20241022"

    h, m = resolve_worker_harness_and_model("sonnet")
    assert h == HARNESS_CLAUDE
    assert m == "claude-3-7-sonnet"


def test_build_harness_command():
    cmd_codex = build_harness_command(HARNESS_CODEX, "/bin/codex", "gpt-6-luna", "fix code", "/tmp")
    assert cmd_codex[0] == "/bin/codex"
    assert cmd_codex[1] == "exec"
    assert "--dangerously-bypass-approvals-and-sandbox" in cmd_codex
    assert "-C" in cmd_codex

    cmd_agy = build_harness_command(HARNESS_ANTIGRAVITY, "/bin/agy", "gemini-3.8-flash-high", "fix code", "/tmp")
    assert cmd_agy[0] == "/bin/agy"
    assert "--dangerously-skip-permissions" in cmd_agy
    assert "--model" in cmd_agy
    assert "gemini-3.8-flash-high" in cmd_agy
    assert "-p" in cmd_agy

    cmd_claude = build_harness_command(HARNESS_CLAUDE, "/bin/claude", "claude-3-5-haiku-20241022", "fix code", "/tmp")
    assert cmd_claude[0] == "/bin/claude"
    assert "--dangerously-skip-permissions" in cmd_claude
    assert "-p" in cmd_claude


def test_parse_and_apply_file_edits(tmp_path):
    response_text = """
I have analyzed the code and applied the required fixes.

```file:src/app.py
def hello():
    return "Hello MeisterRouter"
```

Also updated the styles:
```file:styles/tooltip.css
.tooltip {
    overflow: visible;
}
```

Task completed.
"""
    modified = parse_and_apply_file_edits(response_text, base_dir=str(tmp_path))
    assert len(modified) == 2
    assert "src/app.py" in modified
    assert "styles/tooltip.css" in modified

    app_py = tmp_path / "src" / "app.py"
    assert app_py.exists()
    assert "Hello MeisterRouter" in app_py.read_text()

    css_file = tmp_path / "styles" / "tooltip.css"
    assert css_file.exists()
    assert "overflow: visible;" in css_file.read_text()


def test_native_worker_execute_task_success(tmp_path):
    target_file = tmp_path / "test.txt"
    target_file.write_text("old content")

    mock_process = MagicMock()
    mock_process.stdout = ["Applying updates via codex\n", "Done\n"]
    mock_process.wait.return_value = 0

    with patch("subprocess.Popen", return_value=mock_process) as mock_popen, \
         patch("meister.worker.find_cli_binary", return_value="/mock/bin/codex"), \
         patch("meister.worker.get_git_status_files", side_effect=[set(), {"test.txt"}]):
        worker = NativeWorker(model="luna", cwd=str(tmp_path))
        result = worker.run_task("Update test.txt to say new content", target_files=["test.txt"])

        assert result["status"] == "done"
        assert result["harness"] == HARNESS_CODEX
        assert "test.txt" in result["modified_files"]

        # Ensure subprocess was called with codex exec and NO openrouter calls
        mock_popen.assert_called_once()
        cmd_called = mock_popen.call_args[0][0]
        assert cmd_called[0] == "/mock/bin/codex"
        assert "exec" in cmd_called


def test_native_worker_handles_failure(tmp_path):
    mock_process = MagicMock()
    mock_process.stdout = ["Error: rate limit / quota exceeded\n"]
    mock_process.wait.return_value = 1

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("meister.worker.find_cli_binary", return_value="/mock/bin/codex"):
        worker = NativeWorker(model="luna", cwd=str(tmp_path))
        with pytest.raises(RuntimeError) as exc_info:
            worker.run_task("Do something")
        assert "failed with exit code 1" in str(exc_info.value)


def test_cli_worker_with_task_flag(tmp_path):
    runner = CliRunner()
    mock_result = {
        "status": "done",
        "modified_files": ["app.py"],
        "output": "Successfully implemented",
    }

    with patch("meister.worker.execute_worker_task", return_value=mock_result) as mock_exec:
        result = runner.invoke(
            main,
            ["worker", "--no-pane", "--model", "luna", "--task", "Fix CSS tooltip", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "Status: done" in result.output
        mock_exec.assert_called_once()


def test_cli_worker_with_pane_dispatch(tmp_path):
    runner = CliRunner()
    mock_result = {
        "status": "done",
        "modified_files": ["app.py"],
    }
    with patch("meister.worker.is_herdr_available", return_value=True), \
         patch("meister.worker.run_worker_in_herdr_pane", return_value=mock_result) as mock_pane:
        result = runner.invoke(
            main,
            ["worker", "--model", "luna", "--task", "Fix CSS tooltip", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "Worker task finished in Herdr pane" in result.output
        mock_pane.assert_called_once()
