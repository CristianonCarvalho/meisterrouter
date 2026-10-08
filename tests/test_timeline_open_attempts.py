from __future__ import annotations

import json

from meister.timeline import Segment, build_timeline
from tests.timeline_fixtures import at, ev, phase


def _row(timeline, task_id):
    return next(row for row in timeline.rows if row.task_id == task_id)


def _spans(row):
    return [(segment.phase, segment.start, segment.end) for segment in row.segments]


def test_open_attempt_followed_by_a_new_attempt_ends_at_the_new_spawn():
    events = [
        ev("worker_spawn", "t", 1, tier="x"),
        ev("worker_spawn", "t", 8, tier="x"),
        phase("t", 20, "worker", 12),
        phase("t", 25, "gate", 5),
        ev("subtask_completed", "t", 26, tier="x"),
    ]
    row = _row(build_timeline(events, "r1", at(60)), "t")
    assert _spans(row) == [
        ("worker", at(1), at(8)),
        ("worker", at(8), at(20)),
        ("gate", at(20), at(25)),
        ("integrate", at(25), at(26)),
    ]
    first = row.segments[0]
    assert first.inferred is False
    assert all(segment.end != at(60) for segment in row.segments)
    assert not any(segment.phase == "wait" for segment in row.segments)
    assert row.status == "completed"


def test_open_attempt_does_not_add_wait_from_run_start_before_the_next_attempt():
    events = [
        ev("orchestration_start", "orchestrator", 0, task=json.dumps([{"id": "t", "depends_on": ["x"]}])),
        ev("worker_spawn", "t", 1, tier="x"),
        ev("worker_spawn", "t", 8, tier="x"),
        phase("t", 20, "worker", 12),
        ev("subtask_completed", "t", 21, tier="x"),
    ]
    row = _row(build_timeline(events, "r1", at(60)), "t")
    assert _spans(row) == [
        ("wait", at(0), at(1)),
        ("worker", at(1), at(8)),
        ("worker", at(8), at(20)),
        ("integrate", at(20), at(21)),
    ]


def test_single_open_attempt_is_still_inferred_until_now():
    events = [
        ev("worker_spawn", "t", 1, tier="x"),
        phase("t", 5, "worker", 4),
    ]
    row = _row(build_timeline(events, "r1", at(20)), "t")
    assert row.segments[-1] == Segment("gate", at(5), None, True)
    assert row.status == "running"


def test_open_attempt_followed_by_retry_rejection_then_open_attempt():
    events = [
        ev("worker_spawn", "t", 1, tier="x"),
        ev("worker_spawn", "t", 8, tier="x"),
        phase("t", 12, "worker", 4),
        ev("subtask_rejected", "t", 13, reason="gate", exit_code=1),
        ev("worker_spawn", "t", 14, tier="x"),
    ]
    row = _row(build_timeline(events, "r1", at(20)), "t")
    assert _spans(row) == [
        ("worker", at(1), at(8)),
        ("worker", at(8), at(12)),
        ("integrate", at(12), at(13)),
        ("wait", at(13), at(14)),
        ("worker", at(14), None),
    ]
    assert row.segments[-1].inferred is True
    assert row.status == "running" and row.attempts == 3


def test_two_open_attempts_before_a_completed_one():
    events = [
        ev("worker_spawn", "t", 1, tier="x"),
        ev("worker_spawn", "t", 8, tier="x"),
        phase("t", 12, "worker", 4),
        ev("worker_spawn", "t", 14, tier="x"),
        phase("t", 16, "worker", 2),
        ev("subtask_completed", "t", 17, tier="x"),
    ]
    timeline = build_timeline(events, "r1", at(60))
    row = _row(timeline, "t")
    assert _spans(row) == [
        ("worker", at(1), at(8)),
        ("worker", at(8), at(12)),
        ("gate", at(12), at(14)),
        ("worker", at(14), at(16)),
        ("integrate", at(16), at(17)),
    ]
    assert not any(segment.inferred for segment in row.segments)
    assert row.status == "completed" and row.attempts == 3
