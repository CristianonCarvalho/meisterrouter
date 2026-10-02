import io
import threading
from datetime import datetime, timedelta, timezone

from meister.logger import add_event_observer, log_event, remove_event_observer
from meister.progress import ProgressReporter, format_duration, format_event_line, render_summary
from meister.state import RunState, StateManager, SubtaskState


def test_format_duration():
    assert format_duration(28) == "28 s"
    assert format_duration(59) == "59 s"
    assert format_duration(125) == "2 min 05 s"
    assert format_duration(3720) == "1 h 02 min"


def test_format_event_line_for_each_event():
    assert format_event_line({"event_type": "plan_parsed", "run_id": "123456789", "total": 2, "batches": 1}) == (
        "Plano: 2 tarefas em 1 lotes (run 12345678)"
    )
    assert format_event_line({"event_type": "worker_spawn", "task_id": "task", "tier": "copilot"}, 1, 3) == (
        "[1/3] task iniciada em copilot"
    )
    assert format_event_line(
        {"event_type": "subtask_completed", "task_id": "task", "tier": "copilot", "duration": "28 s"}, 1, 3
    ) == "[1/3] task concluida em copilot (28 s)"
    assert format_event_line(
        {"event_type": "subtask_reused", "task_id": "task", "source_run_id": "abcdefgh123"}, 1, 3
    ) == "[1/3] task reaproveitada do run abcdefgh"
    assert format_event_line(
        {"event_type": "worker_retry", "task_id": "task", "tier": "copilot", "retry": 2, "max_retries": 3},
        1,
        3,
    ) == "[1/3] task pane perdido; retentativa 2/3 em copilot"
    assert format_event_line(
        {"event_type": "quota_error", "task_id": "task", "tier": "copilot", "next_tier": "agy"}, 1, 3
    ) == "[1/3] task cota esgotada em copilot; tentando agy"
    assert format_event_line(
        {"event_type": "quota_error", "task_id": "task", "tier": "copilot"}, 1, 3
    ) == "[1/3] task cota esgotada em copilot"
    for event_type in ("subtask_rejected", "worker_error", "gate_infrastructure_error"):
        assert format_event_line(
            {"event_type": event_type, "task_id": "task", "error": "falha\ndetalhada"}, 1, 3
        ) == "[1/3] task FALHOU: falha detalhada"
    assert format_event_line({"event_type": "worker_spawn", "task_id": "orchestrator"}, 1, 3) == (
        "[1/3] orchestrator iniciada em -"
    )


def test_reporter_uses_plan_order_ignores_unknown_tasks_and_measures_duration():
    stream = io.StringIO()
    reporter = ProgressReporter(stream=stream)
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    reporter.on_event({"event_type": "orchestration_start", "run_id": "run123456"})
    reporter.on_event({
        "event_type": "plan_parsed",
        "run_id": "run123456",
        "task_id": "orchestrator",
        "total": 2,
        "batches": 2,
        "task_ids": ["second", "first"],
    })
    reporter.on_event({"event_type": "worker_spawn", "task_id": "orchestrator", "tier": "ignored"})
    reporter.on_event({"event_type": "worker_spawn", "task_id": "first", "tier": "copilot", "ts": start.isoformat()})
    reporter.on_event({
        "event_type": "subtask_completed",
        "task_id": "first",
        "tier": "copilot",
        "ts": (start + timedelta(seconds=28)).isoformat(),
    })
    assert reporter.run_id == "run123456"
    assert reporter.durations == {"first": 28.0}
    assert stream.getvalue().splitlines() == [
        "Plano: 2 tarefas em 2 lotes (run run12345)",
        "[2/2] first iniciada em copilot",
        "[2/2] first concluida em copilot (28 s)",
    ]


def _make_run():
    state = StateManager(":memory:")
    run_id = "run-progress-test"
    state.create_or_get_run("test run", force_run_id=run_id)
    state.transition_run(run_id, RunState.RUNNING)
    return state, run_id


def test_render_summary_success_failure_reused_and_mixed():
    state, run_id = _make_run()
    state.add_subtasks(run_id, [
        {"id": "success", "description": "success"},
        {"id": "failed", "description": "failed"},
        {"id": "reused", "description": "reused"},
    ])
    rows = {row["step_id"]: row for row in state.get_subtasks(run_id)}
    state.transition_subtask(rows["success"]["subtask_id"], SubtaskState.RUNNING)
    state.transition_subtask(rows["success"]["subtask_id"], SubtaskState.COMPLETED, assigned_tier="copilot")
    state.transition_subtask(
        rows["failed"]["subtask_id"],
        SubtaskState.FAILED,
        assigned_tier="agy",
        error="violacao de escopo (src/index.ts)",
    )
    state.adopt_subtask(
        rows["reused"]["subtask_id"],
        source_run_id="origin-run-123",
        source_subtask_id="origin-task",
        integrated_sha="abc123",
        assigned_tier="copilot",
    )

    summary = render_summary(state, run_id, {"success": 28, "failed": 31}, 97)
    assert "3 tarefas | 1 concluidas | 1 falhou | 1 reaproveitadas | tempo total 1 min 37 s" in summary
    summary_lines = summary.splitlines()
    assert "success" in next(line for line in summary_lines if "success" in line)
    assert "concluida" in next(line for line in summary_lines if "success" in line)
    assert "28 s" in next(line for line in summary_lines if "success" in line)
    assert "FALHOU" in next(line for line in summary_lines if "failed" in line)
    assert "31 s" in next(line for line in summary_lines if "failed" in line)
    assert "violacao de escopo (src/index.ts)" in next(line for line in summary_lines if "failed" in line)
    assert "reaproveitada" in next(line for line in summary_lines if "reused" in line)
    assert "copilot" in next(line for line in summary_lines if "reused" in line)
    assert "run origin-" in summary
    assert "Proximo passo: corrija a causa e rode o mesmo comando (ou --resume se editou o plano)." in summary
    assert "$" not in summary


def test_render_summary_success_next_step():
    state, run_id = _make_run()
    state.add_subtasks(run_id, [{"id": "one", "description": "one"}])
    row = state.get_subtasks(run_id)[0]
    state.transition_subtask(row["subtask_id"], SubtaskState.RUNNING)
    state.transition_subtask(row["subtask_id"], SubtaskState.COMPLETED, assigned_tier="copilot")
    state.transition_run(run_id, RunState.COMPLETED)
    summary = render_summary(state, run_id, {"one": 28}, 28)
    assert "1 tarefas | 1 concluidas | 0 falhou | 0 reaproveitadas" in summary
    assert "Concluido: a main foi atualizada." in summary


def test_event_observer_errors_do_not_break_logging_and_removal_works(monkeypatch, tmp_path):
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path))
    observed = []

    def broken_observer(_record):
        raise RuntimeError("observer failed")

    def observer(record):
        observed.append(record["event_type"])

    add_event_observer(broken_observer)
    add_event_observer(observer)
    try:
        log_event("observed")
        remove_event_observer(observer)
        log_event("not_observed")
    finally:
        remove_event_observer(broken_observer)
        remove_event_observer(observer)
    assert observed == ["observed"]


def test_concurrent_progress_lines_are_not_interleaved(monkeypatch, tmp_path):
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path))
    stream = io.StringIO()
    reporter = ProgressReporter(stream=stream, total_hint=1)
    reporter.task_order = {"task": 1}
    reporter.total = 1
    add_event_observer(reporter.on_event)
    try:
        threads = [
            threading.Thread(
                target=log_event,
                kwargs={"event_type": "worker_error", "run_id": "threaded", "task_id": "task", "error": f"error {i}"},
            )
            for i in range(40)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
    finally:
        remove_event_observer(reporter.on_event)
    lines = stream.getvalue().splitlines()
    assert len(lines) == 40
    assert all(line.startswith("[1/1] task FALHOU: error ") for line in lines)
