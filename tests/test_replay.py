import os
import json
import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.logger import (
    log_event,
    log_classify,
    log_control,
    track_task,
    get_events_by_run_id,
    get_log_dir,
    find_project_root,
)
from meister.state import StateManager


@pytest.fixture
def clean_log_env(tmp_path, monkeypatch):
    log_dir = str(tmp_path / "logs")
    monkeypatch.setenv("MEISTER_LOG_DIR", log_dir)
    return log_dir


# =============================================================================
# 1. Unified Correlation Envelope (Achado #32)
# =============================================================================


def test_unified_correlation_envelope(clean_log_env):
    """Verifica que todo evento registrado possui os campos do envelope unificado (Achado #32)."""
    ev = log_event(
        event_type="worker_spawn",
        run_id="run_abc123",
        task_id="task_sub1",
        attempt=2,
        tier="gemini_flash",
        duration_ms=150.5,
        cost=0.00012,
        exit_code=0,
        extra_detail="pane:123",
    )

    # Campos obrigatórios do envelope unificado
    assert ev["run_id"] == "run_abc123"
    assert ev["task_id"] == "task_sub1"
    assert ev["attempt"] == 2
    assert ev["tier"] == "gemini_flash"
    assert ev["event"] == "worker_spawn"
    assert "ts" in ev
    assert ev["duration_ms"] == 150.5
    assert ev["cost"] == 0.00012
    assert ev["exit_code"] == 0
    assert ev["extra_detail"] == "pane:123"

    # Campos de retrocompatibilidade
    assert ev["event_type"] == "worker_spawn"
    assert ev["timestamp"] == ev["ts"]
    assert ev["cost_usd"] == 0.00012


def test_get_events_by_run_id_filtering(clean_log_env):
    """Verifica que get_events_by_run_id filtra estritamente eventos pelo run_id correlacionado."""
    log_event("classify", run_id="run_1", task_id="t1", tier="jev")
    log_event("worker_spawn", run_id="run_1", task_id="t1", tier="luna")
    log_event("classify", run_id="run_2", task_id="t2", tier="jev")
    log_event("subtask_completed", run_id="run_1", task_id="t1", tier="luna")

    events_run1 = get_events_by_run_id("run_1")
    assert len(events_run1) == 3
    for ev in events_run1:
        assert ev["run_id"] == "run_1"

    events_run2 = get_events_by_run_id("run_2")
    assert len(events_run2) == 1
    assert events_run2[0]["task_id"] == "t2"

    events_none = get_events_by_run_id("non_existent_run")
    assert events_none == []


def test_classify_and_control_correlation_and_model_config(clean_log_env):
    """Verifica que classify e control respeitam o modelo explicitamente recebido."""
    ev_cls = log_classify(
        task_id="task_classify_1",
        context="Refactor database schema",
        classification="HIGH",
        recommended_implementer="gemini_flash",
        confidence=0.92,
        tokens_in=500,
        tokens_out=150,
        cost=0.00025,
        run_id="run_corr_99",
        attempt=1,
        duration_ms=450.0,
        model="custom/jev-model-v2",
    )
    assert ev_cls["run_id"] == "run_corr_99"
    assert ev_cls["tier"] == "jev"
    assert ev_cls["model"] == "custom/jev-model-v2"
    assert ev_cls["cost"] == 0.00025
    assert ev_cls["duration_ms"] == 450.0

    ev_ctl = log_control(
        task_id="task_classify_1",
        action="COMPLETE",
        should_escalate=False,
        switch_implementer=False,
        confidence=0.98,
        cost=0.0001,
        run_id="run_corr_99",
        attempt=1,
        model="custom/jev-model-v2",
    )
    assert ev_ctl["run_id"] == "run_corr_99"
    assert ev_ctl["model"] == "custom/jev-model-v2"
    assert ev_ctl["action"] == "COMPLETE"


def test_track_task_envelope(clean_log_env):
    """Verifica que track_task grava task_start e task_end com correlação e duração em ms."""
    with track_task(task_id="sub_1", subagent="luna", run_id="run_track_1", attempt=2) as t:
        t["tokens_in"] = 200
        t["tokens_out"] = 50
        t["cost"] = 0.00005

    events = get_events_by_run_id("run_track_1")
    assert len(events) == 2
    assert events[0]["event"] == "task_start"
    assert events[0]["attempt"] == 2
    assert events[1]["event"] == "task_end"
    assert events[1]["attempt"] == 2
    assert events[1]["cost"] == 0.00005
    assert events[1]["duration_ms"] >= 0.0


# =============================================================================
# 2. Deterministic Log Location (Achado #32)
# =============================================================================


def test_find_project_root_and_deterministic_log_dir(tmp_path, monkeypatch):
    """Verifica que find_project_root acha a raiz com .meister determinística independente do cwd."""
    monkeypatch.delenv("MEISTER_LOG_DIR", raising=False)

    repo_dir = tmp_path / "my_project"
    repo_dir.mkdir()
    (repo_dir / ".meister").mkdir()

    sub_dir = repo_dir / "src" / "deep" / "nested"
    sub_dir.mkdir(parents=True)

    monkeypatch.chdir(sub_dir)
    assert os.path.realpath(find_project_root()) == os.path.realpath(str(repo_dir))

    expected_log_dir = os.path.join(str(repo_dir), ".meister", "logs")
    assert os.path.realpath(get_log_dir()) == os.path.realpath(expected_log_dir)


# =============================================================================
# 3. CLI meister replay <run_id> (Achado #32)
# =============================================================================


def test_cli_replay_formatted_output(clean_log_env):
    """Testa o comando meister replay <run_id> em modo texto formatado."""
    run_id = "run_audit_test_1"

    log_event("orchestration_start", run_id=run_id, task_id="orchestrator", task="Migrate auth system")
    log_event("classify", run_id=run_id, task_id="t1", tier="jev", classification="MEDIUM", cost=0.000045)
    log_event("worker_spawn", run_id=run_id, task_id="t1", attempt=1, tier="luna")
    log_event("subtask_completed", run_id=run_id, task_id="t1", attempt=1, tier="luna", duration_ms=1200.0, cost=0.000100)
    log_event("control", run_id=run_id, task_id="t1", tier="jev", action="COMPLETE", cost=0.000030)
    log_event("orchestration_end", run_id=run_id, task_id="orchestrator", status="completed", exit_code=0)

    runner = CliRunner()
    result = runner.invoke(main, ["replay", run_id])

    assert result.exit_code == 0
    assert f"Replay da Execução: {run_id}" in result.output
    assert "ORCHESTRATION_START" in result.output
    assert "CLASSIFY" in result.output
    assert "WORKER_SPAWN" in result.output
    assert "SUBTASK_COMPLETED" in result.output
    assert "CONTROL" in result.output
    assert "Total de eventos: 6" in result.output
    assert "Custo total:" in result.output


def test_cli_replay_json_output(clean_log_env):
    """Testa o comando meister replay <run_id> com a flag --json."""
    run_id = "run_json_test_2"

    log_event("worker_spawn", run_id=run_id, task_id="task_x", attempt=1, tier="haiku", cost=0.0005)
    log_event("subtask_completed", run_id=run_id, task_id="task_x", attempt=1, tier="haiku", cost=0.0010)

    runner = CliRunner()
    result = runner.invoke(main, ["replay", run_id, "--json"])

    assert result.exit_code == 0
    parsed = json.loads(result.output)
    assert isinstance(parsed, list)
    assert len(parsed) == 2
    assert parsed[0]["tier"] == "haiku"
    assert parsed[1]["cost"] == 0.0010


def test_cli_replay_not_found(clean_log_env):
    """Testa meister replay com run_id inexistente."""
    runner = CliRunner()
    result = runner.invoke(main, ["replay", "non_existent_run_999"])

    assert result.exit_code != 0
    assert "Nenhuma execução encontrada" in result.output or "Nenhuma execução encontrada" in result.stderr


def test_cli_replay_sqlite_fallback(clean_log_env, tmp_path, monkeypatch):
    """Testa fallback para SQLite caso os logs JSONL não estejam disponíveis para o run_id."""
    db_file = tmp_path / "replay_test.db"
    monkeypatch.setenv("MEISTER_DB_PATH", str(db_file))

    sm = StateManager(db_path=str(db_file))
    run = sm.create_or_get_run(task_prompt="Implement cache layer", force_run_id="run_sqlite_only_1")
    sm.add_subtasks(
        run["run_id"],
        [{"id": "sub_1", "description": "Create Redis connector", "target_files": ["cache.py"]}],
    )
    sm.close()

    runner = CliRunner()
    result = runner.invoke(main, ["replay", "run_sqlite_only_1"])

    assert result.exit_code == 0
    assert "via SQLite" in result.output
    assert "Implement cache layer" in result.output
