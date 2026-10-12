from __future__ import annotations

from meister.timeline import build_timeline
from tests.timeline_fixtures import at, ev, phase


def _segments(events):
    timeline = build_timeline(events, "r1", at(120))
    row = next(row for row in timeline.rows if row.task_id == "t")
    return [(seg.phase, seg.start, seg.end) for seg in row.segments]


def _events(*phases, end_t):
    return [
        ev("orchestration_start", "orchestrator", 0, task="t"),
        ev("worker_spawn", "t", 1, tier="tier_1"),
        phase("t", 10, "worker", 9),
        *phases,
        ev("subtask_completed", "t", end_t, tier="tier_1"),
    ]


def test_short_gaps_between_gate_lock_wait_gate_are_not_drawn():
    events = _events(
        phase("t", 15, "gate", 4.5),  # 0,5 s antes do gate
        phase("t", 25, "lock_wait", 9),  # 1 s entre gate e lock_wait
        phase("t", 30, "gate", 5),  # 0 s entre lock_wait e gate
        end_t=30.5,
    )
    assert _segments(events) == [
        ("worker", at(1), at(10)),
        ("gate", at(10.5), at(15)),
        ("lock_wait", at(16), at(25)),
        ("gate", at(25), at(30)),
        ("integrate", at(30), at(30.5)),
    ]


def test_long_gap_between_phases_is_still_drawn():
    events = _events(
        phase("t", 15, "gate", 5),
        phase("t", 30, "lock_wait", 10),  # 5 s entre gate e lock_wait
        phase("t", 32, "gate", 2),
        end_t=33,
    )
    assert ("integrate", at(15), at(20)) in _segments(events)
    assert _segments(events)[-1] == ("integrate", at(32), at(33))


def test_simple_window_without_lock_wait_is_unchanged():
    events = _events(phase("t", 12, "gate", 2), end_t=13)
    assert _segments(events) == [
        ("worker", at(1), at(10)),
        ("gate", at(10), at(12)),
        ("integrate", at(12), at(13)),
    ]
