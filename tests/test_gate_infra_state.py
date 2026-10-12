"""Erro de infraestrutura do gate: subtask termina FAILED e a mensagem tem um único ponto."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from meister.config import MeisterConfig
from meister.engine.bridge import HerdrEventBridge
from meister.i18n import reset_language_cache, set_language
from meister.state import RunState, StateManager, SubtaskState, compute_subtask_id
from meister.worker import write_atomic_json
from meister.worktree import IntegrationPipeline


@pytest.mark.asyncio
async def test_gate_infrastructure_error_marks_subtask_failed(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    worktree_path = tmp_path / "worker-worktree"
    worktree_path.mkdir()
    worktree = SimpleNamespace(task_id="gate-infra-worktree", worktree_path=str(worktree_path))
    worktree_manager = MagicMock()
    worktree_manager.create_worktree.return_value = worktree
    pipeline = SimpleNamespace(
        integration_info=SimpleNamespace(branch_name="main"),
        wt_mgr=worktree_manager,
        prepare_subtask=MagicMock(return_value=object()),
        merge_prepared=MagicMock(
            return_value=(False, "ERRO DE INFRAESTRUTURA no portão: No tests or linters detected")
        ),
        last_integrated_sha=None,
    )
    sm = StateManager(str(tmp_path / "state.db"))
    subtask = {
        "id": "gate-infra",
        "description": "Gate infrastructure error closes the subtask",
        "target_files": ["app.py"],
        "cwd": str(tmp_path),
    }
    run_id = str(sm.create_or_get_run(task_prompt="plan", cwd=str(tmp_path))["run_id"])
    sm.transition_run(run_id, to_state=RunState.RUNNING)
    sm.add_subtasks(run_id, [subtask])

    mock_client = AsyncMock()
    mock_client.is_connected = True
    bridge = HerdrEventBridge(config=MeisterConfig(), client=mock_client, state_manager=sm)
    bridge._integration_pipeline = pipeline

    async def fake_spawn(tier_name, task_context=None, **kwargs):
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return ("w1:t1", "w1:p1", None)

    bridge.spawner.spawn_worker_tab = fake_spawn
    with patch("meister.engine.bridge.log_event"):
        result = await bridge.execute_subtask(subtask, run_id=run_id)

    assert result is False
    row = sm.get_subtask(compute_subtask_id(run_id, subtask["id"], subtask["description"]))
    assert row is not None
    assert row["status"] == SubtaskState.FAILED.value
    statuses = [item["status"] for item in sm.get_subtasks(run_id)]
    assert statuses.count(SubtaskState.FAILED.value) == 1
    assert SubtaskState.RUNNING.value not in statuses


@pytest.mark.parametrize(
    ("language", "prefix", "continuation"),
    [
        ("en", "GATE INFRASTRUCTURE ERROR:", "The worker's code was not rejected;"),
        ("pt-BR", "ERRO DE INFRAESTRUTURA no portão:", "O código do worker não foi reprovado;"),
    ],
)
def test_gate_infrastructure_message_has_single_period(language, prefix, continuation):
    set_language(language)
    try:
        with_period = IntegrationPipeline._gate_infrastructure_message("Maven is missing; edit that file. ")
        without_period = IntegrationPipeline._gate_infrastructure_message("Maven is missing; edit that file")
    finally:
        reset_language_cache()

    assert str(with_period) == str(without_period)
    assert ".." not in with_period
    assert with_period.startswith(f"{prefix} Maven is missing; edit that file. {continuation}")
    assert with_period.code == "gate_infrastructure"
