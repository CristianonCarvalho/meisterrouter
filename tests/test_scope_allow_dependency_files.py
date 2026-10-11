"""Ponta a ponta: scope.allow_dependency_files deixa a tarefa editar arquivos das ancestrais."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from meister.config import MeisterConfig, WorkerTier, WorkersConfig
from meister.gate import VerificationResult
from meister.engine.bridge import HerdrEventBridge
from meister.hosts import WorkerCommand
from meister.worker import read_atomic_json, write_atomic_json
from meister.worktree import IntegrationPipeline, WorktreeManager
from tests.hosts_fake import FakeHost


class _PassingGate:
    def run_verification_ex(self, repo_path=None, docs_only=False):
        return VerificationResult(True, "ok")


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


# Edições que cada worker de mentira faz no seu worktree.
_EDITS = {
    "task_1": {"src/a.py": "a = 1\n"},
    "task_2": {"src/a.py": "a = 2\n", "src/b.py": "b = 2\n"},
    "task_3": {"src/a.py": "a = 3\n", "src/c.py": "c = 3\n"},
}

_STEPS = [
    {"id": "task_1", "description": "Task 1", "target_files": ["src/a.py"], "depends_on": []},
    {"id": "task_2", "description": "Task 2", "target_files": ["src/b.py"], "depends_on": ["task_1"]},
    {"id": "task_3", "description": "Task 3", "target_files": ["src/c.py"], "depends_on": []},
]


def _bridge(tmp_path, monkeypatch, allow):
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "logs"))
    monkeypatch.setenv("MEISTER_WORKTREES_DIR", str(tmp_path / "worktrees"))
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.invalid")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("a = 0\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "init")
    monkeypatch.chdir(repo)

    wt_mgr = WorktreeManager(repo_root=str(repo), worktrees_dir=str(tmp_path / "worktrees"))
    pipeline = IntegrationPipeline(wt_mgr, gate=_PassingGate())
    pipeline.start_integration("run-deps")

    host = FakeHost()

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        for name, content in _EDITS[task_context["id"]].items():
            path = Path(cwd) / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        payload = read_atomic_json(task_context["task_file"])
        write_atomic_json(payload["result_file"], {"status": "done", "modified_files": []})
        handle = await host.spawn(WorkerCommand(argv=["true"], env={}, cwd=cwd, label=task_context["id"]))
        return ("tab", handle.id, None)

    spawner = MagicMock()
    spawner.spawn_worker_tab = MagicMock(side_effect=fake_spawn_tab)
    spawner.get_tier.return_value = MagicMock(name="luna", model="luna")
    spawner.get_next_available_tier.return_value = None
    del spawner.get_first_available_tier

    cfg = MeisterConfig(
        workers=WorkersConfig(tier_order=[WorkerTier(name="luna", harness="codex", model="luna")])
    )
    cfg.concurrency.layout_strategy = "tabs"
    cfg.gate.repair_attempts = 0
    cfg.scope.tolerated_files = []
    cfg.scope.allow_dependency_files = allow
    bridge = HerdrEventBridge(config=cfg, spawner=spawner, host=host)
    bridge._integration_pipeline = pipeline
    bridge.current_run_id = "run-deps"
    events: list = []
    monkeypatch.setattr(
        "meister.engine.bridge.log_event", lambda **event: events.append(event)
    )
    return bridge, pipeline, events


def _rejections(events, task_id):
    return [
        e.get("reason")
        for e in events
        if e.get("event_type") == "subtask_rejected" and e.get("task_id") == task_id
    ]


def _integrated(pipeline, path):
    return (Path(pipeline.integration_info.worktree_path) / path).read_text()


@pytest.mark.asyncio
async def test_dependent_task_may_edit_ancestor_files_when_option_enabled(tmp_path, monkeypatch):
    bridge, pipeline, events = _bridge(tmp_path, monkeypatch, allow=True)

    assert await bridge.execute_plan(_STEPS[:2]) is True

    assert _rejections(events, "task_2") == []
    assert _integrated(pipeline, "src/a.py") == "a = 2\n"
    assert _integrated(pipeline, "src/b.py") == "b = 2\n"


@pytest.mark.asyncio
async def test_dependent_task_rejected_for_ancestor_files_when_option_disabled(tmp_path, monkeypatch):
    bridge, pipeline, events = _bridge(tmp_path, monkeypatch, allow=False)

    assert await bridge.execute_plan(_STEPS[:2]) is False

    assert _rejections(events, "task_1") == []
    assert _rejections(events, "task_2") == ["scope"]
    assert _integrated(pipeline, "src/a.py") == "a = 1\n"


@pytest.mark.parametrize("allow", [True, False])
@pytest.mark.asyncio
async def test_unrelated_task_still_rejected_for_other_task_files(tmp_path, monkeypatch, allow):
    bridge, pipeline, events = _bridge(tmp_path, monkeypatch, allow=allow)

    assert await bridge.execute_plan(_STEPS) is False

    assert _rejections(events, "task_1") == []
    assert _rejections(events, "task_3") == ["scope"]
    assert not (Path(pipeline.integration_info.worktree_path) / "src" / "c.py").exists()
