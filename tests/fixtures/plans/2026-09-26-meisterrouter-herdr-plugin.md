# MeisterRouter Herdr Plugin Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Transform MeisterRouter into a native, high-performance Herdr plugin with multi-model declarative configuration, parallel subtask dispatch via dynamic multi-pane splits, and reactive zero-latency quota failover.

**Architecture:** A Python asyncio daemon initialized via Herdr's `[[startup]]` hook connects to Herdr's local UNIX domain socket (`HERDR_SOCKET_PATH`). It subscribes to agent and pane lifecycle events, delegates planning to an architect model, decomposes independent subtasks into a Directed Acyclic Graph (DAG), executes parallel workers in tiled Herdr panes, and enforces a deterministic evidence gate before committing.

**Tech Stack:** Python 3.10+, `asyncio`, JSON-RPC over UNIX Domain Sockets, PyYAML, TypeSafe Jev Decisions API (`typesafe/jev-1.13`), OpenRouter API, Rich/Curses (TUI), `pytest`.

**Spec:** [`docs/superpowers/specs/2026-09-26-meisterrouter-herdr-plugin-design.md`](file:///Users/cristianocarvalho/Documents/meisterrouter/docs/superpowers/specs/2026-09-26-meisterrouter-herdr-plugin-design.md)

---

## Global Constraints

- Python versions supported: Python >= 3.10.
- All socket interactions with Herdr must use standard library `asyncio` UNIX domain sockets without third-party RPC bloat.
- Worker models and tier orders must be loaded from `meister.config.yaml` with zero hardcoding in execution code.
- Parallel worker execution must strictly honor `concurrency.max_parallel_workers` (default 4).
- Deterministic Evidence Gate requires test execution and diff summary verification before any commit authorization.

## Review Focus

1. **Herdr socket disconnect/reconnect:** The daemon must gracefully handle Herdr server restarts or transient socket unavailability without crashing.
2. **Quota/Rate limit race condition:** Immediate `ctrl+c` interrupt sent to a failing pane before tokens are wasted on endless retries.
3. **DAG merge collision:** If parallel workers inadvertently modify overlapping files, detect the collision before applying diffs.
4. **ANSI terminal rendering corruption in TUI:** The overlay TUI dashboard must handle terminal resize signals (`SIGWINCH`) and clean exit on `q` / `Esc`.
5. **Missing environment variables:** Clear and immediate diagnostic when `OPENROUTER_API_KEY` or `HERDR_SOCKET_PATH` is absent.

---

### Task 1: Declarative Configuration System (`meister/config.py`)

**Files:**
- Create: `meister/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces:
  - `load_config(config_path: Optional[str] = None) -> MeisterConfig`
  - `MeisterConfig`, `MasterConfig`, `ArchitectConfig`, `WorkerTier`, `ConcurrencyConfig`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py
import pytest
import os
from meister.config import load_config, MeisterConfig

def test_load_default_config_from_yaml(tmp_path):
    config_yaml = tmp_path / "meister.config.yaml"
    config_yaml.write_text("""
version: "1.0"
master:
  provider: "openrouter"
  model: "typesafe/jev-1.13"
architect:
  harness: "claude"
  model: "anthropic/claude-3-7-sonnet"
workers:
  tier_order:
    - name: "luna"
      harness: "native"
      model: "openai/gpt-6-luna"
      cost_per_m_tokens: 0.077
concurrency:
  parallel_tasks: true
  max_parallel_workers: 3
""")
    config = load_config(str(config_yaml))
    assert isinstance(config, MeisterConfig)
    assert config.master.model == "typesafe/jev-1.13"
    assert config.architect.model == "anthropic/claude-3-7-sonnet"
    assert len(config.workers.tier_order) == 1
    assert config.workers.tier_order[0].name == "luna"
    assert config.concurrency.max_parallel_workers == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_config.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'meister.config'`

- [ ] **Step 3: Implement `MeisterConfig` dataclasses and `load_config()` in `meister/config.py`**

Define dataclasses `WorkerTier`, `MasterConfig`, `ArchitectConfig`, `ConcurrencyConfig`, `MeisterConfig` and the YAML parser with defaults for `meister.config.yaml`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_config.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meister/config.py tests/test_config.py
git commit -m "feat: add declarative configuration system for models and worker tiers"
```

---

### Task 2: Herdr Plugin Manifest (`herdr-plugin.toml`)

**Files:**
- Create: `herdr-plugin.toml`
- Test: `tests/test_herdr_manifest.py`

**Interfaces:**
- Produces: Valid Herdr plugin manifest conforming to Herdr Plugin Spec v1.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_herdr_manifest.py
import tomllib
from pathlib import Path

def test_herdr_plugin_manifest_structure():
    manifest_path = Path("herdr-plugin.toml")
    assert manifest_path.exists(), "herdr-plugin.toml must exist at repo root"
    
    with open(manifest_path, "rb") as f:
        data = tomllib.load(f)
        
    assert data["id"] == "dev.meisterrouter.orchestrator"
    assert data["name"] == "MeisterRouter"
    assert data["min_herdr_version"] == "0.7.0"
    assert "startup" in data and len(data["startup"]) > 0
    assert "actions" in data and len(data["actions"]) >= 3
    assert any(a["id"] == "auto-orchestrate" for a in data["actions"])
    assert "panes" in data and any(p["id"] == "dashboard" for p in data["panes"])
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_herdr_manifest.py -v`  
Expected: FAIL with `AssertionError: herdr-plugin.toml must exist at repo root`

- [ ] **Step 3: Create `herdr-plugin.toml` at the repository root**

Fill `herdr-plugin.toml` with `[[startup]]`, `[[actions]]`, `[[panes]]`, and `[[keys.command]]` as specified in Section 3 of the design doc.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_herdr_manifest.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add herdr-plugin.toml tests/test_herdr_manifest.py
git commit -m "feat: add official Herdr plugin manifest"
```

---

### Task 3: Herdr Socket Client (`meister/herdr/client.py`)

**Files:**
- Create: `tests/mocks/mock_herdr_server.py`
- Create: `meister/herdr/client.py`
- Test: `tests/test_herdr_client.py`

**Interfaces:**
- Consumes: Herdr UNIX domain socket at `$HERDR_SOCKET_PATH`
- Produces:
  - `class HerdrSocketClient`
  - `async split_pane(direction: str, command: Optional[list[str]], split_ratio: float) -> str`
  - `async read_pane(pane_id: str, lines: int) -> str`
  - `async prompt_agent(pane_id: str, prompt: str, wait_until: str, timeout_ms: int) -> dict`
  - `async subscribe_events(callback: Callable[[dict], Awaitable[None]]) -> None`
  - `async send_keys(pane_id: str, keys: str) -> None`
  - `async show_notification(message: str) -> None`

- [ ] **Step 1: Create mock Herdr server and failing tests**

```python
# tests/test_herdr_client.py
import pytest
import asyncio
from tests.mocks.mock_herdr_server import run_mock_herdr_server
from meister.herdr.client import HerdrSocketClient

@pytest.mark.asyncio
async def test_client_split_and_read(tmp_path):
    sock_path = str(tmp_path / "herdr.sock")
    server = await run_mock_herdr_server(sock_path)
    
    try:
        client = HerdrSocketClient(sock_path)
        await client.connect()
        pane_id = await client.split_pane(direction="right", command=["echo", "worker"])
        assert pane_id == "w1:p2"
        
        output = await client.read_pane(pane_id)
        assert "mock terminal output" in output
        await client.close()
    finally:
        server.close()
        await server.wait_closed()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_herdr_client.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'meister.herdr.client'`

- [ ] **Step 3: Implement `HerdrSocketClient` in `meister/herdr/client.py` and `run_mock_herdr_server` in `tests/mocks/mock_herdr_server.py`**

Implement standard JSON-RPC request-response handling over `asyncio.open_unix_connection` with unique request IDs and async background event listener.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_herdr_client.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add tests/mocks/mock_herdr_server.py meister/herdr/client.py tests/test_herdr_client.py
git commit -m "feat: implement async JSON-RPC client for Herdr Socket API"
```

---

### Task 4: Worker Spawner & Multi-Model Tier Hierarchy (`meister/herdr/workers.py`)

**Files:**
- Create: `meister/herdr/workers.py`
- Test: `tests/test_workers.py`

**Interfaces:**
- Consumes: `MeisterConfig` from `meister/config.py`, `HerdrSocketClient` from `meister/herdr/client.py`
- Produces:
  - `class WorkerSpawner`
  - `spawn_worker_pane(tier_name: str, task_context: dict) -> Tuple[str, WorkerTier]`
  - `detect_quota_or_rate_limit(output: str) -> bool`
  - `get_next_tier(current_tier_name: str) -> Optional[WorkerTier]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_workers.py
import pytest
from meister.config import load_config
from meister.herdr.workers import WorkerSpawner, detect_quota_or_rate_limit

def test_detect_quota_and_escalate_tier(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("""
version: "1.0"
workers:
  tier_order:
    - name: "luna"
      harness: "native"
      model: "openai/gpt-6-luna"
      cost_per_m_tokens: 0.077
    - name: "gemini_flash"
      harness: "native"
      model: "google/gemini-2.5-flash"
      cost_per_m_tokens: 0.577
""")
    config = load_config(str(cfg_file))
    spawner = WorkerSpawner(config, herdr_client=None)
    
    assert detect_quota_or_rate_limit("Error 429: Rate limit exceeded") is True
    assert detect_quota_or_rate_limit("Credit balance too low (402)") is True
    assert detect_quota_or_rate_limit("Successfully compiled") is False
    
    next_tier = spawner.get_next_tier("luna")
    assert next_tier is not None
    assert next_tier.name == "gemini_flash"
    assert spawner.get_next_tier("gemini_flash") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_workers.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'meister.herdr.workers'`

- [ ] **Step 3: Implement `WorkerSpawner` and quota matcher in `meister/herdr/workers.py`**

Implement regex matchers for 429/402 and tier escalation logic according to `config.workers.tier_order`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_workers.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meister/herdr/workers.py tests/test_workers.py
git commit -m "feat: implement worker tier manager and quota error detector"
```

---

### Task 5: Dependency Analysis & Parallel Task DAG (`meister/herdr/dag.py`)

**Files:**
- Create: `meister/herdr/dag.py`
- Test: `tests/test_dag.py`

**Interfaces:**
- Produces:
  - `class TaskDAG`
  - `class SubtaskNode`
  - `build_subtask_dag(actionable_steps: list[dict]) -> TaskDAG`
  - `get_independent_batches(dag: TaskDAG) -> list[list[SubtaskNode]]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dag.py
import pytest
from meister.herdr.dag import build_subtask_dag, SubtaskNode

def test_dag_parallel_batching():
    steps = [
        {"id": "t1", "description": "Auth backend", "target_files": ["src/auth.py"], "depends_on": []},
        {"id": "t2", "description": "Login frontend", "target_files": ["src/login.tsx"], "depends_on": []},
        {"id": "t3", "description": "Integrate tests", "target_files": ["tests/test_auth.py"], "depends_on": ["t1", "t2"]}
    ]
    dag = build_subtask_dag(steps)
    batches = dag.get_execution_batches()
    
    # t1 and t2 should execute in parallel in the first batch
    assert len(batches) == 2
    batch1_ids = {n.id for n in batches[0]}
    assert batch1_ids == {"t1", "t2"}
    assert [n.id for n in batches[1]] == ["t3"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_dag.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'meister.herdr.dag'`

- [ ] **Step 3: Implement `SubtaskNode` and `TaskDAG` in `meister/herdr/dag.py`**

Implement topological sort and file overlap detection to group independent tasks into concurrent execution batches.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_dag.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meister/herdr/dag.py tests/test_dag.py
git commit -m "feat: implement dependency DAG and parallel batch scheduler"
```

---

### Task 6: Event Bridge & Parallel Failover Supervisor (`meister/herdr/bridge.py`)

**Files:**
- Create: `meister/herdr/bridge.py`
- Test: `tests/test_herdr_bridge.py`

**Interfaces:**
- Consumes: `HerdrSocketClient`, `WorkerSpawner`, `TaskDAG`, `meister.jev`
- Produces:
  - `class HerdrEventBridge`
  - `async run_orchestration_cycle(workspace_id: str, architect_pane_id: str) -> bool`
  - `async handle_herdr_event(event: dict) -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_herdr_bridge.py
import pytest
from unittest.mock import AsyncMock, MagicMock
from meister.herdr.bridge import HerdrEventBridge
from meister.config import load_config

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
    
    bridge = HerdrEventBridge(config=config, client=mock_client)
    subtasks = [
        {"id": "t1", "description": "Backend", "target_files": ["backend.py"], "depends_on": []},
        {"id": "t2", "description": "Frontend", "target_files": ["frontend.py"], "depends_on": []}
    ]
    success = await bridge.execute_parallel_batch(subtasks)
    assert success is True
    assert mock_client.split_pane.call_count == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_herdr_bridge.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'meister.herdr.bridge'`

- [ ] **Step 3: Implement `HerdrEventBridge` in `meister/herdr/bridge.py`**

Implement async batch dispatching using `asyncio.gather` bounded by `config.concurrency.max_parallel_workers`, listener for quota failover, and Jev control handshakes.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_herdr_bridge.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meister/herdr/bridge.py tests/test_herdr_bridge.py
git commit -m "feat: implement Herdr event bridge and parallel execution supervisor"
```

---

### Task 7: Deterministic Quality Gate (`meister/gate.py`)

**Files:**
- Create: `meister/gate.py`
- Test: `tests/test_gate.py`

**Interfaces:**
- Produces:
  - `class DeterministicGate`
  - `detect_test_runner(repo_path: str) -> Optional[str]`
  - `run_verification(repo_path: str) -> Tuple[bool, str]`
  - `evaluate_completion(diff_summary: str, test_passed: bool) -> dict`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_gate.py
import pytest
from meister.gate import DeterministicGate

def test_detect_pytest_runner(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n")
    gate = DeterministicGate(str(tmp_path))
    runner = gate.detect_test_runner()
    assert runner == "pytest"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_gate.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'meister.gate'`

- [ ] **Step 3: Implement `DeterministicGate` in `meister/gate.py`**

Implement auto-detection for pytest, vitest, cargo test, npm test, execution of linters (ruff/eslint), git diff extraction, and Jev `control` decision check.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_gate.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meister/gate.py tests/test_gate.py
git commit -m "feat: implement deterministic quality gate and test runner detector"
```

---

### Task 8: CLI Commands & Daemon Lifecycle (`meister/cli.py`)

**Files:**
- Modify: `meister/cli.py`
- Test: `tests/test_cli_herdr.py`

**Interfaces:**
- Produces CLI entrypoints:
  - `meister daemon --start`
  - `meister herdr-action <action_id>`
  - `meister orchestrate`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_cli_herdr.py
import pytest
from click.testing import CliRunner
from meister.cli import main

def test_cli_has_herdr_commands():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "daemon" in result.output
    assert "herdr-action" in result.output
    assert "orchestrate" in result.output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_cli_herdr.py -v`  
Expected: FAIL with missing commands in help output

- [ ] **Step 3: Add `daemon`, `herdr-action`, and `orchestrate` commands to `meister/cli.py`**

Wire the commands to `HerdrEventBridge`, `load_config`, and graceful signal handling (`SIGINT`, `SIGTERM`).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_cli_herdr.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meister/cli.py tests/test_cli_herdr.py
git commit -m "feat: add Herdr plugin CLI subcommands and daemon lifecycle"
```

---

### Task 9: TUI Telemetry Dashboard Overlay (`meister/herdr/tui.py`)

**Files:**
- Create: `meister/herdr/tui.py`
- Modify: `meister/cli.py`
- Test: `tests/test_tui.py`

**Interfaces:**
- Produces:
  - `render_tui_dashboard(session_state: dict, cost_metrics: dict) -> str`
  - `run_tui_loop() -> None`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tui.py
import pytest
from meister.herdr.tui import render_tui_dashboard

def test_render_tui_dashboard_output():
    state = {
        "workspace": "my-project",
        "status": "ORCHESTRATING",
        "active_worker": "gpt-6-luna",
        "task_desc": "Auth unit tests"
    }
    metrics = {
        "tokens": 42000,
        "cost": 0.0032,
        "traditional_cost": 0.126,
        "savings_pct": 97.4
    }
    output = render_tui_dashboard(state, metrics)
    assert "MeisterRouter Live Telemetry" in output
    assert "gpt-6-luna" in output
    assert "97.4%" in output
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/test_tui.py -v`  
Expected: FAIL with `ModuleNotFoundError: No module named 'meister.herdr.tui'`

- [ ] **Step 3: Implement ANSI/Rich dashboard renderer in `meister/herdr/tui.py`**

Format tables for active session, worker statuses, cost savings, and key shortcuts (`Q` to close, `O` to open web browser).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/test_tui.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add meister/herdr/tui.py tests/test_tui.py meister/cli.py
git commit -m "feat: implement TUI telemetry dashboard for Herdr overlay pane"
```
