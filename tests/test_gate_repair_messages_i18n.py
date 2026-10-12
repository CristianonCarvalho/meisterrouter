"""Mensagens do reparo e da falha de infraestrutura do gate passam pelo catálogo de idiomas."""

from __future__ import annotations

import logging
import string
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from meister.config import MeisterConfig, WorkerTier
from meister.engine.bridge import HerdrEventBridge
from meister.i18n import CATALOGS, reset_language_cache, set_language
from meister.worker import write_atomic_json
from meister.worktree import CodedMessage, WorktreeManager

KEYS = (
    "engine.bridge.gate_repair_attempt",
    "engine.bridge.gate_infrastructure_error",
)


def _fields(text: str) -> set[str]:
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


@pytest.mark.parametrize("key", KEYS)
def test_keys_exist_in_both_catalogs_with_same_fields(key: str) -> None:
    assert key in CATALOGS["en"]
    assert key in CATALOGS["pt-BR"]
    assert _fields(CATALOGS["en"][key]) == _fields(CATALOGS["pt-BR"][key])


def test_english_text_is_unchanged() -> None:
    assert CATALOGS["en"]["engine.bridge.gate_repair_attempt"].format(
        task_id="t1", reason="gate", attempt=1, max=1
    ) == "Deterministic gate rejected subtask t1 (gate). Attempting repair 1/1..."
    assert CATALOGS["en"]["engine.bridge.gate_infrastructure_error"].format(
        task_id="t1", error="boom"
    ) == "Infrastructure error in deterministic gate for subtask t1: boom"


@pytest.fixture
def temp_git_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "tester@test.local"], cwd=repo, check=True)
    (repo / "base.txt").write_text("initial\n")
    subprocess.run(["git", "add", "base.txt"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


async def _run_rejected_subtask(repo: Path, label: str, merge_results: list[Any]) -> bool:
    """Executa uma subtarefa cujo merge devolve, em ordem, os resultados dados."""
    wt_dir = repo / f"worktrees_{label}"
    wt_dir.mkdir()
    wt_mgr = WorktreeManager(repo_root=str(repo), worktrees_dir=str(wt_dir))
    subtask_wt = wt_mgr.create_worktree(task_id=f"run_{label}_task", base_ref="main")
    calls = 0

    async def fake_spawn_tab(tier_name, task_context, cwd, **kwargs):
        nonlocal calls
        calls += 1
        write_atomic_json(task_context["result_file"], {"status": "done", "modified_files": []})
        return ("t1", f"p{calls}", None)

    spawner = MagicMock()
    spawner.spawn_worker_tab = AsyncMock(side_effect=fake_spawn_tab)
    spawner.get_tier.return_value = WorkerTier(name="luna", harness="codex", model="gpt-6-luna")

    pipeline = MagicMock()
    pipeline.integration_info = SimpleNamespace(branch_name="main")
    pipeline.wt_mgr = wt_mgr
    pipeline.last_integrated_sha = "deadbeef"
    pipeline.prepare_subtask = MagicMock(return_value=SimpleNamespace(subtask_wt=subtask_wt))
    pipeline.merge_prepared = MagicMock(side_effect=merge_results)

    cfg = MeisterConfig()
    cfg.gate.repair_attempts = 1
    bridge = HerdrEventBridge(config=cfg, client=AsyncMock(), spawner=spawner)
    bridge._integration_pipeline = pipeline
    bridge.current_run_id = f"run_{label}"
    wt_mgr.create_worktree = MagicMock(return_value=subtask_wt)

    return await bridge.execute_subtask(
        {"id": "task1", "description": f"Test {label}", "target_files": ["app.py"]}
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "Deterministic gate rejected subtask task1 (gate). Attempting repair 1/1..."),
        ("pt-BR", "Portão determinístico reprovou a subtarefa task1 (gate). Tentando reparo 1/1..."),
    ],
)
async def test_repair_message_follows_language(
    temp_git_repo: Path, caplog: pytest.LogCaptureFixture, language: str, expected: str
) -> None:
    set_language(language)
    try:
        with caplog.at_level(logging.WARNING, logger="meister.engine.bridge"):
            ok = await _run_rejected_subtask(
                temp_git_repo,
                language,
                [(False, CodedMessage("FAILED tests/test_x.py::test_y", code="gate")), (True, "ok")],
            )
    finally:
        reset_language_cache()
    assert ok is True
    assert expected in caplog.messages


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("en", "Infrastructure error in deterministic gate for subtask task1: "),
        ("pt-BR", "Erro de infraestrutura no portão determinístico da subtarefa task1: "),
    ],
)
async def test_infrastructure_message_follows_language(
    temp_git_repo: Path, caplog: pytest.LogCaptureFixture, language: str, expected: str
) -> None:
    error = "ERRO DE INFRAESTRUTURA no portão: runner indisponível"
    set_language(language)
    try:
        with caplog.at_level(logging.ERROR, logger="meister.engine.bridge"):
            ok = await _run_rejected_subtask(temp_git_repo, f"infra_{language}", [(False, error)])
    finally:
        reset_language_cache()
    assert ok is False
    assert expected + error in caplog.messages
