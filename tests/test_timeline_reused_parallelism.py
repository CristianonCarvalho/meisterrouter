from meister.timeline import build_timeline
from tests.timeline_fixtures import at, ev, parallel_events


def test_reused_tasks_ignored_in_parallelism_metrics():
    """Oito tasks reaproveitadas seguidas de uma em andamento dão pico 1 e média 1.0.

    As oito aparecem em completed e seus segmentos continuam nas linhas.
    """
    events = [ev("orchestration_start", "orchestrator", 0, run="r1")]
    for i in range(1, 9):
        events.append(
            ev(
                "subtask_reused",
                f"task_{i}",
                0.1 * i,
                run="r1",
                source_run_id="old_run",
                duration_s=15.0,
            )
        )
    events.append(ev("worker_spawn", "task_9", 1.0, run="r1", tier="tier_1b"))

    timeline = build_timeline(events, "r1", at(10))
    summary = timeline.summary

    assert summary.peak_parallel == 1
    assert summary.avg_parallel == 1.0
    assert summary.completed == 8
    assert summary.running == 1
    assert summary.total == 9

    reused_rows = [row for row in timeline.rows if row.task_id != "task_9"]
    assert len(reused_rows) == 8
    for row in reused_rows:
        assert row.status == "reused"
        assert len(row.segments) > 0


def test_run_without_reused_tasks_preserves_existing_parallelism():
    """Uma run sem tasks reaproveitadas mantém os valores de um caso existente."""
    timeline = build_timeline(parallel_events(), "r1", at(30))
    summary = timeline.summary

    assert (summary.total, summary.completed, summary.failed, summary.running) == (3, 2, 0, 1)
    assert summary.peak_parallel == 2
    assert round(summary.avg_parallel, 2) == 1.42


def test_run_with_only_reused_tasks_has_zero_parallelism():
    """Uma run somente com tasks reaproveitadas não consome workers (paralelismo 0)."""
    events = [ev("orchestration_start", "orchestrator", 0, run="r1")]
    for i in range(1, 9):
        events.append(ev("subtask_reused", f"task_{i}", 0.1 * i, run="r1"))

    timeline = build_timeline(events, "r1", at(10))
    summary = timeline.summary

    assert summary.peak_parallel == 0
    assert summary.avg_parallel == 0.0
    assert summary.completed == 8
    assert summary.running == 0
    assert summary.total == 8
