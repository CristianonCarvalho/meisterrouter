import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from click.testing import CliRunner

from meister.cli import main
from meister.worker import (
    UnknownTierError,
    resolve_worker_harness_and_model,
    build_harness_command,
    smoke_test_tier,
    NativeWorker,
    HarnessWorker,
    HARNESS_CODEX,
    HARNESS_ANTIGRAVITY,
    HARNESS_CLAUDE,
    HARNESS_COPILOT,
)


def test_model_alias_resolver_was_removed():
    assert not hasattr(__import__("meister.worker", fromlist=["worker"]), "resolve_worker_model")


def test_resolve_worker_harness_and_model_uses_configured_routes_only():
    expected = [
        ("tier_1", HARNESS_COPILOT, "claude-haiku-5.5"),
        ("tier_1b", HARNESS_COPILOT, "gpt-6-luna"),
        ("tier_1c", HARNESS_CODEX, "gpt-6-luna"),
        ("tier_2", HARNESS_ANTIGRAVITY, "gemini-3.8-flash-high"),
        ("tier_3", HARNESS_CLAUDE, "sonnet"),
        ("tier_3b", HARNESS_CLAUDE, "opus"),
    ]
    for tier_name, expected_harness, expected_model in expected:
        harness, model = resolve_worker_harness_and_model(tier_name)
        assert (harness, model) == (expected_harness, expected_model)

    assert resolve_worker_harness_and_model(None) == (HARNESS_COPILOT, "claude-haiku-5.5")
    assert resolve_worker_harness_and_model("  ") == (HARNESS_COPILOT, "claude-haiku-5.5")
    assert resolve_worker_harness_and_model("TIER_1C") == (HARNESS_CODEX, "gpt-6-luna")
    for unknown in ("luna", "gemini"):
        with pytest.raises(UnknownTierError, match="tier_1.*tier_3b"):
            resolve_worker_harness_and_model(unknown)


def test_worker_resolution_ignores_removed_model_environment_override(monkeypatch):
    monkeypatch.setenv("MEISTER_LUNA_MODEL", "environment-model")
    assert resolve_worker_harness_and_model("tier_1c") == (
        HARNESS_CODEX,
        "gpt-6-luna",
    )


def test_smoke_test_tier():
    for tier in ["tier_1", "tier_1b", "tier_1c", "tier_2", "tier_3", "tier_3b"]:
        res = smoke_test_tier(tier)
        assert res["tier"] == tier
        assert res["harness"] in (
            HARNESS_CODEX, HARNESS_ANTIGRAVITY, HARNESS_CLAUDE, HARNESS_COPILOT
        )
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
    assert cmd_agy.index("--output-format") < cmd_agy.index("-p")
    assert cmd_agy[cmd_agy.index("--output-format") + 1] == "json"
    assert "-p" in cmd_agy

    cmd_claude = build_harness_command(HARNESS_CLAUDE, "/bin/claude", "claude-3-5-haiku-20241022", "fix code", "/tmp")
    assert cmd_claude[0] == "/bin/claude"
    assert "--dangerously-skip-permissions" in cmd_claude
    assert cmd_claude.index("--output-format") < cmd_claude.index("-p")
    assert cmd_claude[cmd_claude.index("--output-format") + 1] == "json"
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
        worker = HarnessWorker(model="tier_1c", cwd=str(tmp_path))
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
        worker = NativeWorker(model="tier_1c", cwd=str(tmp_path))
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
        worker = NativeWorker(model="tier_1c", cwd=str(tmp_path))
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
            ["worker", "--no-pane", "--model", "tier_1c", "--task", "Fix CSS tooltip", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "Status: done" in result.output
        mock_exec.assert_called_once()


def test_cli_worker_with_pane_dispatch(tmp_path, monkeypatch):
    monkeypatch.delenv("MEISTER_IN_PANE", raising=False)
    runner = CliRunner()
    mock_result = {
        "status": "done",
        "modified_files": ["app.py"],
    }
    with patch("meister.worker.is_herdr_available", return_value=True), \
         patch("meister.worker.run_worker_in_herdr_pane", return_value=mock_result) as mock_pane:
        result = runner.invoke(
            main,
            ["worker", "--pane", "--model", "tier_1c", "--task", "Fix CSS tooltip", "--cwd", str(tmp_path)],
        )
        assert result.exit_code == 0
        assert "Worker task finished in Herdr pane" in result.output
        mock_pane.assert_called_once()


def test_cli_worker_pane_timeout_blocks_direct_reexecution(tmp_path, monkeypatch):
    monkeypatch.delenv("MEISTER_IN_PANE", raising=False)
    runner = CliRunner()
    with patch("meister.worker.is_herdr_available", return_value=True), \
         patch("meister.worker.run_worker_in_herdr_pane", side_effect=TimeoutError("timed out")), \
         patch("meister.worker.execute_worker_task") as mock_direct_exec:
        result = runner.invoke(
            main,
            ["worker", "--pane", "--model", "tier_1c", "--task", "Fix CSS tooltip", "--cwd", str(tmp_path)],
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
            model="tier_1c",
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
        "    - {name: tier_1c, harness: codex, model: gpt-modelo-inexistente}\n"
        "    - {name: tier_2, harness: agy, model: gemini-3.8-flash-medium}\n"
    )

    worker = HarnessWorker(model="tier_1c", cwd=str(tmp_path))
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
        "    - {name: tier_1c, harness: codex, model: gpt-override-model}\n"
    )

    worktree_dir = tmp_path / "wt"
    worktree_dir.mkdir()

    task_file = tmp_path / "task.json"
    result_file = tmp_path / "result.json"
    import json
    task_file.write_text(json.dumps({
        "task_id": "t-e2e8",
        "model": "tier_1c",
        "task": "Do something",
        "cwd": str(worktree_dir),
        "config_path": str(cfg_file),
        "result_file": str(result_file),
    }))

    from meister.worker import execute_task_file
    usage = {"tokens_in": 12, "tokens_out": 3, "cost": 0.25, "cost_source": "estimated"}
    with patch.object(
        HarnessWorker,
        "run_task",
        return_value={"status": "done", "modified_files": [], "usage": usage},
    ) as mock_run:
        res = execute_task_file(str(task_file))
        assert res["status"] == "done"
        assert res["usage"] == usage
        assert json.loads(result_file.read_text())["usage"] == usage
        mock_run.assert_called_once()


def test_cli_worker_end_logs_usage_and_unknown_is_not_estimated(tmp_path, monkeypatch):
    monkeypatch.delenv("MEISTER_IN_PANE", raising=False)
    from meister.logger import add_event_observer, remove_event_observer

    events = []
    add_event_observer(events.append)
    try:
        runner = CliRunner()
        usage = {
            "tokens_in": 12,
            "tokens_out": 3,
            "cost": 0.25,
            "cost_source": "estimated",
            "approx": True,
        }
        with patch("meister.worker.execute_worker_task", return_value={
            "status": "done", "modified_files": [], "usage": usage,
        }):
            result = runner.invoke(
                main,
                ["worker", "--no-pane", "--model", "tier_1c", "--task", "test", "--cwd", str(tmp_path)],
            )
        assert result.exit_code == 0
        event = next(event for event in events if event["event"] == "worker_end")
        assert event["cost"] == 0.25
        assert event["cost_source"] == "estimated"
        assert (event["tokens_in"], event["tokens_out"], event["approx"]) == (12, 3, True)

        events.clear()
        with patch("meister.worker.execute_worker_task", return_value={
            "status": "done",
            "modified_files": [],
            "usage": {"cost_source": "unknown", "approx": False},
        }):
            result = runner.invoke(
                main,
                ["worker", "--no-pane", "--model", "tier_1c", "--task", "test", "--cwd", str(tmp_path)],
            )
        assert result.exit_code == 0
        event = next(event for event in events if event["event"] == "worker_end")
        assert event["cost"] == 0.0
        assert event["cost_source"] == "unknown"
    finally:
        remove_event_observer(events.append)


@pytest.mark.asyncio
async def test_run_worker_in_herdr_pane_fast_fail_on_pane_exited(tmp_path):
    """E2E-2: Detectar saída precoce do processo do pane (pane.exited) e falhar rápido com WorkerInfrastructureError."""
    import time
    from meister.worker import run_worker_in_herdr_pane_async, WorkerInfrastructureError

    mock_client = MagicMock()
    mock_client.split_pane = AsyncMock(return_value="p_test_dead")
    mock_client.close_pane = AsyncMock()

    async def fake_subscribe(callback):
        # Dispara evento pane.exited logo após inscrição
        await callback({"type": "pane_exited", "pane_id": "p_test_dead"})

    mock_client.subscribe_events = AsyncMock(side_effect=fake_subscribe)

    with patch("meister.herdr.client.HerdrSocketClient", return_value=mock_client):
        t0 = time.monotonic()
        with pytest.raises(WorkerInfrastructureError) as exc_info:
            await run_worker_in_herdr_pane_async(
                model="tier_1c",
                task="any task",
                cwd=str(tmp_path),
                timeout=180.0,
            )
        duration = time.monotonic() - t0
        # Deve falhar rapidamente (< 5s), sem esperar os 180s do timeout
        assert duration < 5.0
        assert "pane.exited" in str(exc_info.value)
        assert "erro de infraestrutura" in str(exc_info.value)
        mock_client.close_pane.assert_called_with("p_test_dead")


def test_cli_worker_infrastructure_error_fast_exit(tmp_path, monkeypatch):
    """E2E-2: CLI meister worker captura WorkerInfrastructureError e aborta com rc=2 sem escalar tier."""
    monkeypatch.delenv("MEISTER_IN_PANE", raising=False)
    from meister.worker import WorkerInfrastructureError

    runner = CliRunner()
    with patch("meister.worker.is_herdr_available", return_value=True), \
         patch("meister.worker.run_worker_in_herdr_tab", side_effect=WorkerInfrastructureError("process exited")):
        res = runner.invoke(
            main,
            [
                "worker",
                "--model", "tier_1c",
                "--task", "some task",
                "--cwd", str(tmp_path),
            ],
        )
        assert res.exit_code == 2
        assert "Erro de infraestrutura no worker" in res.output


@pytest.mark.asyncio
async def test_run_worker_in_herdr_pane_active_liveness_pane_missing(tmp_path, monkeypatch):
    """Checagem ativa no laço de pane: pane sumido (pane_exists is False) levanta WorkerInfrastructureError."""
    import time
    from meister.worker import run_worker_in_herdr_pane_async, WorkerInfrastructureError

    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.05")

    mock_client = MagicMock()
    mock_client.split_pane = AsyncMock(return_value="p_test_dead")
    mock_client.close_pane = AsyncMock()
    mock_client.pane_exists = AsyncMock(return_value=False)
    mock_client.subscribe_events = AsyncMock()

    with patch("meister.herdr.client.HerdrSocketClient", return_value=mock_client):
        t0 = time.monotonic()
        with pytest.raises(WorkerInfrastructureError) as exc_info:
            await run_worker_in_herdr_pane_async(
                model="tier_1c",
                task="any task",
                cwd=str(tmp_path),
                timeout=180.0,
            )
        duration = time.monotonic() - t0
        assert duration < 5.0
        assert "Pane p_test_dead do worker desapareceu (tab/pane fechada?) sem gerar resultado (erro de infraestrutura)" in str(exc_info.value)
        mock_client.close_pane.assert_called_with("p_test_dead")


@pytest.mark.asyncio
async def test_run_worker_in_herdr_tab_active_liveness_pane_missing(tmp_path, monkeypatch):
    """Checagem ativa no laço de tab: pane sumido (pane_exists is False) levanta WorkerInfrastructureError."""
    import time
    from meister.worker import run_worker_in_herdr_tab_async, WorkerInfrastructureError

    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.05")

    mock_client = MagicMock()
    mock_client.create_tab = AsyncMock(return_value=("t_tab1", "p_pane1"))
    mock_client.wait_pane_ready = AsyncMock(return_value=True)
    mock_client.send_text = AsyncMock()
    mock_client.close_tab = AsyncMock()
    mock_client.pane_exists = AsyncMock(return_value=False)
    mock_client.subscribe_events = AsyncMock()

    with patch("meister.herdr.client.HerdrSocketClient", return_value=mock_client):
        t0 = time.monotonic()
        with pytest.raises(WorkerInfrastructureError) as exc_info:
            await run_worker_in_herdr_tab_async(
                model="tier_1c",
                task="any task",
                cwd=str(tmp_path),
                timeout=180.0,
            )
        duration = time.monotonic() - t0
        assert duration < 5.0
        assert "Pane p_pane1 do worker desapareceu (tab/pane fechada?) sem gerar resultado (erro de infraestrutura)" in str(exc_info.value)
        mock_client.close_tab.assert_called_with("t_tab1")


@pytest.mark.asyncio
async def test_run_worker_in_herdr_pane_real_event_handling(tmp_path):
    """Eventos reais (pane_closed) no listener do worker disparam saída rápida."""
    import time
    from meister.worker import run_worker_in_herdr_pane_async, WorkerInfrastructureError

    mock_client = MagicMock()
    mock_client.split_pane = AsyncMock(return_value="w9:pFG")
    mock_client.close_pane = AsyncMock()

    async def fake_subscribe(callback):
        # Dispara evento pane_closed real com dados dentro de data
        await callback({
            "data": {"pane_id": "w9:pFG", "type": "pane_closed", "workspace_id": "w9"},
            "event": "pane_closed",
        })

    mock_client.subscribe_events = AsyncMock(side_effect=fake_subscribe)

    with patch("meister.herdr.client.HerdrSocketClient", return_value=mock_client):
        t0 = time.monotonic()
        with pytest.raises(WorkerInfrastructureError) as exc_info:
            await run_worker_in_herdr_pane_async(
                model="tier_1c",
                task="any task",
                cwd=str(tmp_path),
                timeout=180.0,
            )
        duration = time.monotonic() - t0
        assert duration < 5.0
        assert "erro de infraestrutura" in str(exc_info.value)


def test_disabled_tier_is_used_only_by_explicit_name():
    from meister.config import MeisterConfig
    from meister.worker import HARNESS_CODEX, HARNESS_COPILOT, UnknownTierError, resolve_worker_harness_and_model

    cfg = MeisterConfig()
    # tier_1c e tier_3b vêm desligadas (fora da ordem ativa).
    assert "tier_1c" not in [t.name for t in cfg.workers.tier_order]
    assert [t.name for t in cfg.workers.disabled] == ["tier_1c", "tier_3b"]

    # sem nome: vale a primeira via ATIVA, nunca uma desligada
    assert resolve_worker_harness_and_model(None, config=cfg) == (
        HARNESS_COPILOT, "claude-haiku-5.5"
    )
    # por nome explicito a via desligada funciona
    assert resolve_worker_harness_and_model("tier_1c", config=cfg) == (HARNESS_CODEX, "gpt-6-luna")
    # nome inexistente continua sendo erro e a mensagem cita as desligadas
    with pytest.raises(UnknownTierError, match="desligadas.*tier_1c.*tier_3b"):
        resolve_worker_harness_and_model("nao_existe", config=cfg)


def _ceiling_config(tmp_path, workers_ceiling, tier_ceiling=None):
    tier_extra = f", max_runtime_seconds: {tier_ceiling}" if tier_ceiling is not None else ""
    cfg_file = tmp_path / "meister.config.yaml"
    cfg_file.write_text(
        "workers:\n"
        f"  max_runtime_seconds: {workers_ceiling}\n"
        "  tier_order:\n"
        f"    - {{name: tier_1c, harness: codex, model: gpt-x{tier_extra}}}\n"
    )
    return str(cfg_file)


def test_direct_worker_without_timeout_uses_the_configured_ceiling(tmp_path):
    """`meister worker` direto nao pode ficar sem limite: vale workers.max_runtime_seconds (ou o da via)."""
    from meister.worker import execute_worker_task, _UNBOUNDED_WORKER_TIMEOUT

    def run(workers_ceiling, tier_ceiling=None, **kwargs):
        cfg = _ceiling_config(tmp_path, workers_ceiling, tier_ceiling)
        with patch.object(HarnessWorker, "run_task", return_value={"status": "done", "modified_files": []}) as mock_run:
            execute_worker_task(model="tier_1c", task="t", cwd=str(tmp_path), config_path=cfg, **kwargs)
        return mock_run.call_args.kwargs["timeout"]

    assert run(123) == 123
    assert run(123, tier_ceiling=77) == 77
    assert run(0) == _UNBOUNDED_WORKER_TIMEOUT
    assert run(123, timeout=5) == 5


def test_execute_task_file_without_timeout_uses_the_configured_ceiling(tmp_path):
    import json
    from meister.worker import execute_task_file

    cfg = _ceiling_config(tmp_path, 321)
    worktree_dir = tmp_path / "wt"
    worktree_dir.mkdir()

    def run(extra):
        task_file = tmp_path / "task.json"
        task_file.write_text(json.dumps({
            "task_id": "t-ceiling", "model": "tier_1c", "task": "x", "cwd": str(worktree_dir),
            "config_path": cfg, "result_file": str(tmp_path / "result.json"), **extra,
        }))
        with patch.object(HarnessWorker, "run_task", return_value={"status": "done", "modified_files": []}) as mock_run:
            execute_task_file(str(task_file))
        return mock_run.call_args.kwargs["timeout"]

    assert run({}) == 321
    assert run({"timeout": 42}) == 42
