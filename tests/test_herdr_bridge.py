import os
import json
from pathlib import Path
import pytest
import asyncio
import subprocess
from unittest.mock import AsyncMock, MagicMock, patch
from meister.herdr.bridge import HerdrEventBridge
from meister.config import load_config, MeisterConfig
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
      harness: "native"
      model: "openai/gpt-6-luna"
    - name: "gemini_flash"
      harness: "native"
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
      harness: "native"
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
      harness: "native"
      model: "openai/gpt-6-luna"
    - name: "gemini_flash"
      harness: "native"
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
    bridge = HerdrEventBridge(config=cfg, client=mock_client, gate=mock_gate)
    success = await bridge.run_orchestration_cycle(
        workspace_id=None,
        architect_pane_id=None,
    )
    assert success is True
    mock_client.get_current_pane.assert_called_once()
    assert any(c[0][0] == "auto_pane" for c in mock_client.read_pane.call_args_list)


def _init_git_repo(path):
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
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
        assert data["model"] == "luna"
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
    assert task_file_seen is not None


@pytest.mark.asyncio
async def test_bridge_infrastructure_error_fast_fails_without_escalation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
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
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
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
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
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
    subprocess.run(["git", "init"], cwd=repo_dir, check=True, capture_output=True)
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




