#!/usr/bin/env python3
"""
tests/crash_driver.py — Subprocess driver for crash-matrix testing.

NOT collected by pytest (does not start with test_).

Runs the full orchestration cycle inside a subprocess on a temporary git repo
with a deterministic 3-subtask plan and FAKE Herdr client + FAKE worker.
Supports fault injection via MEISTER_CRASH_AT / MEISTER_CRASH_TASK /
MEISTER_CRASH_NTH environment variables.

Usage (from test_crash_matrix.py):
    result = subprocess.run([sys.executable, "tests/crash_driver.py", repo_dir],
                            env={..., "MEISTER_CRASH_AT": "after_run_created"})
    # rc == -9 if crash point was hit, 0 if run completed successfully
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


def setup_temp_repo(repo_dir: str) -> str:
    """Create a minimal git repo with a seed file suitable for 3-subtask plan."""
    os.makedirs(repo_dir, exist_ok=True)

    subprocess.run(
        ["git", "init", "-b", "main", repo_dir],
        capture_output=True,
        check=True,
    )

    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "CrashTest"
    env["GIT_AUTHOR_EMAIL"] = "crash@test.local"
    env["GIT_COMMITTER_NAME"] = "CrashTest"
    env["GIT_COMMITTER_EMAIL"] = "crash@test.local"

    # Seed files
    (Path(repo_dir) / "calc.py").write_text("# calc module\n")
    (Path(repo_dir) / "text.py").write_text("# text module\n")
    (Path(repo_dir) / "utils.py").write_text("# utils module\n")
    tests_dir = Path(repo_dir) / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "__init__.py").write_text("")
    (tests_dir / "test_calc.py").write_text("# calc tests\n")
    (tests_dir / "test_text.py").write_text("# text tests\n")
    (tests_dir / "test_utils.py").write_text("# utils tests\n")

    # pyproject.toml for pytest detection
    (Path(repo_dir) / "pyproject.toml").write_text(
        '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n'
    )

    subprocess.run(
        ["git", "add", "-A"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
        env=env,
    )
    subprocess.run(
        ["git", "commit", "-m", "initial seed"],
        cwd=repo_dir,
        capture_output=True,
        check=True,
        env=env,
    )

    return repo_dir


# ---- Fake Herdr state (persisted to file, survives across runs) ----

class FakeHerdrState:
    """Persisted fake Herdr state — tabs, panes, and notification history."""

    def __init__(self, state_file: str):
        self.state_file = state_file
        self.data: Dict[str, Any] = {"tabs": {}, "panes": {}, "notifications": []}
        self._load()

    def _load(self):
        if os.path.exists(self.state_file):
            with open(self.state_file, "r") as f:
                self.data = json.load(f)

    def _save(self):
        with open(self.state_file, "w") as f:
            json.dump(self.data, f)

    def create_tab(self, label: str) -> Tuple[str, str]:
        tab_id = f"tab_{uuid.uuid4().hex[:8]}"
        pane_id = f"pane_{uuid.uuid4().hex[:8]}"
        self.data["tabs"][tab_id] = {"label": label, "pane_id": pane_id, "closed": False}
        self.data["panes"][pane_id] = {"tab_id": tab_id, "closed": False}
        self._save()
        return tab_id, pane_id

    def close_tab(self, tab_id: str):
        if tab_id in self.data["tabs"]:
            self.data["tabs"][tab_id]["closed"] = True
            pane_id = self.data["tabs"][tab_id].get("pane_id")
            if pane_id and pane_id in self.data["panes"]:
                self.data["panes"][pane_id]["closed"] = True
        self._save()

    def close_pane(self, pane_id: str):
        if pane_id in self.data["panes"]:
            self.data["panes"][pane_id]["closed"] = True
        self._save()

    def add_notification(self, msg: str):
        self.data["notifications"].append({"msg": msg, "ts": time.time()})
        self._save()

    def get_open_tabs(self) -> List[str]:
        return [tid for tid, t in self.data["tabs"].items() if not t.get("closed")]

    def get_open_panes(self) -> List[str]:
        return [pid for pid, p in self.data["panes"].items() if not p.get("closed")]


# ---- Fake Herdr client ----

class FakeHerdrClient:
    """Minimal fake HerdrSocketClient that satisfies bridge expectations."""

    def __init__(self, herdr_state: FakeHerdrState):
        self._state = herdr_state
        self.is_connected = True

    async def connect(self):
        self.is_connected = True

    async def subscribe_events(self, handler):
        pass

    async def show_notification(self, msg, **kwargs):
        self._state.add_notification(msg)

    async def read_pane(self, pane_id):
        return ""

    async def get_current_pane(self):
        return {"workspace_id": "test", "pane_id": "architect_pane"}

    async def send_interrupt(self, pane_id):
        pass

    async def close_tab(self, tab_id):
        self._state.close_tab(tab_id)

    async def close_pane(self, pane_id):
        self._state.close_pane(pane_id)

    async def _call(self, method, params):
        return {}


# ---- Fake worker spawner ----

class FakeWorkerSpawner:
    """Deterministic fake spawner: applies edits and writes result.json."""

    def __init__(self, herdr_state: FakeHerdrState, spawn_counts: Dict[str, int]):
        self.herdr_client = None
        self._state = herdr_state
        self.spawn_counts = spawn_counts

    def get_tier(self, name):
        """Return a fake tier object."""
        class FakeTier:
            def __init__(self, n):
                self.name = n
                self.model = f"fake-{n}"
        return FakeTier(name)

    def get_next_available_tier(self, current, **kwargs):
        return None  # No escalation in tests

    async def spawn_worker_tab(self, tier_name, task_context, cwd=None, label="", focus=False):
        tab_id, pane_id = self._state.create_tab(label)
        task_id = task_context.get("id", "unknown")
        self.spawn_counts[task_id] = self.spawn_counts.get(task_id, 0) + 1

        # Simulate the worker: apply edits and write result.json
        await self._do_fake_work(task_context, cwd)

        return tab_id, pane_id, None

    async def spawn_worker_pane(self, tier_name, task_context, direction="right", split_ratio=0.5, cwd=None):
        _, pane_id = self._state.create_tab("")
        task_id = task_context.get("id", "unknown")
        self.spawn_counts[task_id] = self.spawn_counts.get(task_id, 0) + 1
        await self._do_fake_work(task_context, cwd)
        return pane_id, None

    async def _do_fake_work(self, task_context: Dict[str, Any], cwd: Optional[str]):
        """Apply the subtask's edits to the worktree and write result.json."""
        task_id = task_context.get("id", "unknown")
        result_file = task_context.get("result_file")
        work_dir = cwd or task_context.get("cwd") or task_context.get("worktree")

        if work_dir and os.path.isdir(work_dir):
            # Deterministic edits per task
            if "t1" in task_id:
                _apply_t1_edits(work_dir)
            elif "t2" in task_id:
                _apply_t2_edits(work_dir)
            elif "t3" in task_id:
                _apply_t3_edits(work_dir)

        # Write result.json (same contract as run-task)
        if result_file:
            result = {
                "status": "success",
                "output": f"Fake worker completed {task_id}",
                "task_id": task_id,
            }
            os.makedirs(os.path.dirname(result_file), exist_ok=True)
            with open(result_file, "w") as f:
                json.dump(result, f)


def _apply_t1_edits(work_dir: str):
    """t1: Add mul function to calc.py and test_mul to tests/test_calc.py"""
    calc_path = os.path.join(work_dir, "calc.py")
    with open(calc_path, "w") as f:
        f.write("# calc module\n\ndef mul(a, b):\n    return a * b\n")

    test_path = os.path.join(work_dir, "tests", "test_calc.py")
    os.makedirs(os.path.dirname(test_path), exist_ok=True)
    with open(test_path, "w") as f:
        f.write("from calc import mul\n\ndef test_mul():\n    assert mul(3, 4) == 12\n")


def _apply_t2_edits(work_dir: str):
    """t2: Add shout function to text.py and test_shout to tests/test_text.py (depends on t1)"""
    text_path = os.path.join(work_dir, "text.py")
    with open(text_path, "w") as f:
        f.write("# text module\n\ndef shout(name):\n    return name.upper()\n")

    test_path = os.path.join(work_dir, "tests", "test_text.py")
    os.makedirs(os.path.dirname(test_path), exist_ok=True)
    with open(test_path, "w") as f:
        f.write("from text import shout\n\ndef test_shout():\n    assert shout('hello') == 'HELLO'\n")


def _apply_t3_edits(work_dir: str):
    """t3: Add reverse function to utils.py and test_reverse (independent of t1)"""
    utils_path = os.path.join(work_dir, "utils.py")
    with open(utils_path, "w") as f:
        f.write("# utils module\n\ndef reverse(s):\n    return s[::-1]\n")

    test_path = os.path.join(work_dir, "tests", "test_utils.py")
    os.makedirs(os.path.dirname(test_path), exist_ok=True)
    with open(test_path, "w") as f:
        f.write("from utils import reverse\n\ndef test_reverse():\n    assert reverse('abc') == 'cba'\n")


# ---- Plan for 3 subtasks ----

PLAN_3_SUBTASKS = json.dumps([
    {
        "id": "t1",
        "description": "Add mul(a,b) to calc.py and test_mul to tests/test_calc.py",
        "target_files": ["calc.py", "tests/test_calc.py"],
        "depends_on": [],
    },
    {
        "id": "t2",
        "description": "Add shout(name) to text.py and test_shout to tests/test_text.py",
        "target_files": ["text.py", "tests/test_text.py"],
        "depends_on": ["t1"],
    },
    {
        "id": "t3",
        "description": "Add reverse(s) to utils.py and test_reverse to tests/test_utils.py",
        "target_files": ["utils.py", "tests/test_utils.py"],
        "depends_on": [],
    },
])


# ---- Fake gate (always passes) ----

class FakeGate:
    """Deterministic gate that always passes — we want to test crash recovery, not gate logic."""

    def __init__(self, repo_path="."):
        self.repo_path = repo_path

    def detect_test_runner(self, repo_path=None):
        return "pytest"

    def detect_linters(self, repo_path=None):
        return []

    def run_verification(self, repo_path=None):
        return True, "Fake gate: all tests pass"

    def get_diff_summary(self, repo_path=None):
        return "Fake diff summary"

    def evaluate_completion(self, diff_summary=None, test_passed=None, **kwargs):
        return {"action": "COMPLETE", "reason": "Fake gate approval"}


async def run_orchestration(repo_dir: str, state_dir: str) -> int:
    """Run the orchestration cycle with fakes, returning exit code."""
    herdr_state_file = os.path.join(state_dir, "herdr_state.json")
    spawn_counts_file = os.path.join(state_dir, "spawn_counts.json")

    herdr_state = FakeHerdrState(herdr_state_file)

    # Load persisted spawn counts
    spawn_counts: Dict[str, int] = {}
    if os.path.exists(spawn_counts_file):
        with open(spawn_counts_file, "r") as f:
            spawn_counts = json.load(f)

    fake_client = FakeHerdrClient(herdr_state)
    fake_spawner = FakeWorkerSpawner(herdr_state, spawn_counts)
    fake_gate = FakeGate(repo_dir)

    # Import bridge and set up
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from meister.herdr.bridge import HerdrEventBridge
    from meister.config import MeisterConfig
    from meister.state import StateManager

    config = MeisterConfig()
    sm = StateManager()

    bridge = HerdrEventBridge(
        config=config,
        client=fake_client,
        spawner=fake_spawner,
        gate=fake_gate,
        state_manager=sm,
    )

    try:
        success = await bridge.run_orchestration_cycle(
            workspace_id="test",
            architect_pane_id="architect",
            task=PLAN_3_SUBTASKS,
        )
    except Exception as e:
        print(f"DRIVER ERROR: {e}", file=sys.stderr)
        success = False

    # Persist spawn counts
    with open(spawn_counts_file, "w") as f:
        json.dump(spawn_counts, f)

    return 0 if success else 1


def main():
    if len(sys.argv) < 2:
        print("Usage: crash_driver.py <repo_dir> [state_dir]", file=sys.stderr)
        sys.exit(2)

    repo_dir = sys.argv[1]
    state_dir = sys.argv[2] if len(sys.argv) > 2 else os.path.join(repo_dir, ".crash_state")
    os.makedirs(state_dir, exist_ok=True)

    os.chdir(repo_dir)

    rc = asyncio.run(run_orchestration(repo_dir, state_dir))
    sys.exit(rc)


if __name__ == "__main__":
    main()
