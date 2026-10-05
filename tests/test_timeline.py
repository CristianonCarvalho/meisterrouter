from meister.timeline import build_timeline
from tests.timeline_fixtures import at, ev, parallel_events, phase


def _row(timeline, task_id):
    return next(row for row in timeline.rows if row.task_id == task_id)


def _spans(row):
    return [(segment.phase, segment.start, segment.end) for segment in row.segments]


def test_phase_start_is_end_minus_duration_and_integrate_excludes_gates():
    timeline = build_timeline(parallel_events(), "r1", at(30))
    assert _spans(_row(timeline, "task_1")) == [
        ("worker", at(1), at(11)),
        ("gate", at(11), at(12)),
        ("lock_wait", at(12), at(12)),
        ("integrate", at(12), at(13)),
    ]
    assert _row(timeline, "task_1").status == "completed"
    assert _row(timeline, "task_1").duration_s == 12.0


def test_parallel_tasks_overlap_and_summary_counts():
    timeline = build_timeline(parallel_events(), "r1", at(30))
    assert [row.task_id for row in timeline.rows] == ["task_1", "task_2", "task_3"]
    assert [row.title for row in timeline.rows] == ["Task 1: Base", "Task 2: API", "Task 3: Docs"]
    summary = timeline.summary
    assert (summary.total, summary.completed, summary.failed, summary.running) == (3, 2, 0, 1)
    assert summary.peak_parallel == 2
    assert round(summary.avg_parallel, 2) == 1.42
    assert summary.cost_usd == 0.75


def test_running_task_has_inferred_open_segment_and_wait_for_dependencies():
    timeline = build_timeline(parallel_events(), "r1", at(30))
    row = _row(timeline, "task_3")
    assert row.status == "running" and row.depends_on == ["task_1", "task_2"]
    assert _spans(row) == [("wait", at(0), at(23.5)), ("worker", at(23.5), None)]
    assert row.segments[-1].inferred is True
    assert timeline.status == "running" and timeline.ended_at is None


def test_open_phase_is_inferred_from_last_recorded_phase():
    base = [ev("worker_spawn", "t", 1, tier="x")]
    after_worker = build_timeline(base + [phase("t", 11, "worker", 10)], "r1", at(20))
    assert _spans(_row(after_worker, "t"))[-1] == ("gate", at(11), None)
    after_gate = build_timeline(
        base + [phase("t", 11, "worker", 10), phase("t", 12, "gate", 1)], "r1", at(20)
    )
    assert _spans(_row(after_gate, "t"))[-1] == ("integrate", at(12), None)
    nothing = build_timeline(base, "r1", at(20))
    assert _spans(_row(nothing, "t")) == [("worker", at(1), None)]


def test_retry_adds_attempt_wait_segment_and_status_follows_last_attempt():
    events = [
        ev("worker_spawn", "t", 1, tier="x"),
        phase("t", 5, "worker", 4),
        ev("subtask_rejected", "t", 6, reason="gate", exit_code=1),
        ev("worker_spawn", "t", 8, tier="x"),
    ]
    row = _row(build_timeline(events, "r1", at(10)), "t")
    assert row.attempts == 2 and row.status == "running"
    assert ("wait", at(6), at(8)) in _spans(row)

    failed = build_timeline(events[:3], "r1", at(10))
    row = _row(failed, "t")
    assert row.status == "failed" and row.failure == "gate"
    assert failed.summary.failed == 1


def test_ended_run_closes_unfinished_task_as_failed():
    events = [
        ev("orchestration_start", "orchestrator", 0, task="x"),
        ev("worker_spawn", "t", 1, tier="x"),
        ev("orchestration_end", "orchestrator", 9, status="failed"),
    ]
    timeline = build_timeline(events, "r1", at(100))
    row = _row(timeline, "t")
    assert timeline.status == "failed" and timeline.ended_at == at(9)
    assert row.status == "failed" and row.failure == "sem conclusão"
    assert row.segments[-1].end == at(9)


def test_run_without_end_stays_running():
    timeline = build_timeline([ev("worker_spawn", "t", 1, tier="x")], "r1", at(5000))
    assert timeline.status == "running"
    assert _row(timeline, "t").status == "running"


def test_old_log_without_plan_or_phases_uses_natural_order_and_single_bar():
    events = [
        ev("worker_spawn", "task_10", 1, tier="x"),
        ev("subtask_completed", "task_10", 5, tier="x"),
        ev("worker_spawn", "task_2", 1, tier="x"),
        ev("subtask_completed", "task_2", 4, tier="x"),
        ev("subtask_completed", "task_9", 6, tier="x"),
    ]
    timeline = build_timeline(events, "r1", at(10))
    assert [row.task_id for row in timeline.rows] == ["task_2", "task_9", "task_10"]
    legacy = _row(timeline, "task_9")
    assert legacy.status == "completed" and len(legacy.segments) == 1


def test_events_out_of_order_are_sorted_and_other_runs_ignored():
    events = list(reversed(parallel_events())) + parallel_events(run="other")
    timeline = build_timeline(events, "r1", at(30))
    assert _spans(_row(timeline, "task_1"))[0] == ("worker", at(1), at(11))
    assert len(timeline.rows) == 3


def test_run_without_events_is_empty_not_an_error():
    timeline = build_timeline([], "r1", at(0))
    assert timeline.rows == [] and timeline.summary.total == 0
    assert timeline.summary.cost_usd is None


def test_dependencies_ignore_non_json_task_text():
    events = [
        ev("orchestration_start", "orchestrator", 0, task="faça isto"),
        ev("worker_spawn", "t", 1, tier="x"),
    ]
    assert _row(build_timeline(events, "r1", at(2)), "t").depends_on == []
