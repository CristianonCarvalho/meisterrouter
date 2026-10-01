import os
import json
import time
from pathlib import Path
import pytest
import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
from meister.herdr.bridge import HerdrEventBridge
from meister.config import load_config, MeisterConfig
from meister.state import compute_subtask_id
from meister.worker import write_atomic_json


def auto_write_result(search_dir, result_payload=None):
    if result_payload is None:
        result_payload = {"status": "done", "modified_files": []}
    dirs_to_check = [
        Path(search_dir),
        Path(os.getcwd()) / ".meister" / "runs",
    ]
    wt_dir = os.environ.get("MEISTER_WORKTREES_DIR") or os.path.expanduser("~/.meister/worktrees")
    if os.path.exists(wt_dir):
        dirs_to_check.append(Path(wt_dir))
    for d in dirs_to_check:
        if d.is_file():
            d = d.parent
        for tf in d.rglob("*_task.json"):
            try:
                data = json.loads(tf.read_text(encoding="utf-8"))
                rf = data.get("result_file")
                if rf and not os.path.exists(rf):
                    write_atomic_json(rf, result_payload)
            except Exception:
                pass


def _tier_limit_config(tmp_path, tiers, router_mode="first"):
    tier_yaml = "\n".join(
        f"    - name: {name}\n      harness: codex\n      model: {name}\n"
        + (f"      max_parallel: {limit}\n" if limit is not None else "")
        for name, limit in tiers
    )
    cfg_file = tmp_path / "tier_limits.yaml"
    cfg_file.write_text(
        f"router:\n  mode: {router_mode}\nworkers:\n  tier_order:\n{tier_yaml}"
        "concurrency:\n  parallel_tasks: true\n  max_parallel_workers: 4\n  layout_strategy: tiled\n"
    )
    return load_config(str(cfg_file))


def _mock_tier_worker(bridge, pause=0.08):
    active_by_tier = {}
    max_by_tier = {}
    active_total = 0
    max_total = 0
    used_tiers = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        nonlocal active_total, max_total
        used_tiers.append(tier_name)
        active_by_tier[tier_name] = active_by_tier.get(tier_name, 0) + 1
        max_by_tier[tier_name] = max(max_by_tier.get(tier_name, 0), active_by_tier[tier_name])
        active_total += 1
        max_total = max(max_total, active_total)
        result_file = task_context["result_file"]

        async def complete_worker():
            nonlocal active_total
            try:
                await asyncio.sleep(pause)
                write_atomic_json(result_file, {"status": "done", "modified_files": []})
            finally:
                active_by_tier[tier_name] -= 1
                active_total -= 1

        asyncio.create_task(complete_worker())
        return f"pane-{len(used_tiers)}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    return used_tiers, max_by_tier, lambda: max_total


@pytest.mark.asyncio
async def test_bridge_dispatches_parallel_workers(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
concurrency:
  parallel_tasks: true
  max_parallel_workers: 2
  layout_strategy: tiled
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()

    async def fake_split(*args, **kwargs):
        auto_write_result(tmp_path)
        return "w1:p2"

    mock_client.split_pane.side_effect = fake_split
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
async def test_bridge_spreads_parallel_tasks_across_tier_limits(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = _tier_limit_config(tmp_path, [("A", 1), ("B", 1), ("C", None)])
    bridge = HerdrEventBridge(config=config, client=AsyncMock())
    bridge.current_run_id = "tier-spread"
    used, max_by_tier, _ = _mock_tier_worker(bridge)

    result = await asyncio.wait_for(
        bridge.execute_parallel_batch(
            [{"id": f"t{i}", "description": f"task {i}", "timeout": 3} for i in range(3)]
        ),
        timeout=5,
    )

    assert result is True
    assert sorted(used) == ["A", "B", "C"]
    assert max_by_tier["A"] <= 1
    assert max_by_tier["B"] <= 1


@pytest.mark.asyncio
async def test_bridge_waits_for_per_tier_slots_and_completes_all_tasks(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = _tier_limit_config(tmp_path, [("A", 1), ("B", 1)])
    bridge = HerdrEventBridge(config=config, client=AsyncMock())
    bridge.current_run_id = "tier-wait"
    used, max_by_tier, max_total = _mock_tier_worker(bridge, pause=0.1)

    result = await asyncio.wait_for(
        bridge.execute_parallel_batch(
            [{"id": f"t{i}", "description": f"task {i}", "timeout": 3} for i in range(4)]
        ),
        timeout=5,
    )

    assert result is True
    assert len(used) == 4
    assert max_total() == 2
    assert max_by_tier == {"A": 1, "B": 1}


@pytest.mark.asyncio
async def test_bridge_without_tier_limits_keeps_first_tier_and_skips_slot_logic(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = _tier_limit_config(tmp_path, [("A", None), ("B", None)])
    bridge = HerdrEventBridge(config=config, client=AsyncMock())
    bridge.current_run_id = "tier-unlimited"
    used, _, _ = _mock_tier_worker(bridge)

    with patch("meister.herdr.bridge.log_event") as log_event_mock, patch.object(
        bridge,
        "_acquire_tier_slot",
        new=AsyncMock(side_effect=AssertionError("slot acquisition should be skipped")),
    ) as acquire_mock:
        result = await asyncio.wait_for(
            bridge.execute_parallel_batch(
                [{"id": f"t{i}", "description": f"task {i}", "timeout": 3} for i in range(3)]
            ),
            timeout=5,
        )

    assert result is True
    assert used == ["A", "A", "A"]
    assert bridge._tier_active == {}
    acquire_mock.assert_not_awaited()
    assert not any(call.kwargs.get("event_type") == "tier_overflow" for call in log_event_mock.call_args_list)


@pytest.mark.asyncio
async def test_bridge_explicit_initial_tier_skips_per_tier_reservation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = _tier_limit_config(tmp_path, [("A", 1), ("B", 1)])
    bridge = HerdrEventBridge(config=config, client=AsyncMock())
    bridge.current_run_id = "tier-explicit"
    used, _, _ = _mock_tier_worker(bridge)

    assert await asyncio.wait_for(
        bridge.execute_subtask({"id": "explicit", "description": "explicit", "timeout": 3}, initial_tier="B"),
        timeout=5,
    )
    assert used == ["B"]
    assert bridge._tier_active == {}


@pytest.mark.asyncio
async def test_bridge_jev_overflow_moves_forward_and_logs_event(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = _tier_limit_config(tmp_path, [("A", 1), ("B", 1), ("C", 1)], router_mode="jev")
    bridge = HerdrEventBridge(config=config, client=AsyncMock())
    bridge.current_run_id = "tier-jev"
    bridge._tier_active["B"] = 1
    used, _, _ = _mock_tier_worker(bridge)

    with patch("meister.herdr.bridge.classify_task", return_value={"recommended_implementer": "B"}), patch(
        "meister.herdr.bridge.log_event"
    ) as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask({"id": "jev", "description": "jev", "timeout": 3}),
            timeout=5,
        )

    assert result is True
    assert used == ["C"]
    overflow = next(
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "tier_overflow"
    )
    assert overflow["tier"] == "C"
    assert overflow["skipped_tier"] == "B"
    assert overflow["run_id"] == "tier-jev"


@pytest.mark.asyncio
async def test_bridge_open_breaker_tier_is_skipped_under_limit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = _tier_limit_config(tmp_path, [("A", 1), ("B", 1)])
    bridge = HerdrEventBridge(config=config, client=AsyncMock())
    bridge.current_run_id = "tier-breaker"
    state_manager = MagicMock()
    state_manager.is_harness_available.side_effect = lambda name: name != "A"
    bridge.state_manager = state_manager
    used, max_by_tier, _ = _mock_tier_worker(bridge, pause=0.1)

    result = await asyncio.wait_for(
        bridge.execute_parallel_batch(
            [
                {"id": f"breaker-{index}", "description": "breaker", "timeout": 3}
                for index in range(2)
            ]
        ),
        timeout=5,
    )

    assert result is True
    assert used == ["B", "B"]
    assert max_by_tier["B"] == 1
    assert bridge._tier_active["B"] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [False, RuntimeError("worker failed")], ids=["false", "exception"])
async def test_bridge_releases_tier_slot_after_false_or_exception(tmp_path, failure):
    config = _tier_limit_config(tmp_path, [("A", 1)])
    bridge = HerdrEventBridge(config=config, client=AsyncMock())
    calls = 0

    async def fake_core(subtask, initial_tier, run_id):
        nonlocal calls
        calls += 1
        task_id = subtask["id"]
        key = compute_subtask_id(run_id or "default", task_id, subtask.get("description", task_id))
        await bridge._acquire_tier_slot("A", key, run_id)
        if calls == 1:
            if isinstance(failure, Exception):
                raise failure
            return failure
        return True

    bridge._execute_subtask_core = fake_core
    first = {"id": "first", "description": "first"}
    if isinstance(failure, Exception):
        with pytest.raises(RuntimeError, match="worker failed"):
            await asyncio.wait_for(bridge.execute_subtask(first), timeout=2)
    else:
        assert await asyncio.wait_for(bridge.execute_subtask(first), timeout=2) is False

    assert await asyncio.wait_for(
        bridge.execute_subtask({"id": "second", "description": "second"}),
        timeout=2,
    )
    assert bridge._tier_active["A"] == 0
    assert bridge._held_slots == {}


@pytest.mark.asyncio
async def test_bridge_bounds_concurrency_with_max_parallel_workers(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
concurrency:
  parallel_tasks: true
  max_parallel_workers: 2
  layout_strategy: tiled
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()

    active_concurrent = 0
    max_observed_concurrent = 0

    async def mock_split(*args, **kwargs):
        nonlocal active_concurrent, max_observed_concurrent
        active_concurrent += 1
        if active_concurrent > max_observed_concurrent:
            max_observed_concurrent = active_concurrent
        auto_write_result(tmp_path)
        await asyncio.sleep(0.02)
        active_concurrent -= 1
        return "w1:p1"

    mock_client.split_pane.side_effect = mock_split
    mock_client.read_pane.return_value = "Done"

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
      harness: "codex"
      model: "openai/gpt-6-luna"
    - name: "gemini_flash"
      harness: "agy"
      model: "google/gemini-2.5-flash"
concurrency:
  layout_strategy: tiled
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()

    attempt = 0
    async def fake_split(*args, **kwargs):
        nonlocal attempt
        attempt += 1
        if attempt == 1:
            auto_write_result(tmp_path, {"status": "error", "output": "HTTP 429: Insufficient quota balance"})
            return "w1:p1"
        else:
            auto_write_result(tmp_path, {"status": "done", "output": "Successfully implemented"})
            return "w1:p2"

    mock_client.split_pane.side_effect = fake_split
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
  layout_strategy: tiled
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()

    async def fake_split(*args, **kwargs):
        auto_write_result(tmp_path)
        return "w1:p1"

    mock_client.split_pane.side_effect = fake_split
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
  isolation_mode: "none"
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
        assert mock_client.show_notification.await_count >= 1
        mock_exec.assert_awaited_once()
        mock_gate.run_verification.assert_called_once()


@pytest.mark.asyncio
async def test_bridge_run_orchestration_cycle_gate_failure(tmp_path):
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
"""
    mock_gate = MagicMock()
    mock_gate.run_verification.return_value = (False, "1 failed")
    mock_gate.get_diff_summary.return_value = "Modified a.py"
    # Even if evaluate_completion were to return COMPLETE, gate failure must block completion
    mock_gate.evaluate_completion.return_value = {"action": "COMPLETE"}

    bridge = HerdrEventBridge(config=config, client=mock_client, gate=mock_gate)

    with patch.object(bridge, "execute_plan", new=AsyncMock(return_value=True)):
        success = await bridge.run_orchestration_cycle(workspace_id="ws1", architect_pane_id="w1:p0")
        assert success is False
        mock_gate.run_verification.assert_called_once()
        mock_gate.evaluate_completion.assert_not_called()



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
      harness: "codex"
      model: "openai/gpt-6-luna"
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()

    async def fake_split(*args, **kwargs):
        auto_write_result(tmp_path, {"status": "error", "output": "429 Rate limit"})
        return "w1:p1"

    mock_client.split_pane.side_effect = fake_split
    mock_client.read_pane.return_value = "429 Rate limit"

    bridge = HerdrEventBridge(config=config, client=mock_client)
    subtask = {"id": "t1", "description": "Single tier test"}
    success = await bridge.execute_subtask(subtask)
    assert success is False


@pytest.mark.asyncio
async def test_bridge_non_quota_error_returns_false(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
workers:
  tier_order:
    - name: "luna"
      harness: "codex"
      model: "openai/gpt-6-luna"
    - name: "gemini_flash"
      harness: "agy"
      model: "google/gemini-2.5-flash"
concurrency:
  layout_strategy: tiled
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()

    async def fake_split(*args, **kwargs):
        auto_write_result(tmp_path, {"status": "error", "output": "SyntaxError in code"})
        return "w1:p1"

    mock_client.split_pane.side_effect = fake_split
    mock_client.read_pane.return_value = "SyntaxError"

    bridge = HerdrEventBridge(config=config, client=mock_client)
    subtask = {"id": "t1", "description": "Syntax error test"}
    success = await bridge.execute_subtask(subtask)
    assert success is False
    # Did not escalate to second tier because it was not a quota error
    assert mock_client.split_pane.call_count == 1


@pytest.mark.asyncio
async def test_bridge_run_orchestration_cycle_with_direct_task():
    mock_client = AsyncMock()
    mock_client.is_connected = True

    async def fake_split_direct(*args, **kwargs):
        auto_write_result(os.getcwd())
        return "w1:p2"

    mock_client.split_pane.side_effect = fake_split_direct
    mock_client.read_pane.return_value = "Success output"
    mock_client.show_notification = AsyncMock()

    mock_gate = MagicMock()
    mock_gate.run_verification.return_value = (True, "All tests passed")
    mock_gate.get_diff_summary.return_value = "1 file changed"
    mock_gate.evaluate_completion.return_value = {"action": "COMPLETE"}

    cfg = MeisterConfig()
    cfg.concurrency.isolation_mode = "none"
    cfg.concurrency.layout_strategy = "tiled"
    bridge = HerdrEventBridge(config=cfg, client=mock_client, gate=mock_gate)
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

    async def fake_split_auto(*args, **kwargs):
        auto_write_result(os.getcwd())
        return "auto_ws:p3"

    mock_client.split_pane.side_effect = fake_split_auto
    mock_client.show_notification = AsyncMock()

    mock_gate = MagicMock()
    mock_gate.run_verification.return_value = (True, "All tests passed")
    mock_gate.get_diff_summary.return_value = "1 file changed"
    mock_gate.evaluate_completion.return_value = {"action": "COMPLETE"}

    cfg = MeisterConfig()
    cfg.concurrency.isolation_mode = "none"
    cfg.concurrency.layout_strategy = "tiled"
    bridge = HerdrEventBridge(config=cfg, client=mock_client, gate=mock_gate)
    success = await bridge.run_orchestration_cycle(
        workspace_id=None,
        architect_pane_id=None,
    )
    assert success is True
    mock_client.get_current_pane.assert_called_once()
    assert any(c[0][0] == "auto_pane" for c in mock_client.read_pane.call_args_list)


def _init_git_repo(path):
    subprocess.run(["git", "init", "-b", "main"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "ci@meister.local"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "Meister CI"], cwd=path, check=True)
    app = path / "app.py"
    app.write_text("def test_func():\n    return True\n")
    pytest_ini = path / "pytest.ini"
    pytest_ini.write_text("[pytest]\npythonpath = .\n")
    subprocess.run(["git", "add", "."], cwd=path, check=True)
    subprocess.run(["git", "commit", "-m", "initial commit"], cwd=path, check=True)


@pytest.mark.asyncio
async def test_bridge_orchestration_eval_verify_does_not_advance_main(tmp_path, monkeypatch):
    repo_dir = tmp_path / "git_repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    monkeypatch.chdir(repo_dir)

    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True).stdout.strip()

    mock_client = AsyncMock()
    mock_client.is_connected = True

    async def fake_split(*args, **kwargs):
        auto_write_result(tmp_path)
        return "w1:p1"

    mock_client.split_pane.side_effect = fake_split
    mock_client.read_pane.return_value = "Task completed"
    mock_client.show_notification = AsyncMock()

    mock_gate = MagicMock()
    mock_gate.run_verification.return_value = (True, "All tests passed")
    mock_gate.get_diff_summary.return_value = "1 file changed"
    # Jev returns VERIFY instead of COMPLETE
    mock_gate.evaluate_completion.return_value = {"action": "VERIFY", "reason": "Requires human approval"}

    bridge = HerdrEventBridge(client=mock_client, gate=mock_gate)
    success = await bridge.run_orchestration_cycle(
        workspace_id="w1",
        architect_pane_id="w1:p0",
        task="1. Add feature",
    )
    # Orchestration cycle must fail because Jev rejected completion
    assert success is False

    # Main branch MUST NOT have advanced!
    head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True).stdout.strip()
    assert head_before == head_after


@pytest.mark.asyncio
async def test_bridge_orchestration_dirty_repo_fails_fast_forward_and_preserves_main(tmp_path, monkeypatch):
    repo_dir = tmp_path / "git_repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    monkeypatch.chdir(repo_dir)

    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True).stdout.strip()

    # Dirty the main repository
    with open(repo_dir / "app.py", "a", encoding="utf-8") as f:
        f.write("\n# uncommitted changes\n")

    mock_client = AsyncMock()
    mock_client.is_connected = True

    async def fake_split_dirty(*args, **kwargs):
        auto_write_result(tmp_path)
        return "w1:p1"

    mock_client.split_pane.side_effect = fake_split_dirty
    mock_client.read_pane.return_value = "Task completed"
    mock_client.show_notification = AsyncMock()

    mock_gate = MagicMock()
    mock_gate.run_verification.return_value = (True, "All tests passed")
    mock_gate.get_diff_summary.return_value = "1 file changed"
    mock_gate.evaluate_completion.return_value = {"action": "COMPLETE"}

    bridge = HerdrEventBridge(client=mock_client, gate=mock_gate)
    success = await bridge.run_orchestration_cycle(
        workspace_id="w1",
        architect_pane_id="w1:p0",
        task="1. Add feature",
    )
    # Orchestration cycle must fail because fast-forward to dirty repo is rejected
    assert success is False

    # Main branch HEAD must NOT have advanced
    head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True).stdout.strip()
    assert head_before == head_after


@pytest.mark.asyncio
async def test_bridge_orchestration_happy_path_fast_forwards_main(tmp_path, monkeypatch):
    repo_dir = tmp_path / "git_repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    monkeypatch.chdir(repo_dir)

    head_before = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True).stdout.strip()

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.split_pane.return_value = "w1:p1"
    mock_client.show_notification = AsyncMock()

    # Simulate worker writing and committing a file in its worktree
    async def mock_split(*args, **kwargs):
        # find the active worktree and write a change
        from meister.worktree import WorktreeManager
        wm = WorktreeManager(repo_root=str(repo_dir))
        for wt in wm.list_active_worktrees():
            if "integration" not in wt.task_id:
                p = Path(wt.worktree_path) / "feature.py"
                p.write_text("FEATURE = True\n")
                wm.commit_worktree(wt.worktree_path, "feat: worker feature")
        auto_write_result(tmp_path)
        return "w1:p1"

    mock_client.split_pane.side_effect = mock_split

    async def mock_tab(*args, **kwargs):
        pane_id = await mock_split(*args, **kwargs)
        return ("w1:t1", pane_id)

    mock_client.create_tab.side_effect = mock_tab
    mock_client.close_tab = AsyncMock()
    mock_client.read_pane.return_value = "Task completed"

    mock_gate = MagicMock()
    mock_gate.run_verification.return_value = (True, "All tests passed")
    mock_gate.get_diff_summary.return_value = "feature.py | 1 +"
    mock_gate.evaluate_completion.return_value = {"action": "COMPLETE"}

    bridge = HerdrEventBridge(client=mock_client, gate=mock_gate)
    success = await bridge.run_orchestration_cycle(
        workspace_id="w1",
        architect_pane_id="w1:p0",
        task="1. Add feature",
    )
    assert success is True

    # Main branch MUST have advanced!
    head_after = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo_dir, capture_output=True, text=True, check=True).stdout.strip()
    assert head_before != head_after
    assert (repo_dir / "feature.py").exists()


@pytest.mark.asyncio
async def test_bridge_subtask_uses_task_contract_without_prompt_agent(tmp_path, monkeypatch):
    """P-1: Bridge executa worker via contrato task.json + polling de result.json sem chamar prompt_agent."""
    repo_dir = tmp_path / "git_repo"
    repo_dir.mkdir()
    _init_git_repo(repo_dir)
    monkeypatch.chdir(repo_dir)

    mock_client = AsyncMock()
    mock_client.is_connected = True
    # If prompt_agent is ever called, it must fail the test!
    mock_client.prompt_agent = MagicMock(side_effect=AssertionError("prompt_agent was called! It must be removed."))

    executed_command = None
    task_file_seen = None

    async def fake_split(*args, **kwargs):
        nonlocal executed_command, task_file_seen
        executed_command = kwargs.get("command")
        runs_dir = repo_dir / ".meister" / "runs"
        tfiles = list(runs_dir.glob("*_task.json"))
        assert len(tfiles) == 1
        task_file_seen = tfiles[0]
        data = json.loads(task_file_seen.read_text(encoding="utf-8"))
        assert data["task_id"] == "t1"
        assert data["model"] == "copilot_luna"
        # Worker writes result.json
        write_atomic_json(data["result_file"], {"status": "done", "modified_files": ["app.py"]})
        return "w1:p1"

    mock_client.split_pane.side_effect = fake_split

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    subtask = {
        "id": "t1",
        "description": "Add feature",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is True
    assert mock_client.prompt_agent.call_count == 0
    assert executed_command is not None
    assert executed_command[1:4] == ["-m", "meister.cli", "run-task"]
    assert task_file_seen is not None


@pytest.mark.asyncio
async def test_bridge_infrastructure_error_fast_fails_without_escalation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    from meister.herdr.client import HerdrRPCError

    mock_client = AsyncMock()
    mock_client.is_connected = True
    # Simulate Herdr RPC error on split_pane (e.g. agent_not_found or spawn failure)
    mock_client.split_pane.side_effect = HerdrRPCError(-32000, "agent target w9:pS not found")

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    subtask = {
        "id": "t1",
        "description": "Add feature",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is False
    # Must have failed fast on the very first attempt without cascading through luna->gemini->haiku->sonnet
    assert mock_client.split_pane.call_count == 1


@pytest.mark.asyncio
async def test_bridge_premature_exit_fast_fails_without_escalation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.split_pane.return_value = "w1:p1"
    mock_client.read_pane.return_value = "Some crash error"

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    async def simulate_exit_event():
        await asyncio.sleep(0.05)
        # Push pane.exited event
        await bridge.handle_herdr_event({
            "method": "pane.exited",
            "params": {"pane_id": "w1:p1", "exit_code": 1}
        })

    asyncio.create_task(simulate_exit_event())

    subtask = {
        "id": "t1",
        "description": "Add feature",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is False
    # Must have failed fast without escalating
    assert mock_client.split_pane.call_count == 1


@pytest.mark.asyncio
async def test_bridge_defaults_to_tabs_and_passes_worktree_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.create_tab = AsyncMock(return_value=("w1:t2", "w1:p2"))
    mock_client.wait_pane_ready = AsyncMock(return_value=True)
    mock_client.send_text = AsyncMock()
    mock_client.close_tab = AsyncMock()

    async def fake_create_tab(*args, **kwargs):
        auto_write_result(repo_dir)
        return ("w1:t2", "w1:p2")

    mock_client.create_tab.side_effect = fake_create_tab

    cfg = MeisterConfig()
    # Default layout_strategy is tabs
    assert cfg.concurrency.layout_strategy == "tabs"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    subtask = {
        "id": "t1",
        "description": "Add feature",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is True
    # Dedicated tab was created by default
    assert mock_client.create_tab.call_count == 1
    call_kwargs = mock_client.create_tab.call_args.kwargs
    assert call_kwargs.get("cwd") == str(repo_dir)
    assert call_kwargs.get("label") == "worker:t1"
    assert mock_client.split_pane.call_count == 0


@pytest.mark.asyncio
async def test_bridge_passes_cwd_to_split_pane_in_tiled_mode(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    mock_client = AsyncMock()
    mock_client.is_connected = True

    async def fake_split(*args, **kwargs):
        auto_write_result(repo_dir)
        return "w1:p1"

    mock_client.split_pane.side_effect = fake_split

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    subtask = {
        "id": "t1",
        "description": "Add feature",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is True
    assert mock_client.split_pane.call_count == 1
    assert mock_client.split_pane.call_args.kwargs.get("cwd") == str(repo_dir)


@pytest.mark.asyncio
async def test_bridge_cleans_up_subtask_worktree_and_branch_on_failure(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    from meister.worktree import WorktreeManager, IntegrationPipeline

    wt_mgr = WorktreeManager(repo_root=str(repo_dir))
    pipeline = IntegrationPipeline(wt_mgr)
    pipeline.start_integration(run_id="run_fail_test")

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.create_tab = AsyncMock()
    mock_client.wait_pane_ready = AsyncMock(return_value=True)
    mock_client.send_text = AsyncMock()
    mock_client.close_tab = AsyncMock()

    async def fake_worker_fail(*args, **kwargs):
        wts = [wt for wt in wt_mgr.list_active_worktrees() if not wt.task_id.startswith("int_")]
        assert len(wts) == 1
        wt_path = Path(wts[0].worktree_path)
        (wt_path / "temp.txt").write_text("unmerged work")
        subprocess.run(["git", "add", "temp.txt"], cwd=wt_path, check=True)
        subprocess.run(["git", "commit", "-m", "work in progress"], cwd=wt_path, check=True)
        auto_write_result(tmp_path, {"status": "error", "output": "Subtask failed"})
        return ("w1:t2", "w1:p2")

    mock_client.create_tab.side_effect = fake_worker_fail

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tabs"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)
    bridge._integration_pipeline = pipeline

    subtask = {
        "id": "t1",
        "description": "Failing subtask",
        "target_files": ["app.py"],
    }

    success = await bridge.execute_subtask(subtask, run_id="run_fail_test")
    assert success is False

    # The subtask worktree MUST be cleaned up
    remaining_wts = [wt for wt in wt_mgr.list_active_worktrees() if not wt.task_id.startswith("int_")]
    assert len(remaining_wts) == 0

    # The subtask branch MUST be deleted
    branches_proc = subprocess.run(["git", "branch"], cwd=repo_dir, capture_output=True, text=True, check=True)
    assert "meister/worktree/run_fail_test_t1" not in branches_proc.stdout

    # The unmerged commit MUST be archived under refs/meister/archive
    refs_proc = subprocess.run(["git", "for-each-ref", "refs/meister/archive/"], cwd=repo_dir, capture_output=True, text=True, check=True)
    assert "refs/meister/archive/run_fail_test_t1" in refs_proc.stdout


@pytest.mark.asyncio
async def test_bridge_subtask_and_integration_run_in_threads_without_blocking_event_loop(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    from meister.worktree import WorktreeManager, IntegrationPipeline

    mock_gate = MagicMock()
    mock_gate.run_verification.return_value = (True, "All tests passed")

    wt_mgr = WorktreeManager(repo_root=str(repo_dir))
    pipeline = IntegrationPipeline(wt_mgr, gate=mock_gate)
    pipeline.start_integration(run_id="run_thread_test")

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.create_tab = AsyncMock(return_value=("w1:t2", "w1:p2"))
    mock_client.wait_pane_ready = AsyncMock(return_value=True)
    mock_client.send_text = AsyncMock()
    mock_client.close_tab = AsyncMock()

    async def fake_worker(*args, **kwargs):
        auto_write_result(tmp_path)
        for p in Path(wt_mgr.worktrees_dir).rglob("app.py"):
            if "int_" not in str(p):
                p.write_text("print('hello modified')\n")
        return ("w1:t2", "w1:p2")

    mock_client.create_tab.side_effect = fake_worker

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tabs"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)
    bridge._integration_pipeline = pipeline

    real_to_thread = asyncio.to_thread
    dispatched_targets = []

    async def tracking_to_thread(func, *args, **kwargs):
        dispatched_targets.append(getattr(func, "__name__", str(func)))
        return await real_to_thread(func, *args, **kwargs)

    monkeypatch.setattr(asyncio, "to_thread", tracking_to_thread)

    subtask = {
        "id": "t1",
        "description": "Thread subtask",
        "target_files": ["app.py"],
    }

    success = await bridge.execute_subtask(subtask, run_id="run_thread_test")
    assert success is True

    # create_worktree, integrate_subtask, and cleanup_worktree must all have run through asyncio.to_thread!
    assert "create_worktree" in dispatched_targets
    assert "integrate_subtask" in dispatched_targets
    assert "cleanup_worktree" in dispatched_targets


@pytest.mark.asyncio
async def test_bridge_closes_worker_tab_per_attempt(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.create_tab = AsyncMock()
    mock_client.close_tab = AsyncMock()
    mock_client.wait_pane_ready = AsyncMock(return_value=True)
    mock_client.send_text = AsyncMock()

    closed_tabs = []
    created_tabs = []

    async def fake_create_tab(*args, **kwargs):
        tab_num = len(created_tabs) + 1
        t_id = f"w1:t{tab_num}"
        p_id = f"w1:p{tab_num}"
        created_tabs.append(t_id)
        auto_write_result(tmp_path)
        return (t_id, p_id)

    async def fake_close_tab(tab_id):
        closed_tabs.append(tab_id)
        return True

    mock_client.create_tab.side_effect = fake_create_tab
    mock_client.close_tab.side_effect = fake_close_tab

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tabs"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    subtask = {
        "id": "t1",
        "description": "Tab close test",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is True
    # Tab was created and closed upon attempt exit!
    assert len(created_tabs) == 1
    assert closed_tabs == ["w1:t1"]


@pytest.mark.asyncio
async def test_bridge_run_orchestration_cycle_sweeps_active_panes_on_exit(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.create_tab = AsyncMock(return_value=("w1:t2", "w1:p2"))
    mock_client.close_pane = AsyncMock()
    mock_client.show_notification = AsyncMock()

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    # Pre-register a dangling pane
    sm = bridge.get_state_manager()

    async def fake_execute_plan(steps):
        # simulate worker pane registered in SQLite during run
        sm.register_pane("w1:p_dangling", run_id=bridge.current_run_id)
        return False

    bridge.execute_plan = fake_execute_plan

    success = await bridge.run_orchestration_cycle(
        workspace_id="w1",
        architect_pane_id="w1:p0",
        task="1. Add feature",
    )
    assert success is False
    # Even on failure, all active panes for the run must be swept and closed
    assert mock_client.close_pane.called
    close_calls = [c.args[0] for c in mock_client.close_pane.call_args_list]
    assert "w1:p_dangling" in close_calls


@pytest.mark.asyncio
async def test_workers_in_same_run_get_distinct_worktree_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    from meister.worktree import WorktreeManager, IntegrationPipeline
    wt_mgr = WorktreeManager(repo_root=str(repo_dir))
    pipeline = IntegrationPipeline(wt_mgr)
    pipeline.start_integration("run-unique-paths")

    mock_client = AsyncMock()
    mock_client.is_connected = True
    spawned_cwds = []

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        spawned_cwds.append(cwd)
        auto_write_result(tmp_path)
        return ("t1", "w1:p1", None)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.return_value = MagicMock(name="luna", model="gpt-6-luna")

    bridge = HerdrEventBridge(client=mock_client, spawner=mock_spawner)
    bridge._integration_pipeline = pipeline
    bridge.current_run_id = "run-unique-paths"

    # Execute same task twice (simulating retry or zombie restart in same run)
    subtask = {
        "id": "t2",
        "description": "Task 2 shout",
        "target_files": ["text.py"],
        "cwd": str(repo_dir),
    }

    # Worker 1
    await bridge.execute_subtask(subtask)

    # Worker 2 (second run or retry)
    await bridge.execute_subtask(subtask)

    assert len(spawned_cwds) == 2
    # The two workers in the same run must have distinct worktree paths to avoid collisions
    assert spawned_cwds[0] != spawned_cwds[1], f"Worktree paths collided: {spawned_cwds}"


@pytest.mark.asyncio
async def test_resumed_orchestration_closes_registered_panes_before_cleanup(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    from tests.mocks.mock_herdr_server import run_mock_herdr_server
    from meister.herdr.client import HerdrSocketClient
    sock_path = str(tmp_path / "herdr_resume.sock")
    server = await run_mock_herdr_server(sock_path)

    try:
        client = HerdrSocketClient(sock_path)
        await client.connect()

        cfg = MeisterConfig()
        cfg.concurrency.layout_strategy = "tabs"
        cfg.concurrency.isolation_mode = "git_worktree"

        bridge = HerdrEventBridge(config=cfg, client=client)
        sm = bridge.get_state_manager()

        # Simulate previous run that crashed, leaving active pane and tab in SQLite
        raw_plan = '[{"id":"t1","description":"task 1","target_files":[],"depends_on":[]}]'
        run_record = sm.create_or_get_run(task_prompt=raw_plan, cwd=os.getcwd())
        old_run_id = str(run_record["run_id"])
        sm.register_pane("w1:p_zombie", run_id=old_run_id, tab_id="w1:t_zombie")

        # Mock execute_plan so we just observe what happens on resume startup
        async def fake_execute_plan(steps):
            return True

        bridge.execute_plan = fake_execute_plan

        # On resume, run_orchestration_cycle is called with the SAME task
        await bridge.run_orchestration_cycle(
            workspace_id="w1",
            architect_pane_id="w1:p0",
            task=raw_plan,
        )

        # Verify that w1:t_zombie and/or w1:p_zombie were closed in Herdr
        close_requests = [
            r for r in server.received_requests
            if r.get("method") in ("tab.close", "pane.close")
        ]
        closed_ids = []
        for r in close_requests:
            params = r.get("params", {})
            if "tab_id" in params:
                closed_ids.append(params["tab_id"])
            if "pane_id" in params:
                closed_ids.append(params["pane_id"])

        assert "w1:t_zombie" in closed_ids or "w1:p_zombie" in closed_ids, (
            f"Orphan pane/tab was not closed on resume. Received: {closed_ids}"
        )
        # SQLite active_panes should no longer contain the orphan
        assert "w1:p_zombie" not in sm.get_active_panes(old_run_id)

    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_subtask_rejected_logged_when_gate_fails(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    monkeypatch.chdir(tmp_path)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("def add(a, b): return a + b\n")
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text("def test_app(): assert 1 == 1\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    from meister.worktree import WorktreeManager, IntegrationPipeline
    from meister.gate import DeterministicGate

    wt_mgr = WorktreeManager(repo_root=str(repo_dir))
    gate = DeterministicGate(repo_path=str(repo_dir))
    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    pipeline.start_integration("run-gate-fail")

    mock_client = AsyncMock()
    mock_client.is_connected = True

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        # Worker introduces a deliberate failing test
        test_file = Path(cwd) / "tests" / "test_app.py"
        test_file.write_text("def test_failing(): assert 1 == 2\n")
        auto_write_result(cwd)
        return ("t1", "w1:p1", None)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.return_value = MagicMock(name="luna", model="gpt-6-luna")

    bridge = HerdrEventBridge(client=mock_client, spawner=mock_spawner, gate=gate)
    bridge._integration_pipeline = pipeline
    bridge.current_run_id = "run-gate-fail"

    subtask = {
        "id": "t2",
        "description": "Fail gate",
        "target_files": ["tests/test_app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is False

    # Read orchestration log jsonl
    log_file = log_dir / "orchestration_log.jsonl"
    assert log_file.exists()
    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    rejected_events = [e for e in events if e.get("event_type") == "subtask_rejected" and e.get("task_id") == "t2"]
    assert len(rejected_events) >= 1, f"Expected subtask_rejected event in log: {events}"
    rej = rejected_events[0]
    assert rej.get("reason") == "gate"
    assert rej.get("error") is not None and len(rej.get("error")) > 0

    # Also verify orchestration_end event logs the failure reason
    plan_json = json.dumps([subtask])
    cycle_success = await bridge.run_orchestration_cycle(
        workspace_id="w1",
        architect_pane_id="w1:p0",
        task=plan_json,
    )
    assert cycle_success is False
    events2 = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    end_events = [e for e in events2 if e.get("event_type") == "orchestration_end" and e.get("exit_code") == 1]
    assert end_events[-1].get("reason") is not None and "rejected" in end_events[-1]["reason"]


@pytest.mark.asyncio
async def test_subtask_rejected_logged_when_no_changes(tmp_path, monkeypatch):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    monkeypatch.chdir(tmp_path)

    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("def add(a, b): return a + b\n")
    tests_dir = repo_dir / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_app.py").write_text("def test_app(): assert 1 == 1\n")
    subprocess.run(["git", "add", "."], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo_dir, check=True)

    from meister.worktree import WorktreeManager, IntegrationPipeline
    from meister.gate import DeterministicGate

    wt_mgr = WorktreeManager(repo_root=str(repo_dir))
    gate = DeterministicGate(repo_path=str(repo_dir))
    pipeline = IntegrationPipeline(wt_mgr, gate=gate)
    pipeline.start_integration("run-no-changes")

    mock_client = AsyncMock()
    mock_client.is_connected = True

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        # Worker does NOT modify any file
        auto_write_result(cwd)
        return ("t1", "w1:p1", None)

    mock_spawner = MagicMock()
    mock_spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    mock_spawner.get_tier.return_value = MagicMock(name="luna", model="gpt-6-luna")

    bridge = HerdrEventBridge(client=mock_client, spawner=mock_spawner, gate=gate)
    bridge._integration_pipeline = pipeline
    bridge.current_run_id = "run-no-changes"

    subtask = {
        "id": "t1",
        "description": "No op task with target_files",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is False

    # Read orchestration log jsonl
    log_file = log_dir / "orchestration_log.jsonl"
    assert log_file.exists()
    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]

    rejected_events = [e for e in events if e.get("event_type") == "subtask_rejected" and e.get("task_id") == "t1"]
    assert len(rejected_events) >= 1, f"Expected subtask_rejected event in log: {events}"
    rej = rejected_events[0]
    assert rej.get("reason") == "no_changes"
    assert rej.get("error") is not None and "sem alterações" in rej.get("error").lower()


@pytest.mark.asyncio
async def test_bridge_skips_initial_tier_when_circuit_breaker_open(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
workers:
  tier_order:
    - name: "luna"
      harness: "codex"
      model: "openai/gpt-6-luna"
    - name: "gemini_flash"
      harness: "agy"
      model: "google/gemini-2.5-flash"
concurrency:
  layout_strategy: tiled
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()
    mock_client.read_pane.return_value = "Done"

    async def fake_split(*args, **kwargs):
        auto_write_result(tmp_path, {"status": "done", "output": "ok"})
        return "w1:p1"

    mock_client.split_pane.side_effect = fake_split

    bridge = HerdrEventBridge(config=config, client=mock_client)
    sm = bridge.get_state_manager()
    active_run = sm.create_or_get_run(task_prompt="test run")
    bridge.current_run_id = active_run["run_id"]

    spawned_tiers = []
    orig_spawn = bridge.spawner.spawn_worker_pane
    async def track_spawn(tier_name, *args, **kwargs):
        spawned_tiers.append(tier_name)
        return await orig_spawn(tier_name, *args, **kwargs)
    bridge.spawner.spawn_worker_pane = track_spawn

    # Open breaker for luna (first tier)
    sm.record_harness_failure("luna", is_quota=True)

    subtask = {"id": "t1", "description": "Auth module", "target_files": []}
    success = await bridge.execute_subtask(subtask)
    assert success is True

    # Luna was NOT dispatched; first spawn was gemini_flash
    assert len(spawned_tiers) == 1
    assert spawned_tiers[0] == "gemini_flash"

    # Check tier_skipped_breaker event
    from meister.logger import get_log_file
    log_file = Path(get_log_file())
    assert log_file.exists()
    events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
    skip_events = [e for e in events if e.get("event") == "tier_skipped_breaker" or e.get("event_type") == "tier_skipped_breaker"]
    assert len(skip_events) == 1
    assert skip_events[0]["tier"] == "gemini_flash"
    assert skip_events[0]["skipped_tier"] == "luna"
    assert skip_events[0]["task_id"] == "t1"
    assert skip_events[0]["run_id"] == bridge.current_run_id


@pytest.mark.asyncio
async def test_bridge_maintains_initial_tier_when_all_breakers_open(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
workers:
  tier_order:
    - name: "luna"
      harness: "codex"
      model: "openai/gpt-6-luna"
    - name: "gemini_flash"
      harness: "agy"
      model: "google/gemini-2.5-flash"
concurrency:
  layout_strategy: tiled
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()
    mock_client.read_pane.return_value = "Done"

    async def fake_split(*args, **kwargs):
        auto_write_result(tmp_path, {"status": "done", "output": "ok"})
        return "w1:p1"

    mock_client.split_pane.side_effect = fake_split

    bridge = HerdrEventBridge(config=config, client=mock_client)
    sm = bridge.get_state_manager()
    active_run = sm.create_or_get_run(task_prompt="test run")
    bridge.current_run_id = active_run["run_id"]

    spawned_tiers = []
    orig_spawn = bridge.spawner.spawn_worker_pane
    async def track_spawn(tier_name, *args, **kwargs):
        spawned_tiers.append(tier_name)
        return await orig_spawn(tier_name, *args, **kwargs)
    bridge.spawner.spawn_worker_pane = track_spawn

    # Open breaker for both tiers
    sm.record_harness_failure("luna", is_quota=True)
    sm.record_harness_failure("gemini_flash", is_quota=True)

    subtask = {"id": "t1", "description": "Auth module", "target_files": []}
    success = await bridge.execute_subtask(subtask)
    assert success is True

    # Maintains initial tier: luna
    assert len(spawned_tiers) == 1
    assert spawned_tiers[0] == "luna"

    from meister.logger import get_log_file
    log_file = Path(get_log_file())
    if log_file.exists():
        events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        skip_events = [e for e in events if e.get("event") == "tier_skipped_breaker" or e.get("event_type") == "tier_skipped_breaker"]
        assert len(skip_events) == 0


@pytest.mark.asyncio
async def test_bridge_initial_tier_when_no_breakers_open(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
workers:
  tier_order:
    - name: "luna"
      harness: "codex"
      model: "openai/gpt-6-luna"
    - name: "gemini_flash"
      harness: "agy"
      model: "google/gemini-2.5-flash"
concurrency:
  layout_strategy: tiled
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()
    mock_client.read_pane.return_value = "Done"

    async def fake_split(*args, **kwargs):
        auto_write_result(tmp_path, {"status": "done", "output": "ok"})
        return "w1:p1"

    mock_client.split_pane.side_effect = fake_split

    bridge = HerdrEventBridge(config=config, client=mock_client)
    sm = bridge.get_state_manager()
    active_run = sm.create_or_get_run(task_prompt="test run")
    bridge.current_run_id = active_run["run_id"]

    spawned_tiers = []
    orig_spawn = bridge.spawner.spawn_worker_pane
    async def track_spawn(tier_name, *args, **kwargs):
        spawned_tiers.append(tier_name)
        return await orig_spawn(tier_name, *args, **kwargs)
    bridge.spawner.spawn_worker_pane = track_spawn

    subtask = {"id": "t1", "description": "Auth module", "target_files": []}
    success = await bridge.execute_subtask(subtask)
    assert success is True

    # Default initial tier is luna
    assert len(spawned_tiers) == 1
    assert spawned_tiers[0] == "luna"

    from meister.logger import get_log_file
    log_file = Path(get_log_file())
    if log_file.exists():
        events = [json.loads(line) for line in log_file.read_text(encoding="utf-8").splitlines() if line.strip()]
        skip_events = [e for e in events if e.get("event") == "tier_skipped_breaker" or e.get("event_type") == "tier_skipped_breaker"]
        assert len(skip_events) == 0


@pytest.mark.asyncio
async def test_bridge_execute_plan_unscoped_serialized_disjoint_parallel(tmp_path):
    """(i) Duas tarefas sem escopo NÃO ficam simultâneas (máx 1); duas com escopo disjunto ficam (máx 2)."""
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
concurrency:
  parallel_tasks: true
  max_parallel_workers: 2
  layout_strategy: tiled
""")
    config = load_config(str(cfg_file))
    mock_client = AsyncMock()

    active_concurrent = 0
    max_observed_concurrent = 0

    async def mock_split(*args, **kwargs):
        nonlocal active_concurrent, max_observed_concurrent
        active_concurrent += 1
        if active_concurrent > max_observed_concurrent:
            max_observed_concurrent = active_concurrent
        auto_write_result(tmp_path)
        await asyncio.sleep(0.02)
        active_concurrent -= 1
        return "w1:p1"

    mock_client.split_pane.side_effect = mock_split
    mock_client.read_pane.return_value = "Done"

    bridge = HerdrEventBridge(config=config, client=mock_client)

    # 1. Duas tarefas sem escopo: devem rodar em lotes separados, concorrência máxima = 1
    unscoped_steps = [
        {"id": "u1", "description": "Unscoped 1", "target_files": [], "depends_on": []},
        {"id": "u2", "description": "Unscoped 2", "target_files": [], "depends_on": []},
    ]
    max_observed_concurrent = 0
    active_concurrent = 0
    success = await bridge.execute_plan(unscoped_steps)
    assert success is True
    assert max_observed_concurrent == 1
    assert mock_client.split_pane.call_count == 2

    # 2. Duas tarefas com escopos disjuntos: rodam no mesmo lote, concorrência máxima = 2
    mock_client.split_pane.reset_mock()
    disjoint_steps = [
        {"id": "d1", "description": "Disjoint 1", "target_files": ["a.py"], "depends_on": []},
        {"id": "d2", "description": "Disjoint 2", "target_files": ["b.py"], "depends_on": []},
    ]
    max_observed_concurrent = 0
    active_concurrent = 0
    success2 = await bridge.execute_plan(disjoint_steps)
    assert success2 is True
    assert max_observed_concurrent == 2
    assert mock_client.split_pane.call_count == 2


@pytest.mark.asyncio
async def test_bridge_handle_herdr_event_real_formats():
    """Verifica que bridge.handle_herdr_event processa formatos reais de pane_exited e pane_closed."""
    bridge = HerdrEventBridge()
    ev1 = asyncio.Event()
    bridge._exit_events["w9:pFM"] = ev1
    ev2 = asyncio.Event()
    bridge._exit_events["w9:pFG"] = ev2
    ev3 = asyncio.Event()
    bridge._exit_events["w9:p1"] = ev3

    # pane_exited REAL aciona _exit_events
    await bridge.handle_herdr_event({
        "data": {"pane_id": "w9:pFM", "type": "pane_exited", "workspace_id": "w9"},
        "event": "pane_exited",
    })
    assert ev1.is_set()

    # pane_closed REAL aciona _exit_events
    await bridge.handle_herdr_event({
        "data": {"pane_id": "w9:pFG", "type": "pane_closed", "workspace_id": "w9"},
        "event": "pane_closed",
    })
    assert ev2.is_set()

    # pane_created REAL NÃO aciona
    await bridge.handle_herdr_event({
        "data": {"pane": {"pane_id": "w9:p1", "workspace_id": "w9"}},
        "event": "pane_created",
    })
    assert not ev3.is_set()

    # pane de OUTRO id NÃO aciona
    ev4 = asyncio.Event()
    bridge._exit_events["w9:pOther"] = ev4
    await bridge.handle_herdr_event({
        "data": {"pane_id": "w9:pDifferent", "type": "pane_exited", "workspace_id": "w9"},
        "event": "pane_exited",
    })
    assert not ev4.is_set()

    # Formatos antigos continuam funcionando
    ev_old = asyncio.Event()
    bridge._exit_events["w1:pOld"] = ev_old
    await bridge.handle_herdr_event({
        "method": "pane.exited",
        "params": {"pane_id": "w1:pOld", "exit_code": 0}
    })
    assert ev_old.is_set()


@pytest.mark.asyncio
async def test_bridge_active_liveness_pane_missing_fails_fast(tmp_path, monkeypatch):
    """Checagem ativa: pane sumido (pane_exists is False) falha rápido sem escalar tier."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.05")

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.split_pane.return_value = "w1:p1"
    mock_client.read_pane.return_value = ""

    call_count = 0
    async def fake_pane_exists(pane_id):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            return False
        return True

    mock_client.pane_exists = AsyncMock(side_effect=fake_pane_exists)

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    subtask = {
        "id": "t1",
        "description": "Add feature",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    with patch("meister.herdr.bridge.log_event") as mock_log_event:
        t0 = time.monotonic()
        success = await bridge.execute_subtask(subtask)
        elapsed = time.monotonic() - t0

        assert success is False
        assert elapsed < 2.0
        assert mock_client.split_pane.call_count == 1

        err_calls = [
            kwargs for _, kwargs in mock_log_event.call_args_list
            if kwargs.get("status") == "infrastructure_error"
        ]
        assert len(err_calls) >= 1
        assert "Pane w1:p1 do worker desapareceu (tab/pane fechada?) sem gerar resultado (erro de infraestrutura)" in err_calls[0].get("error", "")


@pytest.mark.asyncio
async def test_bridge_active_liveness_pane_missing_but_result_exists(tmp_path, monkeypatch):
    """Variante: pane_exists devolve False mas o result_file já existe -> sucesso."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.05")

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.split_pane.return_value = "w1:p1"
    mock_client.read_pane.return_value = ""

    async def fake_pane_exists(pane_id):
        # Escreve o resultado antes ou no momento em que detecta sumiço
        auto_write_result(repo_dir)
        return False

    mock_client.pane_exists = AsyncMock(side_effect=fake_pane_exists)

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    subtask = {
        "id": "t1",
        "description": "Add feature",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is True


@pytest.mark.asyncio
async def test_bridge_active_liveness_pane_exists_success(tmp_path, monkeypatch):
    """Variante: pane_exists sempre True e resultado aparece -> sucesso normal."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.05")

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.split_pane.return_value = "w1:p1"
    mock_client.read_pane.return_value = ""
    mock_client.pane_exists = AsyncMock(return_value=True)

    async def write_later():
        await asyncio.sleep(0.3)
        auto_write_result(repo_dir)

    asyncio.create_task(write_later())

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    subtask = {
        "id": "t1",
        "description": "Add feature",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is True
    assert mock_client.pane_exists.call_count >= 1


@pytest.mark.asyncio
async def test_bridge_active_liveness_interval_zero_disabled(tmp_path, monkeypatch):
    """Variante: MEISTER_PANE_LIVENESS_INTERVAL=0 -> nenhuma chamada a pane_exists."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "app.py").write_text("print('hello')\n")
    subprocess.run(["git", "add", "app.py"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True)

    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0")

    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.split_pane.return_value = "w1:p1"
    mock_client.read_pane.return_value = ""
    mock_client.pane_exists = AsyncMock(return_value=True)

    async def write_later():
        await asyncio.sleep(0.05)
        auto_write_result(repo_dir)

    asyncio.create_task(write_later())

    cfg = MeisterConfig()
    cfg.concurrency.layout_strategy = "tiled"
    cfg.concurrency.isolation_mode = "none"
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    subtask = {
        "id": "t1",
        "description": "Add feature",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await bridge.execute_subtask(subtask)
    assert success is True
    assert mock_client.pane_exists.call_count == 0


def _make_router_bridge(
    tmp_path,
    mode="jev",
    tier_count=2,
    unavailable_cooldown_seconds=300,
    timeout_seconds=10,
    max_attempts=2,
):
    from meister.config import load_config
    from meister.state import StateManager

    tiers = [
        {"name": "copilot", "harness": "copilot", "model": "model-copilot"},
        {"name": "luna", "harness": "codex", "model": "model-luna"},
    ][:tier_count]
    config_file = tmp_path / f"{mode}_{tier_count}.yaml"
    config_file.write_text(
        "router:\n"
        f"  mode: {mode}\n"
        f"  unavailable_cooldown_seconds: {unavailable_cooldown_seconds}\n"
        f"  timeout_seconds: {timeout_seconds}\n"
        f"  max_attempts: {max_attempts}\n"
        "master:\n"
        "  model: test-jev\n"
        "workers:\n"
        "  tier_order:\n"
        + "".join(
            f"    - name: {tier['name']}\n"
            f"      harness: {tier['harness']}\n"
            f"      model: {tier['model']}\n"
            for tier in tiers
        )
        + "concurrency:\n"
        "  layout_strategy: tiled\n"
        "  isolation_mode: none\n"
    )
    config = load_config(str(config_file))
    client = AsyncMock()

    async def fake_split(*args, **kwargs):
        auto_write_result(tmp_path)
        return f"w1:p{client.split_pane.call_count}"

    client.split_pane.side_effect = fake_split
    client.read_pane.return_value = "Done"
    state_manager = StateManager(str(tmp_path / "state.db"))
    bridge = HerdrEventBridge(config=config, client=client, state_manager=state_manager)
    subtask = {
        "id": "route-task",
        "description": "Implement routed task",
        "target_files": ["src/routed.py"],
        "cwd": str(tmp_path),
    }
    return bridge, client, state_manager, subtask


def _track_spawned_tiers(bridge):
    spawned_tiers = []
    original_spawn = bridge.spawner.spawn_worker_pane

    async def track_spawn(tier_name, *args, **kwargs):
        spawned_tiers.append(tier_name)
        return await original_spawn(tier_name, *args, **kwargs)

    bridge.spawner.spawn_worker_pane = track_spawn
    return spawned_tiers


@pytest.mark.asyncio
async def test_bridge_with_empty_tier_order_returns_false(tmp_path):
    bridge, client, _, subtask = _make_router_bridge(tmp_path)
    bridge.config.workers.tier_order.clear()

    with patch("meister.herdr.bridge.logger.error") as mock_error:
        assert await bridge.execute_subtask(subtask) is False

    mock_error.assert_called_once()
    client.split_pane.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(("mode", "tier_count"), [("first", 2), ("jev", 1)])
async def test_bridge_does_not_classify_in_first_mode_or_single_tier(tmp_path, mode, tier_count):
    bridge, _, _, subtask = _make_router_bridge(tmp_path, mode=mode, tier_count=tier_count)
    spawned_tiers = _track_spawned_tiers(bridge)

    with patch(
        "meister.herdr.bridge.classify_task",
        side_effect=AssertionError("Jev must not be called"),
    ) as mock_classify:
        assert await bridge.execute_subtask(subtask) is True

    mock_classify.assert_not_called()
    assert spawned_tiers == ["copilot"]


@pytest.mark.asyncio
async def test_bridge_jev_routes_and_logs_decision(tmp_path):
    bridge, _, _, subtask = _make_router_bridge(tmp_path)
    spawned_tiers = _track_spawned_tiers(bridge)
    classify_result = {
        "classification": "MEDIUM",
        "classification_confidence": 0.88,
        "recommended_implementer": "luna",
        "implementer_confidence": 0.91,
        "fallback_rule_applied": False,
    }

    with patch("meister.herdr.bridge.classify_task", return_value=classify_result) as mock_classify, \
         patch("meister.herdr.bridge.log_event") as mock_log_event:
        assert await bridge.execute_subtask(subtask, run_id="route-run") is True

    mock_classify.assert_called_once()
    assert mock_classify.call_args.kwargs["model"] == "test-jev"
    assert mock_classify.call_args.kwargs["implementers"] == bridge.config.workers.tier_order
    assert mock_classify.call_args.kwargs["timeout"] == bridge.config.router.timeout_seconds
    assert mock_classify.call_args.kwargs["max_attempts"] == bridge.config.router.max_attempts
    assert spawned_tiers == ["luna"]
    assert bridge._jev_unavailable_until == 0.0
    route_events = [
        call.kwargs for call in mock_log_event.call_args_list
        if call.kwargs.get("event_type") == "route_decision"
    ]
    assert len(route_events) == 1
    assert route_events[0]["tier"] == "luna"
    assert route_events[0]["classification"] == "MEDIUM"
    assert route_events[0]["confidence"] == 0.88
    assert route_events[0]["fallback_rule_applied"] is False


@pytest.mark.asyncio
async def test_bridge_jev_exception_falls_back_to_first_tier(tmp_path):
    bridge, _, _, subtask = _make_router_bridge(tmp_path)
    spawned_tiers = _track_spawned_tiers(bridge)

    with patch("meister.herdr.bridge.classify_task", side_effect=RuntimeError("routing unavailable")), \
         patch("meister.herdr.bridge.log_event") as mock_log_event:
        assert await bridge.execute_subtask(subtask) is True

    assert spawned_tiers == ["copilot"]
    route_event = next(
        call.kwargs for call in mock_log_event.call_args_list
        if call.kwargs.get("event_type") == "route_decision"
    )
    assert route_event["fallback_rule_applied"] is True
    assert route_event["status"] == "fallback"
    assert route_event["error"] == "routing unavailable"
    assert bridge._jev_unavailable_until > time.monotonic()


@pytest.mark.asyncio
async def test_bridge_jev_failure_cools_down_then_retries(tmp_path):
    bridge, _, _, first_subtask = _make_router_bridge(
        tmp_path, unavailable_cooldown_seconds=0.05
    )
    spawned_tiers = _track_spawned_tiers(bridge)
    failed = {
        "classification": "SMALL",
        "recommended_implementer": "copilot",
        "fallback_rule_applied": True,
        "api_unavailable": True,
    }
    healthy = {
        "classification": "MEDIUM",
        "recommended_implementer": "luna",
        "fallback_rule_applied": False,
        "api_unavailable": False,
    }
    second_subtask = {**first_subtask, "id": "route-task-2"}
    third_subtask = {**first_subtask, "id": "route-task-3"}

    with patch("meister.herdr.bridge.classify_task", side_effect=[failed, healthy]) as mock_classify, \
         patch("meister.herdr.bridge.log_event") as mock_log_event:
        assert await bridge.execute_subtask(first_subtask) is True
        assert await bridge.execute_subtask(second_subtask) is True
        assert mock_classify.call_count == 1
        skipped = [
            call.kwargs for call in mock_log_event.call_args_list
            if call.kwargs.get("event_type") == "route_decision"
            and call.kwargs.get("status") == "skipped_unavailable"
        ]
        assert len(skipped) == 1
        assert skipped[0]["tier"] == "copilot"

        await asyncio.sleep(0.06)
        assert await bridge.execute_subtask(third_subtask) is True

    assert mock_classify.call_count == 2
    assert spawned_tiers == ["copilot", "copilot", "luna"]
    route_events = [
        call.kwargs for call in mock_log_event.call_args_list
        if call.kwargs.get("event_type") == "route_decision"
    ]
    assert [event.get("status") for event in route_events] == [
        "fallback", "skipped_unavailable", None
    ]


@pytest.mark.asyncio
async def test_bridge_jev_timeout_activates_cooldown(tmp_path):
    bridge, _, _, subtask = _make_router_bridge(
        tmp_path,
        unavailable_cooldown_seconds=1,
        timeout_seconds=0.001,
        max_attempts=1,
    )
    spawned_tiers = _track_spawned_tiers(bridge)

    def slow_classify(**_kwargs):
        time.sleep(0.1)
        return {"recommended_implementer": "luna", "api_unavailable": False}

    real_wait_for = asyncio.wait_for
    requested_timeouts = []

    async def fast_timeout(awaitable, timeout):
        requested_timeouts.append(timeout)
        if timeout == 5.001:
            timeout = 0.01
        return await real_wait_for(awaitable, timeout=timeout)

    with patch("meister.herdr.bridge.classify_task", side_effect=slow_classify) as mock_classify, \
         patch("meister.herdr.bridge.asyncio.wait_for", side_effect=fast_timeout), \
         patch("meister.herdr.bridge.log_event") as mock_log_event:
        assert await bridge.execute_subtask(subtask) is True
        assert await bridge.execute_subtask({**subtask, "id": "after-timeout"}) is True

    assert mock_classify.call_count == 1
    assert requested_timeouts == [5.001]
    assert spawned_tiers == ["copilot", "copilot"]
    route_events = [
        call.kwargs for call in mock_log_event.call_args_list
        if call.kwargs.get("event_type") == "route_decision"
    ]
    assert route_events[0]["status"] == "fallback"
    assert "timed out" in route_events[0]["error"].lower()
    assert route_events[1]["status"] == "skipped_unavailable"


@pytest.mark.asyncio
async def test_bridge_explicit_tier_and_running_assigned_tier_skip_jev(tmp_path):
    from meister.state import SubtaskState, compute_subtask_id

    bridge, _, state_manager, subtask = _make_router_bridge(tmp_path)
    spawned_tiers = _track_spawned_tiers(bridge)
    with patch("meister.herdr.bridge.classify_task", side_effect=AssertionError("must not classify")) as mock_classify:
        assert await bridge.execute_subtask(subtask, initial_tier="luna") is True
    mock_classify.assert_not_called()
    assert spawned_tiers == ["luna"]

    run_id = "resume-run"
    state_manager.create_or_get_run("resume", cwd=str(tmp_path), force_run_id=run_id)
    state_manager.add_subtasks(run_id, [subtask])
    subtask_id = compute_subtask_id(run_id, subtask["id"], subtask["description"])
    state_manager.transition_subtask(
        subtask_id,
        to_state=SubtaskState.RUNNING,
        assigned_tier="luna",
    )
    spawned_tiers.clear()
    with patch("meister.herdr.bridge.classify_task", side_effect=AssertionError("must reuse assignment")) as mock_classify:
        assert await bridge.execute_subtask(subtask, run_id=run_id) is True
    mock_classify.assert_not_called()
    assert spawned_tiers == ["luna"]


@pytest.mark.asyncio
async def test_bridge_jev_choice_with_open_breaker_uses_next_tier(tmp_path):
    bridge, _, _, subtask = _make_router_bridge(tmp_path)
    spawned_tiers = _track_spawned_tiers(bridge)
    next_tier = bridge.spawner.get_tier("luna")
    bridge.spawner.get_first_available_tier = lambda *_args, **_kwargs: next_tier
    classify_result = {
        "classification": "MEDIUM",
        "recommended_implementer": "copilot",
        "implementer_confidence": 0.9,
        "fallback_rule_applied": False,
    }

    with patch("meister.herdr.bridge.classify_task", return_value=classify_result), \
         patch("meister.herdr.bridge.log_event") as mock_log_event:
        assert await bridge.execute_subtask(subtask) is True

    assert spawned_tiers == ["luna"]
    assert any(
        call.kwargs.get("event_type") == "tier_skipped_breaker"
        for call in mock_log_event.call_args_list
    )


@pytest.mark.asyncio
async def test_bridge_classifies_parallel_subtasks_without_blocking_event_loop(tmp_path):
    bridge, _, _, _ = _make_router_bridge(tmp_path)
    spawned_tiers = _track_spawned_tiers(bridge)
    subtask_count = 3

    def slow_classify(**_kwargs):
        time.sleep(0.2)
        return {
            "classification": "MEDIUM",
            "recommended_implementer": "copilot",
            "implementer_confidence": 0.9,
            "fallback_rule_applied": False,
        }

    subtasks = [
        {
            "id": f"route-{index}",
            "description": f"Parallel route {index}",
            "target_files": [f"src/{index}.py"],
            "cwd": str(tmp_path),
        }
        for index in range(subtask_count)
    ]

    with patch("meister.herdr.bridge.classify_task", side_effect=slow_classify) as mock_classify:
        started = time.monotonic()
        results = await asyncio.gather(*(bridge.execute_subtask(task) for task in subtasks))
        elapsed = time.monotonic() - started

    assert results == [True, True, True]
    assert mock_classify.call_count == subtask_count
    assert elapsed < subtask_count * 0.2
    assert spawned_tiers == ["copilot"] * subtask_count
