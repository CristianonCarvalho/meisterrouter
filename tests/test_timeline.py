import json
from datetime import timedelta

from meister.timeline import build_timeline
from tests.timeline_fixtures import at, ev, parallel_events, phase, worker_cli_events


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
    events = [ev("worker_spawn", "t", 1, tier="x")]
    timeline = build_timeline(events, "r1", at(600))
    assert timeline.status == "running"
    assert _row(timeline, "t").status == "running"

    stalled = build_timeline(events, "r1", at(5000))
    assert stalled.status == "stalled" and stalled.stalled_since == at(1)
    assert _row(stalled, "t").status == "stalled"
    assert _row(stalled, "t").failure is None
    assert (_row(stalled, "t").segments[-1].start, _row(stalled, "t").segments[-1].end) == (
        at(1), at(1)
    )
    assert (stalled.summary.running, stalled.summary.stalled) == (0, 1)


def test_stalled_threshold_is_customizable_and_recent_events_restore_running():
    events = [
        ev("orchestration_start", "orchestrator", 0, task="plan"),
        ev("worker_spawn", "t", 1, tier="x"),
        phase("t", 10, "worker", 9),
    ]
    assert build_timeline(events, "r1", at(20), stale_after=timedelta(seconds=5)).status == "stalled"
    assert build_timeline(events, "r1", at(20), stale_after=timedelta(seconds=30)).status == "running"
    recent = events + [ev("route_decision", "orchestrator", 19)]
    assert build_timeline(recent, "r1", at(20)).status == "running"


def test_stalled_run_closes_waiting_segments_and_ended_runs_never_stall():
    events = [
        ev(
            "orchestration_start",
            "orchestrator",
            0,
            run="r1",
            task=json.dumps([
                {"id": "active", "depends_on": []},
                {"id": "waiting", "depends_on": ["active"]},
            ]),
        ),
        ev("worker_spawn", "active", 1, tier="x"),
        phase("active", 10, "worker", 9),
        ev(
            "plan_parsed",
            "orchestrator",
            0.1,
            task_ids=["active", "waiting"],
            task_titles={},
        ),
    ]
    stalled = build_timeline(events, "r1", at(5000))
    waiting = _row(stalled, "waiting")
    assert waiting.status == "waiting"
    assert _spans(waiting) == [("wait", at(0), at(10))]
    assert all(segment.end == at(10) for row in stalled.rows for segment in row.segments)

    completed = build_timeline(
        events + [ev("orchestration_end", "orchestrator", 11, status="completed")],
        "r1",
        at(5000),
    )
    assert completed.status == "completed" and completed.stalled_since is None
    failed = build_timeline(
        events + [ev("orchestration_end", "orchestrator", 11, status="failed")],
        "r1",
        at(5000),
    )
    assert failed.status == "failed" and failed.stalled_since is None


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
    assert timeline.jev.calls == []


def test_jev_lane_tracks_run_calls_cost_duration_and_worker_tier():
    events = [
        ev("classify", "fix_28", 2, tier="jev", duration_ms=1500, cost=0.01),
        ev("route_decision", "fix_28", 3, tier="agy_gemini_flash"),
        ev("worker_spawn", "fix_28", 4, tier="agy_gemini_flash"),
        ev("subtask_completed", "fix_28", 8, tier="agy_gemini_flash", cost=0.2),
        ev("control", "fix_28", 9, tier="jev", duration_ms=2000, cost_usd=0.02),
        ev("control", "other", 10, run="other", duration_ms=5000, cost=10),
    ]
    timeline = build_timeline(events, "r1", at(20))
    assert _row(timeline, "fix_28").tier == "agy_gemini_flash"
    assert timeline.jev.classify_count == 1
    assert timeline.jev.control_count == 1
    assert timeline.jev.cost_usd == 0.03
    assert [(call.kind, call.start, call.end, call.task_id) for call in timeline.jev.calls] == [
        ("classify", at(0.5), at(2), "fix_28"),
        ("control", at(7), at(9), "fix_28"),
    ]
    assert build_timeline([ev("worker_spawn", "task_1", 1, tier="worker")], "r1", at(2)).jev.calls == []


def test_dependencies_ignore_non_json_task_text():
    events = [
        ev("orchestration_start", "orchestrator", 0, task="faça isto"),
        ev("worker_spawn", "t", 1, tier="x"),
    ]
    assert _row(build_timeline(events, "r1", at(2)), "t").depends_on == []


def test_pane_lost_retry_closes_the_abandoned_attempt_without_a_phantom_phase():
    events = [
        ev("worker_spawn", "t", 1, tier="x"),
        phase("t", 5, "worker", 4),
        ev("worker_retry", "t", 5.1, reason="pane_lost"),
        ev("worker_spawn", "t", 8, tier="x"),
        phase("t", 20, "worker", 12),
        phase("t", 25, "gate", 5),
        ev("subtask_rejected", "t", 26, reason="gate"),
    ]
    timeline = build_timeline(events, "r1", at(60))
    assert _spans(_row(timeline, "t")) == [
        ("worker", at(1), at(5)),
        ("wait", at(5.1), at(8)),
        ("worker", at(8), at(20)),
        ("gate", at(20), at(25)),
        ("integrate", at(25), at(26)),
    ]
    assert timeline.summary.peak_parallel == 1  # uma tarefa nunca roda em paralelo consigo mesma


def test_announced_retry_without_a_new_attempt_is_running_then_failed_when_the_run_ends():
    events = [
        ev("worker_spawn", "t", 1, tier="x"),
        phase("t", 5, "worker", 4),
        ev("worker_retry", "t", 5.1, reason="pane_lost"),
    ]
    row = _row(build_timeline(events, "r1", at(10)), "t")
    assert row.status == "running" and row.end is None
    assert _spans(row)[-1] == ("wait", at(5.1), None) and row.segments[-1].inferred is True

    ended = events + [ev("orchestration_end", "orchestrator", 9, status="failed")]
    row = _row(build_timeline(ended, "r1", at(100)), "t")
    assert row.status == "failed" and row.failure == "sem conclusão"
    assert _spans(row)[-1] == ("wait", at(5.1), at(9))


def test_worker_cli_timeout_is_a_failed_task_and_closes_the_run():
    timeline = build_timeline(worker_cli_events(), "worker_cli", at(20))
    row = _row(timeline, "5b936c6b64348070")
    assert len(timeline.rows) == 1
    assert row.title == "" and row.tier == "copilot_luna"
    assert row.status == "failed" and "timeout" in row.failure
    assert _spans(row) == [("worker", at(1), at(8))]
    assert (timeline.summary.total, timeline.summary.failed) == (1, 1)
    assert timeline.ended_at == at(10) and timeline.status == "failed"
    assert timeline.summary.running == 0


def test_worker_cli_success_and_failure_statuses():
    success = [
        ev("worker_start", "success", 1, tier="copilot_luna"),
        phase("success", 5, "worker", 4),
        ev("worker_end", "success", 6, status="done"),
    ]
    timeline = build_timeline(success, "r1", at(20))
    assert _row(timeline, "success").status == "completed"
    assert timeline.status == "completed" and timeline.ended_at == at(6)

    for worker_status, error in (
        ("infrastructure_error", "infra falhou"),
        ("error", "erro do worker"),
        ("", ""),
    ):
        events = [ev("worker_start", "failed", 1, tier="copilot_luna")]
        terminal = ev("worker_end", "failed", 2, status=worker_status)
        if error:
            terminal["error"] = error
        events.append(terminal)
        row = _row(build_timeline(events, "r1", at(20)), "failed")
        assert row.status == "failed" and row.failure == (error or worker_status)


def test_worker_cli_open_task_runs_and_stalls_using_existing_timeout():
    events = [ev("worker_start", "open", 1, tier="copilot_luna")]
    running = build_timeline(events, "r1", at(20))
    row = _row(running, "open")
    assert running.status == row.status == "running"
    assert _spans(row) == [("worker", at(1), None)]
    assert row.segments[-1].inferred is True

    stalled = build_timeline(events, "r1", at(5000))
    assert stalled.status == "stalled"
    assert _row(stalled, "open").status == "stalled"


def test_worker_cli_run_waits_for_every_task_to_finish():
    events = [
        ev("worker_start", "done", 1, tier="copilot_luna"),
        ev("worker_start", "timed_out", 1.5, tier="copilot_luna"),
        ev("worker_end", "done", 3, status="done"),
    ]
    pending = build_timeline(events, "r1", at(10))
    assert pending.status == "running" and pending.ended_at is None
    assert pending.summary.completed == 1 and pending.summary.running == 1

    complete = build_timeline(
        events + [ev("worker_end", "timed_out", 4, status="timeout")], "r1", at(10)
    )
    assert complete.status == "failed" and complete.ended_at == at(4)
    assert (complete.summary.completed, complete.summary.failed) == (1, 1)


def test_worker_cli_start_does_not_duplicate_orchestrate_spawn():
    events = [
        ev("orchestration_start", "orchestrator", 0, task="plan"),
        ev("worker_start", "task", 1, tier="copilot_luna"),
        ev("worker_spawn", "task", 2, tier="copilot_luna"),
        phase("task", 5, "worker", 3),
        ev("subtask_completed", "task", 6, tier="copilot_luna"),
    ]
    row = _row(build_timeline(events, "r1", at(10)), "task")
    assert row.attempts == 1 and row.status == "completed"
    assert _spans(row) == [("worker", at(2), at(5)), ("integrate", at(5), at(6))]
