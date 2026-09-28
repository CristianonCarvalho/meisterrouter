import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from click.testing import CliRunner

from meister.cli import main
from meister.worker import (
    resolve_worker_model,
    resolve_worker_harness_and_model,
    build_harness_command,
    smoke_test_tier,
    NativeWorker,
    HarnessWorker,
    HARNESS_CODEX,
    HARNESS_ANTIGRAVITY,
    HARNESS_CLAUDE,
)


def test_resolve_worker_model():
    assert resolve_worker_model("luna") == "gpt-6-luna"
    assert resolve_worker_model("gemini_flash") == "gemini-3.8-flash-high"
    assert resolve_worker_model("haiku") == "haiku"
    assert resolve_worker_model("sonnet") == "sonnet"
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
    assert m == "haiku"

    h, m = resolve_worker_harness_and_model("sonnet")
    assert h == HARNESS_CLAUDE
    assert m == "sonnet"


def test_worker_external_model_override(monkeypatch):
    monkeypatch.setenv("MEISTER_HAIKU_MODEL", "claude-haiku-v4")
    monkeypatch.setenv("MEISTER_SONNET_MODEL", "claude-sonnet-v5")
    assert resolve_worker_model("haiku") == "claude-haiku-v4"
    h, m = resolve_worker_harness_and_model("haiku")
    assert m == "claude-haiku-v4"

    assert resolve_worker_model("sonnet") == "claude-sonnet-v5"
    h, m = resolve_worker_harness_and_model("sonnet")
    assert m == "claude-sonnet-v5"


def test_smoke_test_tier():
    for tier in ["luna", "gemini_flash", "haiku", "sonnet"]:
        res = smoke_test_tier(tier)
        assert res["tier"] == tier
        assert res["harness"] in (HARNESS_CODEX, HARNESS_ANTIGRAVITY, HARNESS_CLAUDE)
        assert res["resolved_model"] is not None
        assert isinstance(res["available"], bool)


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


def test_parse_and_apply_file_edits_removed_and_output_not_written(tmp_path):
    import meister.worker as worker_mod
    assert not hasattr(worker_mod, "parse_and_apply_file_edits")

    # Verify HarnessWorker does not extract codeblocks to disk
    response_text = """
```file:src/app.py
def hello():
    return "Malicious or unvetted write"
```
"""
    mock_process = MagicMock()
    mock_process.stdout = [response_text]
    mock_process.wait.return_value = 0

    with patch("subprocess.Popen", return_value=mock_process), \
         patch("meister.worker.find_cli_binary", return_value="/mock/bin/codex"), \
         patch("meister.worker.get_git_status_files", return_value=set()):
        worker = HarnessWorker(model="luna", cwd=str(tmp_path))
        res = worker.run_task("do not write")
        assert res["modified_files"] == []
        assert not (tmp_path / "src" / "app.py").exists()


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


def test_cli_worker_pane_timeout_blocks_direct_reexecution(tmp_path):
    runner = CliRunner()
    with patch("meister.worker.is_herdr_available", return_value=True), \
         patch("meister.worker.run_worker_in_herdr_pane", side_effect=TimeoutError("timed out")), \
         patch("meister.worker.execute_worker_task") as mock_direct_exec:
        result = runner.invoke(
            main,
            ["worker", "--model", "luna", "--task", "Fix CSS tooltip", "--cwd", str(tmp_path)],
        )
        assert result.exit_code != 0
        assert "Timeout no worker do Herdr" in result.output
        assert "Reexecução direta bloqueada" in result.output
        mock_direct_exec.assert_not_called()


@pytest.mark.asyncio
async def test_run_worker_in_herdr_pane_async_timeout_cleans_up_pane(tmp_path):
    from meister.worker import run_worker_in_herdr_pane_async
    mock_client = MagicMock()
    mock_client.split_pane = AsyncMock(return_value="w1:p2")
    mock_client.send_interrupt = AsyncMock()
    mock_client.close_pane = AsyncMock()

    with patch("meister.herdr.client.HerdrSocketClient", return_value=mock_client), \
         pytest.raises(TimeoutError):
        await run_worker_in_herdr_pane_async(
            model="luna",
            task="long task",
            cwd=str(tmp_path),
            timeout=0.01,
        )

    mock_client.send_interrupt.assert_called_once_with("w1:p2")
    mock_client.close_pane.assert_called_once_with("w1:p2")


def test_worker_resolves_model_from_config_yaml_in_cwd(tmp_path):
    """E2E-8: Worker resolve modelo a partir do meister.config.yaml presente no cwd."""
    cfg_file = tmp_path / "meister.config.yaml"
    cfg_file.write_text(
        "workers:\n"
        "  tier_order:\n"
        "    - {name: luna, harness: native, model: gpt-modelo-inexistente}\n"
        "    - {name: gemini_flash, harness: native, model: gemini-3.8-flash-medium}\n"
    )

    worker = HarnessWorker(model="luna", cwd=str(tmp_path))
    assert worker.resolved_model == "gpt-modelo-inexistente"
    cmd = build_harness_command(worker.harness, "/usr/bin/codex", worker.resolved_model, "test task", str(tmp_path))
    assert "-m" in cmd
    assert "gpt-modelo-inexistente" in cmd


def test_execute_task_file_with_explicit_config_path(tmp_path):
    """E2E-8: Worker no pane recebe config_path explicito no task.json e resolve modelo customizado."""
    cfg_dir = tmp_path / "cfg"
    cfg_dir.mkdir()
    cfg_file = cfg_dir / "meister.config.yaml"
    cfg_file.write_text(
        "workers:\n"
        "  tier_order:\n"
        "    - {name: luna, harness: native, model: gpt-override-model}\n"
    )

    worktree_dir = tmp_path / "wt"
    worktree_dir.mkdir()

    task_file = tmp_path / "task.json"
    result_file = tmp_path / "result.json"
    import json
    task_file.write_text(json.dumps({
        "task_id": "t-e2e8",
        "model": "luna",
        "task": "Do something",
        "cwd": str(worktree_dir),
        "config_path": str(cfg_file),
        "result_file": str(result_file),
    }))

    from meister.worker import execute_task_file
    with patch.object(HarnessWorker, "run_task", return_value={"status": "done", "modified_files": []}) as mock_run:
        res = execute_task_file(str(task_file))
        assert res["status"] == "done"
        mock_run.assert_called_once()


