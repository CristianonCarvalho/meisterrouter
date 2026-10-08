import os
import json
import io
import threading
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
from meister.logger import add_event_observer, remove_event_observer
from meister.progress import ProgressReporter


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


def _timeout_test_config(tmp_path, tier_names=("A",), idle=0.05, maximum=0, retries=0):
    tiers = "\n".join(
        f"    - name: {name}\n      harness: codex\n      model: {name}\n"
        for name in tier_names
    )
    cfg_file = tmp_path / "timeout_config.yaml"
    cfg_file.write_text(
        f"router:\n  mode: first\n"
        f"retry:\n  pane_lost_attempts: {retries}\n  pane_lost_backoff_seconds: 0\n"
        f"workers:\n  idle_timeout_seconds: {idle}\n  max_runtime_seconds: {maximum}\n"
        f"  tier_order:\n{tiers}"
        "concurrency:\n  parallel_tasks: false\n  max_parallel_workers: 1\n  layout_strategy: tiled\n  isolation_mode: none\n"
    )
    return load_config(str(cfg_file))


def _init_test_git_repo(repo_dir):
    repo_dir.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-b", "main"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo_dir, check=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo_dir, check=True)
    (repo_dir / "base.txt").write_text("base\n")
    subprocess.run(["git", "add", "base.txt"], cwd=repo_dir, check=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=repo_dir, check=True, capture_output=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_dir, check=True, capture_output=True, text=True
    ).stdout.strip()


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
async def test_bridge_plan_parsed_logs_task_titles(tmp_path, monkeypatch):
    from meister.herdr.dag import SubtaskNode

    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    bridge = HerdrEventBridge(config=load_config(None), client=AsyncMock())
    bridge.current_run_id = "titles-run"
    steps = [
        {"id": "t1", "description": "\n  Criar rota /oauth/callback\nDetalhes longos", "depends_on": []},
        SubtaskNode(id="t2", description="x" * 300),
        {"id": "t3", "description": "", "depends_on": []},
    ]

    with patch("meister.herdr.bridge.log_event") as mock_log_event, \
         patch.object(bridge, "execute_parallel_batch", new=AsyncMock(return_value=True)):
        assert await bridge.execute_plan(steps) is True

    plan = next(
        call.kwargs for call in mock_log_event.call_args_list
        if call.kwargs.get("event_type") == "plan_parsed"
    )
    assert plan["task_ids"] == ["t1", "t2", "t3"]
    assert plan["task_titles"]["t1"] == "Criar rota /oauth/callback"
    assert plan["task_titles"]["t2"] == "x" * 119 + "…"
    assert plan["task_titles"]["t3"] == ""


@pytest.mark.asyncio
async def test_bridge_execute_plan_with_dag_batches(tmp_path, monkeypatch):
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
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
    bridge.current_run_id = "bridge-progress-run"
    steps = [
        {"id": "t1", "description": "Backend API", "target_files": ["api.py"], "depends_on": []},
        {"id": "t2", "description": "Frontend UI", "target_files": ["app.tsx"], "depends_on": []},
        {"id": "t3", "description": "Integration test", "target_files": ["test_integ.py"], "depends_on": ["t1", "t2"]},
    ]

    stream = io.StringIO()
    reporter = ProgressReporter(stream=stream)
    add_event_observer(reporter.on_event)
    try:
        success = await bridge.execute_plan(steps)
    finally:
        remove_event_observer(reporter.on_event)
    assert success is True
    assert mock_client.split_pane.call_count == 3
    progress_lines = stream.getvalue().splitlines()
    assert progress_lines[0] == "Plano: 3 tarefas em 2 lotes (run bridge-p)"
    for index, task_id in enumerate(("t1", "t2", "t3"), 1):
        assert any(line.startswith(f"[{index}/3] {task_id} iniciada em ") for line in progress_lines)
        assert any(line.startswith(f"[{index}/3] {task_id} concluida em ") for line in progress_lines)


@pytest.mark.asyncio
async def test_bridge_run_orchestration_cycle(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    _init_test_git_repo(repo_dir)
    monkeypatch.chdir(repo_dir)
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
async def test_bridge_run_orchestration_cycle_gate_failure(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    _init_test_git_repo(repo_dir)
    monkeypatch.chdir(repo_dir)
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
async def test_bridge_run_orchestration_cycle_with_direct_task(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    _init_test_git_repo(repo_dir)
    monkeypatch.chdir(repo_dir)
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
async def test_bridge_run_orchestration_cycle_autodetects_pane_and_workspace(tmp_path, monkeypatch):
    repo_dir = tmp_path / "repo"
    _init_test_git_repo(repo_dir)
    monkeypatch.chdir(repo_dir)
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
        assert data["model"] == "tier_1"
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

    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        success = await bridge.execute_subtask(subtask)
    assert success is False
    # Must have failed fast on the very first attempt without cascading through luna->gemini->haiku->sonnet
    assert mock_client.split_pane.call_count == 1
    assert not any(
        call.kwargs.get("event_type") == "worker_retry"
        for call in log_event_mock.call_args_list
    )


@pytest.mark.asyncio
async def test_bridge_idle_timeout_is_not_reported_as_pane_loss(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.01")
    config = _timeout_test_config(tmp_path, idle=0.02, retries=0)
    client = AsyncMock()
    client.read_pane.return_value = "unchanged"
    bridge = HerdrEventBridge(config=config, client=client)

    async def never_finishes(tier_name, task_context=None, **kwargs):
        return "pane-timeout", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = never_finishes
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask({"id": "idle", "description": "idle worker", "cwd": str(tmp_path)}),
            timeout=3,
        )

    assert result is False
    events = [call.kwargs for call in log_event_mock.call_args_list]
    timeout_event = next(event for event in events if event.get("event_type") == "worker_timeout")
    assert timeout_event["kind"] == "idle"
    assert timeout_event["seconds"] == 0.02
    assert timeout_event["action"] == "falhando sem trabalho aproveitavel"
    assert "timeout por inatividade" in timeout_event["message"]
    assert not any(
        event.get("event_type") == "worker_error"
        and "encerrou prematuramente" in event.get("error", "")
        for event in events
    )
    assert not any(event.get("event_type") == "harness_reaped" for event in events)
    client.send_interrupt.assert_awaited_once_with("pane-timeout")


@pytest.mark.asyncio
@pytest.mark.parametrize("activity", ["worktree", "pane"])
async def test_bridge_worktree_or_pane_activity_resets_idle_timeout(
    tmp_path, monkeypatch, activity
):
    """Atividade continua (por contagem de leituras, nao por relogio) impede o timeout por inatividade.

    O loop do bridge dorme 0,2 s por iteracao. Sem reset, o idle (0,8 s) dispararia por volta da 4a leitura;
    com reset, so dispara depois que a atividade cessa (leitura > active_polls). A prova e a contagem de
    leituras no instante do timeout, nao o relogio.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.01")
    repo_dir = tmp_path / "repo"
    _init_test_git_repo(repo_dir)
    config = _timeout_test_config(tmp_path, idle=0.8, retries=0)
    client = AsyncMock()
    active_polls = 12
    reads = []
    reads_at_timeout = []

    def read_pane(_pane):
        reads.append(1)
        n = len(reads)
        if n <= active_polls and activity == "worktree":
            (repo_dir / f"worker-output-{n}.txt").write_text("activity\n")
        if activity == "pane":
            return f"tick-{min(n, active_polls)}"
        return "unchanged"

    client.read_pane.side_effect = read_pane
    client.send_interrupt.side_effect = lambda _pane: reads_at_timeout.append(len(reads))
    bridge = HerdrEventBridge(config=config, client=client)

    async def never_finishes(tier_name, task_context=None, **kwargs):
        return "pane-activity", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = never_finishes
    with patch("meister.herdr.bridge.log_event"):
        result = await asyncio.wait_for(
            bridge.execute_subtask(
                {"id": f"activity-{activity}", "description": "activity", "cwd": str(repo_dir)}
            ),
            timeout=30,
        )

    assert result is False
    assert reads_at_timeout
    assert reads_at_timeout[0] >= active_polls


@pytest.mark.asyncio
async def test_bridge_max_runtime_timeout_fires_despite_activity_and_idle_can_be_disabled(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.01")
    # idle folgado: o teto (0,12 s) tem de disparar primeiro mesmo se o runner pausar entre leituras
    config = _timeout_test_config(tmp_path, idle=5.0, maximum=0.12, retries=0)
    client = AsyncMock()
    client.read_pane.side_effect = lambda _pane: str(time.monotonic())
    bridge = HerdrEventBridge(config=config, client=client)

    async def never_finishes(tier_name, task_context=None, **kwargs):
        return "pane-runtime", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = never_finishes
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask(
                {"id": "runtime", "description": "continuous activity", "cwd": str(tmp_path)}
            ),
            timeout=3,
        )

    assert result is False
    timeout_event = next(
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "worker_timeout"
    )
    assert timeout_event["kind"] == "max_runtime"
    assert timeout_event["seconds"] == 0.12


@pytest.mark.asyncio
async def test_bridge_zero_idle_timeout_disables_idle_protection(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.01")
    config = _timeout_test_config(tmp_path, idle=0, maximum=0.12, retries=0)
    client = AsyncMock()
    client.read_pane.return_value = "unchanged"
    bridge = HerdrEventBridge(config=config, client=client)

    async def never_finishes(tier_name, task_context=None, **kwargs):
        return "pane-idle-disabled", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = never_finishes
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        assert not await asyncio.wait_for(
            bridge.execute_subtask({"id": "idle-disabled", "description": "runtime remains capped"}),
            timeout=3,
        )

    timeout_event = next(
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "worker_timeout"
    )
    assert timeout_event["kind"] == "max_runtime"


@pytest.mark.asyncio
async def test_bridge_zero_max_runtime_disables_runtime_ceiling(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.01")
    # idle folgado (0,8 s, como no teste vizinho, e nao 30 ms): sob carga o laco pode atrasar mais que 30 ms entre duas leituras e o
    # timeout por inatividade disparava antes da atividade registrada. O fim do worker e por contagem de leituras
    # (atividade continua), nao por relogio; o que o teste prova e que max_runtime=0 nao vira teto imediato.
    config = _timeout_test_config(tmp_path, idle=0.8, maximum=0, retries=0)
    client = AsyncMock()
    reads = []

    def read_pane(_pane):
        reads.append(1)
        return f"tick-{len(reads)}"

    client.read_pane.side_effect = read_pane
    bridge = HerdrEventBridge(config=config, client=client)

    async def finish_worker(tier_name, task_context=None, **kwargs):
        async def write_result_after_polls():
            while len(reads) < 4:  # o laco do bridge le o painel a cada ~0,2 s
                await asyncio.sleep(0.005)
            write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})

        asyncio.get_running_loop().create_task(write_result_after_polls())
        return "pane-no-runtime-limit", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = finish_worker
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        assert await asyncio.wait_for(
            bridge.execute_subtask({"id": "no-runtime-limit", "description": "finish", "cwd": str(tmp_path)}),
            timeout=3,
        )
    assert not any(
        call.kwargs.get("event_type") == "worker_timeout"
        for call in log_event_mock.call_args_list
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("integration_result, expected", [((True, ""), True), ((False, "Portão determinístico falhou"), False)])
@pytest.mark.parametrize("worker_commits", [False, True], ids=["dirty-worktree", "new-commit"])
async def test_bridge_timeout_salvages_dirty_or_committed_work_through_integration(
    tmp_path, monkeypatch, integration_result, expected, worker_commits
):
    from types import SimpleNamespace
    from meister.state import RunState, StateManager, SubtaskState

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.01")
    repo_dir = tmp_path / "repo"
    base_commit = _init_test_git_repo(repo_dir)
    worktree = SimpleNamespace(
        task_id="salvage-worktree",
        worktree_path=str(repo_dir),
        base_commit=base_commit,
    )
    worktree_manager = MagicMock()
    worktree_manager.create_worktree.return_value = worktree
    pipeline = SimpleNamespace(
        integration_info=SimpleNamespace(branch_name="main"),
        wt_mgr=worktree_manager,
        prepare_subtask=MagicMock(return_value=object()),
        merge_prepared=MagicMock(return_value=integration_result),
        last_integrated_sha=None,
    )
    state = StateManager(":memory:")
    run_id = "timeout-salvage-run"
    state.create_or_get_run("timeout salvage", force_run_id=run_id)
    state.transition_run(run_id, RunState.RUNNING)
    state.add_subtasks(run_id, [
        {
            "id": "salvage",
            "description": "salvage work",
            "target_files": ["salvaged.txt"],
            "depends_on": [],
        }
    ])
    config = _timeout_test_config(tmp_path, idle=0.02, retries=0)
    config.gate.repair_attempts = 0
    client = AsyncMock()
    client.read_pane.return_value = "idle"
    bridge = HerdrEventBridge(config=config, client=client, state_manager=state)
    bridge.current_run_id = run_id
    bridge._integration_pipeline = pipeline

    async def commit_worker_output(tier_name, task_context=None, **kwargs):
        (repo_dir / "salvaged.txt").write_text("completed work\n")
        if worker_commits:
            subprocess.run(["git", "add", "salvaged.txt"], cwd=repo_dir, check=True)
            subprocess.run(
                ["git", "commit", "-m", "worker completed before timeout"],
                cwd=repo_dir,
                check=True,
                capture_output=True,
            )
        return "pane-salvage", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = commit_worker_output
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        result = await bridge.execute_subtask(
            {
                "id": "salvage",
                "description": "salvage work",
                "target_files": ["salvaged.txt"],
                "cwd": str(repo_dir),
            }
        )

    pipeline.prepare_subtask.assert_called_once()
    pipeline.merge_prepared.assert_called_once()
    events = [call.kwargs for call in log_event_mock.call_args_list]
    assert any(event.get("event_type") == "worker_timeout_salvaged" for event in events)
    if expected:
        assert result is True
        row = state.get_subtask(compute_subtask_id(run_id, "salvage", "salvage work"))
        assert row["status"] == SubtaskState.COMPLETED.value
    else:
        assert result is False
        rejected = next(
            event for event in events if event.get("event_type") == "subtask_rejected"
        )
        assert rejected["reason"] == "gate"
        row = state.get_subtask(compute_subtask_id(run_id, "salvage", "salvage work"))
        assert row["status"] == SubtaskState.FAILED.value


@pytest.mark.asyncio
async def test_bridge_timeout_retries_same_tier_then_escalates_and_fails_with_timeout(
    tmp_path, monkeypatch
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.01")
    config = _timeout_test_config(tmp_path, tier_names=("A", "B"), idle=0.02, retries=1)
    client = AsyncMock()
    client.read_pane.return_value = "unchanged"
    bridge = HerdrEventBridge(config=config, client=client)
    spawns = []

    async def never_finishes(tier_name, task_context=None, **kwargs):
        spawns.append(tier_name)
        return f"pane-{len(spawns)}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = never_finishes
    with patch("meister.herdr.bridge.log_event") as log_event_mock, patch.object(
        bridge.get_state_manager(), "record_harness_failure"
    ) as record_failure:
        result = await asyncio.wait_for(
            bridge.execute_subtask({"id": "timeout-escalation", "description": "timeouts only"}),
            timeout=5,
        )

    assert result is False
    assert spawns == ["A", "A", "B"]
    retry = next(
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "worker_retry"
    )
    assert retry["reason"] == "timeout"
    assert retry["tier"] == "A"
    assert record_failure.call_count == 0
    final_error = next(
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "worker_error"
    )
    assert final_error["status"] == "timeout"
    assert "timeout por inatividade" in final_error["error"]
    assert "encerrou prematuramente" not in final_error["error"]


@pytest.mark.asyncio
async def test_bridge_passes_worker_timeout_as_runtime_plus_sixty(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = _timeout_test_config(tmp_path, idle=0, maximum=4, retries=0)
    client = AsyncMock()
    bridge = HerdrEventBridge(config=config, client=client)
    payload_timeouts = []

    async def finish_worker(tier_name, task_context=None, **kwargs):
        payload_timeouts.append(json.loads(Path(task_context["task_file"]).read_text())["timeout"])
        write_atomic_json(
            task_context["result_file"],
            {"status": "done", "modified_files": []},
        )
        return "pane-timeout-value", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = finish_worker
    assert await asyncio.wait_for(
        bridge.execute_subtask({"id": "timeout-value", "description": "payload timeout"}),
        timeout=3,
    )
    assert payload_timeouts == [64]


@pytest.mark.asyncio
@pytest.mark.parametrize("trigger", ["liveness", "pane_exit"])
async def test_bridge_retries_lost_pane_on_same_tier_and_worktree(
    tmp_path, monkeypatch, trigger
):
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.001")
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
    mock_client.read_pane.return_value = ""
    config = MeisterConfig()
    config.concurrency.layout_strategy = "tiled"
    config.concurrency.isolation_mode = "none"
    config.workers.tier_order[0].max_parallel = 1
    config.retry.pane_lost_backoff_seconds = 0
    bridge = HerdrEventBridge(config=config, client=mock_client)
    worktree_path = tmp_path / "worker-worktree"
    worktree_path.mkdir()
    worktree = SimpleNamespace(task_id="lost-pane-worktree", worktree_path=str(worktree_path))
    worktree_manager = MagicMock()
    worktree_manager.create_worktree.return_value = worktree
    pipeline = SimpleNamespace(
        integration_info=SimpleNamespace(branch_name="main"),
        wt_mgr=worktree_manager,
        prepare_subtask=MagicMock(return_value=object()),
        merge_prepared=MagicMock(return_value=(True, "")),
        last_integrated_sha=None,
    )
    bridge._integration_pipeline = pipeline
    spawn_calls = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawn_calls.append((tier_name, kwargs.get("cwd")))
        pane_id = f"w1:p{len(spawn_calls)}"
        if len(spawn_calls) == 2:
            assert (worktree_path / "partial.txt").exists()
            write_atomic_json(
                task_context["result_file"],
                {"status": "done", "modified_files": []},
            )
        else:
            (worktree_path / "partial.txt").write_text("partial worker output")
            if trigger == "pane_exit":
                asyncio.create_task(
                    bridge.handle_herdr_event(
                        {
                            "method": "pane.exited",
                            "params": {"pane_id": pane_id, "exit_code": 1},
                        }
                    )
                )
        return pane_id, bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    if trigger == "liveness":
        mock_client.pane_exists.return_value = False

    with patch("meister.herdr.bridge.log_event") as log_event_mock, patch.object(
        bridge.get_state_manager(), "record_harness_failure"
    ) as record_failure:
        result = await asyncio.wait_for(
            bridge.execute_subtask(
                {
                    "id": "lost-pane",
                    "description": "Recover after pane loss",
                    "target_files": ["app.py"],
                    "cwd": str(repo_dir),
                    "timeout": 3,
                }
            ),
            timeout=5,
        )

    assert result is True
    assert spawn_calls == [
        ("tier_1", str(worktree_path)),
        ("tier_1", str(worktree_path)),
    ]
    worktree_manager.create_worktree.assert_called_once()
    pipeline.prepare_subtask.assert_called_once()
    pipeline.merge_prepared.assert_called_once()
    retry_event = next(
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "worker_retry"
    )
    assert retry_event["reason"] == "pane_lost"
    assert retry_event["retry"] == 1
    assert retry_event["backoff_seconds"] == 0
    assert retry_event["attempt"] == 1
    record_failure.assert_not_called()
    assert bridge._quota_events == {}
    assert bridge._exit_events == {}
    assert bridge.active_workers == {}
    assert bridge._tier_active["tier_1"] == 0
    assert bridge._held_slots == {}


@pytest.mark.asyncio
async def test_bridge_fails_after_pane_lost_retries_without_escalation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.001")
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
    mock_client.read_pane.return_value = ""
    mock_client.pane_exists.return_value = False
    config = MeisterConfig()
    config.concurrency.layout_strategy = "tiled"
    config.concurrency.isolation_mode = "none"
    config.retry.pane_lost_backoff_seconds = 0
    bridge = HerdrEventBridge(config=config, client=mock_client)
    spawns = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawns.append(tier_name)
        return f"w1:p{len(spawns)}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    with patch("meister.herdr.bridge.log_event") as log_event_mock, patch.object(
        bridge.get_state_manager(), "record_harness_failure"
    ) as record_failure:
        result = await asyncio.wait_for(
            bridge.execute_subtask(
                {
                    "id": "lost-pane-exhausted",
                    "description": "Fail after pane loss retries",
                    "target_files": ["app.py"],
                    "cwd": str(repo_dir),
                    "timeout": 3,
                }
            ),
            timeout=5,
        )

    assert result is False
    assert spawns == ["tier_1", "tier_1"]
    retry_events = [
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "worker_retry"
    ]
    assert len(retry_events) == 1
    errors = [
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "worker_error"
    ]
    assert len(errors) == 1
    assert "retentativas esgotadas: 1" in errors[0]["error"]
    record_failure.assert_not_called()


@pytest.mark.asyncio
async def test_bridge_tracks_pane_lost_retry_limit_per_subtask(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.001")
    mock_client = AsyncMock()
    mock_client.is_connected = True
    mock_client.read_pane.return_value = ""
    mock_client.pane_exists.return_value = False
    config = MeisterConfig()
    config.concurrency.layout_strategy = "tiled"
    config.concurrency.isolation_mode = "none"
    config.retry.pane_lost_backoff_seconds = 0
    bridge = HerdrEventBridge(config=config, client=mock_client)
    spawns = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawns.append(tier_name)
        if len(spawns) % 2 == 0:
            write_atomic_json(
                task_context["result_file"],
                {"status": "done", "modified_files": []},
            )
        return f"w1:p{len(spawns)}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        for task_id in ("first", "second"):
            assert await asyncio.wait_for(
                bridge.execute_subtask(
                    {
                        "id": task_id,
                        "description": f"Task {task_id}",
                        "cwd": str(tmp_path),
                        "timeout": 3,
                    }
                ),
                timeout=5,
            )

    assert spawns == ["tier_1"] * 4
    retry_events = [
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "worker_retry"
    ]
    assert [(event["task_id"], event["retry"]) for event in retry_events] == [
        ("first", 1),
        ("second", 1),
    ]


@pytest.mark.asyncio
async def test_bridge_pane_lost_retry_budget_survives_tier_escalation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_PANE_LIVENESS_INTERVAL", "0.001")
    mock_client = AsyncMock()
    mock_client.is_connected = True
    # p1 (via A) e p3 (via B) somem; p2 (via A, apos a retentativa) termina com erro de cota.
    mock_client.pane_exists.side_effect = lambda pane_id: pane_id == "w1:p2"
    mock_client.read_pane.side_effect = (
        lambda pane_id: "Error 429: Rate limit exceeded" if pane_id == "w1:p2" else ""
    )
    config = MeisterConfig()
    config.concurrency.layout_strategy = "tiled"
    config.concurrency.isolation_mode = "none"
    config.retry.pane_lost_attempts = 1
    config.retry.pane_lost_backoff_seconds = 0
    bridge = HerdrEventBridge(config=config, client=mock_client)
    spawns = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawns.append(tier_name)
        pane_id = f"w1:p{len(spawns)}"
        if pane_id == "w1:p2":
            write_atomic_json(
                task_context["result_file"],
                {"status": "done", "modified_files": []},
            )
        return pane_id, bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = fake_spawn
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        result = await asyncio.wait_for(
            bridge.execute_subtask(
                {
                    "id": "budget",
                    "description": "Retry budget is per subtask",
                    "cwd": str(tmp_path),
                    "timeout": 3,
                }
            ),
            timeout=8,
        )

    # A retentativa ja foi gasta na via A: a via B nao ganha outra.
    assert result is False
    assert len(spawns) == 3
    assert spawns[0] == spawns[1] != spawns[2]
    retry_events = [
        call.kwargs for call in log_event_mock.call_args_list
        if call.kwargs.get("event_type") == "worker_retry"
    ]
    assert len(retry_events) == 1


@pytest.mark.asyncio
async def test_bridge_does_not_retry_gate_infrastructure_error(tmp_path, monkeypatch):
    from types import SimpleNamespace

    monkeypatch.chdir(tmp_path)
    worktree_path = tmp_path / "worker-worktree"
    worktree_path.mkdir()
    worktree = SimpleNamespace(task_id="gate-error-worktree", worktree_path=str(worktree_path))
    worktree_manager = MagicMock()
    worktree_manager.create_worktree.return_value = worktree
    pipeline = SimpleNamespace(
        integration_info=SimpleNamespace(branch_name="main"),
        wt_mgr=worktree_manager,
        prepare_subtask=MagicMock(return_value=object()),
        merge_prepared=MagicMock(
            return_value=(False, "ERRO DE INFRAESTRUTURA no portão: gate runner indisponível")
        ),
        last_integrated_sha=None,
    )
    mock_client = AsyncMock()
    mock_client.is_connected = True
    config = MeisterConfig()
    config.retry.pane_lost_backoff_seconds = 0
    bridge = HerdrEventBridge(config=config, client=mock_client)
    bridge._integration_pipeline = pipeline
    spawns = []

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        spawns.append(tier_name)
        write_atomic_json(
            task_context["result_file"],
            {"status": "done", "modified_files": []},
        )
        return ("w1:t1", "w1:p1", None)

    bridge.spawner.spawn_worker_tab = fake_spawn
    with patch("meister.herdr.bridge.log_event") as log_event_mock:
        result = await bridge.execute_subtask(
            {
                "id": "gate-infrastructure-error",
                "description": "Do not retry deterministic gate infrastructure failures",
                "target_files": ["app.py"],
                "cwd": str(tmp_path),
            }
        )

    assert result is False
    assert spawns == ["tier_1"]
    assert not any(
        call.kwargs.get("event_type") == "worker_retry"
        for call in log_event_mock.call_args_list
    )
    pipeline.prepare_subtask.assert_called_once()
    assert pipeline.merge_prepared.call_count == 1


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
    cfg.retry.pane_lost_attempts = 0
    # Limites curtos: se o evento se perdesse, o teste falha em segundos em vez de ficar pendurado
    # (os padroes sao 600 s de inatividade e 3600 s de teto).
    cfg.workers.idle_timeout_seconds = 20
    cfg.workers.max_runtime_seconds = 30
    bridge = HerdrEventBridge(config=cfg, client=mock_client)

    async def simulate_exit_event():
        # Espera o bridge registrar o pane: um evento enviado antes do registro e descartado
        # (corrida vista no CI do Python 3.10).
        for _ in range(500):
            if "w1:p1" in bridge._exit_events:
                break
            await asyncio.sleep(0.01)
        # Push pane.exited event
        await bridge.handle_herdr_event({
            "method": "pane.exited",
            "params": {"pane_id": "w1:p1", "exit_code": 1}
        })

    exit_task = asyncio.create_task(simulate_exit_event())

    subtask = {
        "id": "t1",
        "description": "Add feature",
        "target_files": ["app.py"],
        "cwd": str(repo_dir),
    }

    success = await asyncio.wait_for(bridge.execute_subtask(subtask), timeout=60)
    await exit_task
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
        auto_write_result(
            tmp_path,
            {
                "status": "done",
                "modified_files": [],
                "usage": {
                    "tokens_in": 123,
                    "tokens_out": 45,
                    "tokens_total": 168,
                    "credits": 0.15,
                    "cost": 0.0005,
                    "cost_source": "estimated",
                    "approx": True,
                },
            },
        )
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

    events = []
    add_event_observer(events.append)
    phase_start = time.monotonic()
    try:
        success = await bridge.execute_subtask(subtask, run_id="run_thread_test")
    finally:
        phase_elapsed_ms = (time.monotonic() - phase_start) * 1000.0
        remove_event_observer(events.append)
    assert success is True

    # create_worktree, prepare_subtask, merge_prepared, and cleanup_worktree use asyncio.to_thread.
    assert "create_worktree" in dispatched_targets
    assert "prepare_subtask" in dispatched_targets
    assert "merge_prepared" in dispatched_targets
    assert "cleanup_worktree" in dispatched_targets
    phases = [event for event in events if event.get("event") == "worker_phase"]
    assert [event["phase"] for event in phases].count("worker") == 1
    assert [event["phase"] for event in phases].count("integrate") == 1
    assert [event["phase"] for event in phases].count("lock_wait") == 1
    gate_phases = [event for event in phases if event["phase"] == "gate"]
    assert gate_phases and all(event["duration_ms"] >= 0 for event in gate_phases)
    assert sum(event["duration_ms"] for event in phases) <= phase_elapsed_ms + 1000
    assert all(event["task_id"] == "t1" for event in phases)

    completed = next(event for event in events if event.get("event") == "subtask_completed")
    assert completed["cost"] == 0.0005
    assert completed["cost_source"] == "estimated"
    assert (completed["tokens_in"], completed["tokens_out"], completed["tokens_total"]) == (123, 45, 168)
    assert completed["credits"] == 0.15
    assert completed["approx"] is True


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
    bridge.config.gate.repair_attempts = 0
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
    monkeypatch.chdir(repo_dir)

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
    monkeypatch.chdir(repo_dir)

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
    cfg.retry.pane_lost_attempts = 0
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
        assert not any(
            kwargs.get("event_type") == "worker_retry"
            for _, kwargs in mock_log_event.call_args_list
        )


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
        # o resultado só aparece depois da 1ª sondagem de liveness (com um prazo largo): sob carga o setup do
        # worktree pode levar mais que qualquer espera fixa, e o resultado já existiria antes da primeira sondagem
        for _ in range(600):
            if mock_client.pane_exists.call_count >= 1:
                break
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


@pytest.mark.asyncio
async def test_bridge_prepares_concurrently_merges_serially_and_keeps_task_shas(tmp_path):
    from types import SimpleNamespace
    from meister.state import RunState, SubtaskState

    bridge, _, state, template = _make_router_bridge(tmp_path, mode="first", tier_count=1)
    run_id = "parallel-prepare-run"
    state.create_or_get_run("parallel prepare", force_run_id=run_id)
    state.transition_run(run_id, RunState.RUNNING)
    subtasks = [
        {**template, "id": task_id, "description": f"task {task_id}"}
        for task_id in ("task-a", "task-b")
    ]
    state.add_subtasks(run_id, subtasks)

    prepare_barrier = threading.Barrier(2, timeout=30)
    cleanup_barrier = threading.Barrier(2, timeout=30)
    merge_guard = threading.Lock()
    phase_durations = {}

    class FakePipeline:
        integration_info = SimpleNamespace(branch_name="main")
        last_integrated_sha = None

        def __init__(self):
            self.max_merge_concurrency = 0
            self.active_merges = 0
            self.wt_mgr = SimpleNamespace(
                create_worktree=self.create_worktree,
                cleanup_worktree=self.cleanup_worktree,
            )

        def create_worktree(self, task_id, base_ref):
            path = tmp_path / task_id
            path.mkdir()
            return SimpleNamespace(task_id=task_id, worktree_path=str(path), base_commit="base")

        def prepare_subtask(self, subtask_wt, **kwargs):
            started = time.monotonic()
            try:
                prepare_barrier.wait()
                return subtask_wt.task_id, f"sha-{kwargs['task_id']}"
            finally:
                with merge_guard:
                    phase_durations.setdefault(kwargs["task_id"], {})["prepare"] = time.monotonic() - started

        def merge_prepared(self, prepared):
            started = time.monotonic()
            with merge_guard:
                self.active_merges += 1
                self.max_merge_concurrency = max(self.max_merge_concurrency, self.active_merges)
            try:
                time.sleep(0.6)  # longo o bastante para que o tempo de fila (lock_wait) se distinga do ruído de carga
                self.last_integrated_sha = prepared[1]
                return True, ""
            finally:
                with merge_guard:
                    phase_durations.setdefault(prepared[1].removeprefix("sha-"), {})["merge"] = (
                        time.monotonic() - started
                    )
                with merge_guard:
                    self.active_merges -= 1

        def cleanup_worktree(self, task_id, **kwargs):
            cleanup_barrier.wait()

    pipeline = FakePipeline()
    bridge._integration_pipeline = pipeline

    async def finish_worker(tier_name, task_context=None, **kwargs):
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return f"worker-{task_context['id']}", bridge.spawner.get_tier(tier_name)

    bridge.spawner.spawn_worker_pane = finish_worker
    events = []
    add_event_observer(events.append)
    try:
        results = await asyncio.wait_for(
            asyncio.gather(*(bridge.execute_subtask(item, run_id=run_id) for item in subtasks)),
            timeout=10,
        )
    finally:
        remove_event_observer(events.append)

    assert results == [True, True]
    assert pipeline.max_merge_concurrency == 1
    phase_events = [
        event for event in events
        if event.get("event") == "worker_phase" and event.get("phase") in {"integrate", "lock_wait"}
    ]
    assert sum(event["phase"] == "lock_wait" for event in phase_events) == len(subtasks)
    assert sum(event["phase"] == "integrate" for event in phase_events) == len(subtasks)
    for item in subtasks:
        subtask_id = compute_subtask_id(run_id, item["id"], item["description"])
        row = state.get_subtask(subtask_id)
        assert row["status"] == SubtaskState.COMPLETED.value
        assert row["integrated_sha"] == f"sha-{item['id']}"
        task_phases = {
            event["phase"]: event["duration_ms"]
            for event in phase_events
            if event.get("task_id") == item["id"]
        }
        measured = phase_durations[item["id"]]
        assert task_phases["integrate"] >= (measured["prepare"] + measured["merge"]) * 1000
        # overhead de agendamento sob carga fica na casa de 100-150 ms; a fila (lock_wait) seria >= 600 ms
        assert task_phases["integrate"] - (measured["prepare"] + measured["merge"]) * 1000 < 400
    assert max(
        event["duration_ms"] for event in phase_events if event["phase"] == "lock_wait"
    ) >= 300


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
async def test_bridge_classifies_structured_task_context_with_configured_limit(tmp_path):
    from meister.jev_context import build_jev_context

    bridge, _, _, subtask = _make_router_bridge(tmp_path)
    bridge.config.router.context_max_chars = 600
    subtask.update(
        {
            "description": (
                "Task 3: Implement a focused change\n\n"
                "## Global Constraints\n"
                + ("security auth migration " * 100)
                + "\n\nArquivos permitidos: src/auth.py\n\n"
                + ("Implement the task body. " * 100)
            ),
            "target_files": ["src/auth.py"],
            "depends_on": ["task_1", "task_2"],
        }
    )
    expected = build_jev_context(subtask, 600)
    result = {
        "classification": "MEDIUM",
        "recommended_implementer": "luna",
        "fallback_rule_applied": False,
    }

    with patch("meister.herdr.bridge.classify_task", return_value=result) as mock_classify:
        assert await bridge.execute_subtask(subtask, run_id="structured-context-run") is True

    context = mock_classify.call_args.kwargs["context"]
    assert context == expected
    assert len(context) <= 600
    assert "Tarefa: Task 3: Implement a focused change" in context
    assert "Arquivos (1): src/auth.py" in context
    assert "Depende de: task_1, task_2" in context
    assert "security auth migration" not in context
    assert "Implement the task body." in context


@pytest.mark.asyncio
async def test_bridge_does_not_cut_the_structured_context_at_the_old_2000_chars(tmp_path):
    """Com o limite padrao (4000), um contexto de ~3000 caracteres chega inteiro ao Jev (o corte antigo era 2000)."""
    from meister.jev_context import build_jev_context

    bridge, _, _, subtask = _make_router_bridge(tmp_path)
    assert bridge.config.router.context_max_chars == 4000
    subtask.update(
        {
            "description": (
                "Task 3: Implement a focused change\n\n"
                "## Global Constraints\n" + ("restricao global " * 200)
                + "\n\nArquivos permitidos: src/auth.py\n\n"
                + "Corpo: " + ("passo da tarefa " * 170) + "FIM-DO-CORPO"
            ),
            "target_files": ["src/auth.py"],
            "depends_on": ["task_1"],
        }
    )
    expected = build_jev_context(subtask, 4000)
    assert 2000 < len(expected) <= 4000
    result = {"classification": "MEDIUM", "recommended_implementer": "luna", "fallback_rule_applied": False}

    with patch("meister.herdr.bridge.classify_task", return_value=result) as mock_classify:
        assert await bridge.execute_subtask(subtask, run_id="no-2000-cut-run") is True

    context = mock_classify.call_args.kwargs["context"]
    assert context == expected
    assert len(context) > 2000
    assert context.rstrip().endswith("FIM-DO-CORPO")


@pytest.mark.asyncio
async def test_bridge_logs_jev_events_with_the_logical_task_id(tmp_path):
    """classify e route_decision usam o id LOGICO (como worker_spawn/subtask_completed); o hash vai em `subtask_id`."""
    from meister.state import compute_subtask_id

    bridge, _, _, subtask = _make_router_bridge(tmp_path)
    run_id = "logical-id-run"
    result = {"classification": "MEDIUM", "recommended_implementer": "luna", "fallback_rule_applied": False}

    with patch("meister.herdr.bridge.classify_task", return_value=result) as mock_classify, \
         patch("meister.herdr.bridge.log_event") as mock_log_event:
        assert await bridge.execute_subtask(subtask, run_id=run_id) is True

    logical_id = str(subtask["id"])
    assert mock_classify.call_args.kwargs["task_id"] == logical_id
    routes = [
        call.kwargs for call in mock_log_event.call_args_list
        if call.kwargs.get("event_type") == "route_decision"
    ]
    assert routes
    expected_hash = compute_subtask_id(run_id, logical_id, subtask.get("description", logical_id))
    for route in routes:
        assert route["task_id"] == logical_id
        assert route["subtask_id"] == expected_hash


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
    assert route_event["task_id"] == subtask["id"]
    assert route_event["subtask_id"] and route_event["subtask_id"] != route_event["task_id"]
    assert route_event["error"] == "routing unavailable"
    assert bridge._jev_unavailable_until > time.monotonic()


@pytest.mark.asyncio
async def test_bridge_jev_failure_cools_down_then_retries(tmp_path):
    # cooldown longo: o teste nao pode depender do relogio (um runner lento fazia o cooldown de 50 ms expirar
    # antes da 2a subtarefa). O fim do cooldown e simulado zerando o estado do bridge.
    bridge, _, _, first_subtask = _make_router_bridge(
        tmp_path, unavailable_cooldown_seconds=300
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
        # ids dos eventos de rota: o logico (como os eventos do worker) e o hash em `subtask_id`
        fallback_routes = [
            call.kwargs for call in mock_log_event.call_args_list
            if call.kwargs.get("event_type") == "route_decision"
            and call.kwargs.get("status") == "fallback"
        ]
        assert fallback_routes and fallback_routes[0]["task_id"] == first_subtask["id"]
        assert fallback_routes[0]["subtask_id"] and fallback_routes[0]["subtask_id"] != first_subtask["id"]
        assert skipped[0]["task_id"] == second_subtask["id"]
        assert skipped[0]["subtask_id"] and skipped[0]["subtask_id"] != second_subtask["id"]

        bridge._jev_unavailable_until = 0.0  # fim do cooldown, sem depender do relogio
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
        unavailable_cooldown_seconds=300,
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
    assert route_events[0]["task_id"] == subtask["id"]
    assert route_events[1]["task_id"] == "after-timeout"
    assert all(event["subtask_id"] and event["subtask_id"] != event["task_id"] for event in route_events[:2])


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
    with patch("meister.herdr.bridge.classify_task", side_effect=AssertionError("must reuse assignment")) as mock_classify, \
         patch("meister.herdr.bridge.log_event") as mock_log_event:
        assert await bridge.execute_subtask(subtask, run_id=run_id) is True
    mock_classify.assert_not_called()
    assert spawned_tiers == ["luna"]
    resumed = [
        call.kwargs for call in mock_log_event.call_args_list
        if call.kwargs.get("event_type") == "route_decision"
    ]
    assert resumed and resumed[0]["status"] == "resumed"
    assert resumed[0]["task_id"] == subtask["id"]
    assert resumed[0]["subtask_id"] == subtask_id


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
    # Prova o paralelismo sem depender do relogio: as 3 chamadas so passam da barreira
    # se estiverem em andamento ao mesmo tempo (em serie, a barreira expira e o maximo fica 1).
    barrier = threading.Barrier(subtask_count, timeout=30)
    lock = threading.Lock()
    active = 0
    max_active = 0

    def slow_classify(**_kwargs):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        finally:
            with lock:
                active -= 1
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
        results = await asyncio.gather(*(bridge.execute_subtask(task) for task in subtasks))

    assert results == [True, True, True]
    assert mock_classify.call_count == subtask_count
    assert max_active == subtask_count
    assert spawned_tiers == ["copilot"] * subtask_count
