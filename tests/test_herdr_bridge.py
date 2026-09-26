import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from meister.herdr.bridge import HerdrEventBridge
from meister.config import load_config
from meister.herdr.dag import SubtaskNode


@pytest.mark.asyncio
async def test_bridge_dispatches_parallel_workers(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
concurrency:
  parallel_tasks: true
  max_parallel_workers: 2
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()
    mock_client.split_pane.side_effect = ["w1:p2", "w1:p3"]
    mock_client.prompt_agent.return_value = {"status": "done"}
    mock_client.read_pane.return_value = "Success output"

    bridge = HerdrEventBridge(config=config, client=mock_client)
    subtasks = [
        {"id": "t1", "description": "Backend", "target_files": ["backend.py"], "depends_on": []},
        {"id": "t2", "description": "Frontend", "target_files": ["frontend.py"], "depends_on": []},
    ]
    success = await bridge.execute_parallel_batch(subtasks)
    assert success is True
    assert mock_client.split_pane.call_count == 2


@pytest.mark.asyncio
async def test_bridge_bounds_concurrency_with_max_parallel_workers(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
concurrency:
  parallel_tasks: true
  max_parallel_workers: 2
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()
    mock_client.split_pane.side_effect = ["w1:p1", "w1:p2", "w1:p3", "w1:p4"]
    mock_client.read_pane.return_value = "Done"

    active_concurrent = 0
    max_observed_concurrent = 0

    async def mock_prompt(*args, **kwargs):
        nonlocal active_concurrent, max_observed_concurrent
        active_concurrent += 1
        if active_concurrent > max_observed_concurrent:
            max_observed_concurrent = active_concurrent
        await asyncio.sleep(0.02)
        active_concurrent -= 1
        return {"status": "done"}

    mock_client.prompt_agent.side_effect = mock_prompt

    bridge = HerdrEventBridge(config=config, client=mock_client)
    subtasks = [
        {"id": f"t{i}", "description": f"Task {i}", "target_files": [f"f{i}.py"], "depends_on": []}
        for i in range(4)
    ]
    success = await bridge.execute_parallel_batch(subtasks)
    assert success is True
    assert max_observed_concurrent <= 2
    assert mock_client.split_pane.call_count == 4


@pytest.mark.asyncio
async def test_bridge_quota_failover_escalates_to_next_tier(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
workers:
  tier_order:
    - name: "luna"
      harness: "native"
      model: "openai/gpt-6-luna"
    - name: "gemini_flash"
      harness: "native"
      model: "google/gemini-2.5-flash"
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()
    mock_client.split_pane.side_effect = ["w1:p1", "w1:p2"]

    # First attempt fails with 429 quota, second attempt succeeds
    mock_client.prompt_agent.side_effect = [
        {"status": "error", "output": "HTTP 429: Insufficient quota balance"},
        {"status": "done", "output": "Successfully implemented"},
    ]
    mock_client.read_pane.side_effect = [
        "HTTP 429: Insufficient quota balance",
        "Successfully implemented",
    ]

    bridge = HerdrEventBridge(config=config, client=mock_client)
    subtask = {"id": "t1", "description": "Auth module", "target_files": ["auth.py"]}

    success = await bridge.execute_subtask(subtask)
    assert success is True
    # Should have interrupted the first failing pane
    mock_client.send_interrupt.assert_awaited_with("w1:p1")
    # Should have split two panes (initial luna, then escalated gemini_flash)
    assert mock_client.split_pane.call_count == 2


@pytest.mark.asyncio
async def test_bridge_handle_herdr_event_detects_quota_and_interrupts(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
concurrency:
  max_parallel_workers: 2
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()

    bridge = HerdrEventBridge(config=config, client=mock_client)
    # Register an active worker pane
    bridge.active_workers["w1:p9"] = {
        "task_id": "t1",
        "current_tier": "luna",
        "status": "running",
    }

    event = {
        "event": "agent.output",
        "pane_id": "w1:p9",
        "output": "Error: 429 Too Many Requests - rate limit exceeded",
    }
    await bridge.handle_herdr_event(event)

    # Bridge should have sent interrupt to pane w1:p9
    mock_client.send_interrupt.assert_awaited_with("w1:p9")
    assert bridge.active_workers["w1:p9"]["status"] == "quota_error"


@pytest.mark.asyncio
async def test_bridge_execute_plan_with_dag_batches(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
concurrency:
  parallel_tasks: true
  max_parallel_workers: 2
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()
    mock_client.split_pane.side_effect = ["w1:p1", "w1:p2", "w1:p3"]
    mock_client.prompt_agent.return_value = {"status": "done"}
    mock_client.read_pane.return_value = "Done"

    bridge = HerdrEventBridge(config=config, client=mock_client)
    steps = [
        {"id": "t1", "description": "Backend API", "target_files": ["api.py"], "depends_on": []},
        {"id": "t2", "description": "Frontend UI", "target_files": ["app.tsx"], "depends_on": []},
        {"id": "t3", "description": "Integration test", "target_files": ["test_integ.py"], "depends_on": ["t1", "t2"]},
    ]

    success = await bridge.execute_plan(steps)
    assert success is True
    assert mock_client.split_pane.call_count == 3


@pytest.mark.asyncio
async def test_bridge_run_orchestration_cycle(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
concurrency:
  max_parallel_workers: 2
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()
    mock_client.read_pane.return_value = """
Plan:
1. id: step1 | files: [a.py] | depends: []
2. id: step2 | files: [b.py] | depends: [step1]
"""
    mock_client.split_pane.side_effect = ["w1:p1", "w1:p2"]
    mock_gate = MagicMock()
    mock_gate.run_verification.return_value = (True, "All 84 tests passed")
    mock_gate.get_diff_summary.return_value = "Added features and tests"
    mock_gate.evaluate_completion.return_value = {"action": "COMPLETE", "confidence": 0.99}

    bridge = HerdrEventBridge(config=config, client=mock_client, gate=mock_gate)

    with patch.object(bridge, "execute_plan", new=AsyncMock(return_value=True)) as mock_exec:
        success = await bridge.run_orchestration_cycle(workspace_id="ws1", architect_pane_id="w1:p0")
        assert success is True
        mock_client.read_pane.assert_awaited_with("w1:p0")
        mock_client.show_notification.assert_awaited_once()
        mock_exec.assert_awaited_once()
        mock_gate.run_verification.assert_called_once()


def test_parse_architect_plan_formats():
    from meister.herdr.bridge import parse_architect_plan

    # JSON code fence format
    json_text = """
Here is the execution plan:
```json
[
  {"id": "t1", "description": "Backend", "target_files": ["api.py"], "depends_on": []},
  {"id": "t2", "description": "Frontend", "target_files": ["ui.py"], "depends_on": ["t1"]}
]
```
Proceed with execution.
"""
    parsed = parse_architect_plan(json_text)
    assert len(parsed) == 2
    assert parsed[0]["id"] == "t1"
    assert parsed[1]["depends_on"] == ["t1"]

    # Numbered markdown list
    list_text = """
1. Implement database schema in models.py
2. Implement auth endpoints in routes.py
"""
    parsed_list = parse_architect_plan(list_text)
    assert len(parsed_list) == 2
    assert parsed_list[0]["id"] == "task_1"
    assert "models.py" in parsed_list[0]["description"]

    # Empty text
    assert parse_architect_plan("") == []
    assert parse_architect_plan("   ") == []


@pytest.mark.asyncio
async def test_bridge_max_tier_exhaustion_returns_false(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
workers:
  tier_order:
    - name: "luna"
      harness: "native"
      model: "openai/gpt-6-luna"
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()
    mock_client.split_pane.return_value = "w1:p1"
    mock_client.prompt_agent.return_value = {"status": "error", "output": "429 Rate limit"}
    mock_client.read_pane.return_value = "429 Rate limit"

    bridge = HerdrEventBridge(config=config, client=mock_client)
    success = await bridge.execute_subtask({"id": "t1", "description": "Single tier test"})
    assert success is False


@pytest.mark.asyncio
async def test_bridge_non_quota_error_returns_false(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
workers:
  tier_order:
    - name: "luna"
      harness: "native"
      model: "openai/gpt-6-luna"
    - name: "gemini_flash"
      harness: "native"
      model: "google/gemini-2.5-flash"
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()
    mock_client.split_pane.return_value = "w1:p1"
    mock_client.prompt_agent.return_value = {"status": "error", "output": "SyntaxError in code"}
    mock_client.read_pane.return_value = "SyntaxError"

    bridge = HerdrEventBridge(config=config, client=mock_client)
    success = await bridge.execute_subtask({"id": "t1", "description": "Syntax error test"})
    assert success is False
    # Did not escalate to second tier because it was not a quota error
    assert mock_client.split_pane.call_count == 1


@pytest.mark.asyncio
async def test_bridge_run_orchestration_cycle_with_direct_task():
    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.split_pane.return_value = "w1:p2"
    mock_client.prompt_agent.return_value = {"status": "done"}
    mock_client.read_pane.return_value = "Success output"
    mock_client.show_notification = AsyncMock()

    mock_gate = MagicMock()
    mock_gate.run_verification.return_value = (True, "All tests passed")
    mock_gate.get_diff_summary.return_value = "1 file changed"
    mock_gate.evaluate_completion.return_value = {"action": "COMPLETE"}

    bridge = HerdrEventBridge(client=mock_client, gate=mock_gate)
    success = await bridge.run_orchestration_cycle(
        workspace_id="w1",
        architect_pane_id="w1:p1",
        task="1. Implement login endpoint",
    )
    assert success is True
    # Should not read architect pane when task is directly provided
    assert not mock_client.read_pane.called or mock_client.read_pane.call_args[0][0] != "w1:p1"


@pytest.mark.asyncio
async def test_bridge_run_orchestration_cycle_autodetects_pane_and_workspace():
    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.get_current_pane.return_value = {"pane_id": "auto_pane", "workspace_id": "auto_ws"}
    mock_client.read_pane.return_value = "1. Auto detected task"
    mock_client.split_pane.return_value = "auto_ws:p3"
    mock_client.prompt_agent.return_value = {"status": "done"}
    mock_client.show_notification = AsyncMock()

    mock_gate = MagicMock()
    mock_gate.run_verification.return_value = (True, "All tests passed")
    mock_gate.get_diff_summary.return_value = "1 file changed"
    mock_gate.evaluate_completion.return_value = {"action": "COMPLETE"}

    bridge = HerdrEventBridge(client=mock_client, gate=mock_gate)
    success = await bridge.run_orchestration_cycle(
        workspace_id=None,
        architect_pane_id=None,
    )
    assert success is True
    mock_client.get_current_pane.assert_called_once()
    assert any(c[0][0] == "auto_pane" for c in mock_client.read_pane.call_args_list)


