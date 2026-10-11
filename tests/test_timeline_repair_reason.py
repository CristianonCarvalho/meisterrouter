from __future__ import annotations

from meister.i18n import t
from meister.timeline import build_timeline
from tests.timeline_fixtures import at, ev, phase


def _attempts(events):
    timeline = build_timeline(events, "r1", at(30))
    row = next(row for row in timeline.rows if row.task_id == "t")
    return row.attempt_details


def _repair_events(**gate_fields):
    return [
        ev("worker_spawn", "t", 1, tier="tier_1"),
        phase("t", 5, "worker", 4),
        ev("gate_repair", "t", 6, tier="tier_1", **gate_fields),
        ev("worker_retry", "t", 6, tier="tier_1", reason="gate_repair", retry=1),
        ev("worker_spawn", "t", 8, tier="tier_1"),
    ]


def test_gate_repair_scope_keeps_reason_and_detail():
    attempts = _attempts(_repair_events(reason="scope", detail="meister.config.yaml"))
    assert attempts[0].outcome == "scope"
    assert attempts[0].detail == "meister.config.yaml"


def test_gate_repair_gate_uses_failed_tests_reason():
    failed = ["tests/test_a.py::test_one"]
    attempts = _attempts(_repair_events(reason="gate", failed_tests=failed))
    assert attempts[0].outcome == "gate_rejected"
    assert attempts[0].reason == t(
        "gate_repair.timeline.failure_singular", tests="test_one"
    )


def test_retry_without_gate_repair_stays_error():
    events = [
        ev("worker_spawn", "t", 1, tier="tier_1"),
        phase("t", 5, "worker", 4),
        ev("worker_retry", "t", 6, tier="tier_1", reason="pane_lost", detail="sumiu"),
        ev("worker_spawn", "t", 8, tier="tier_1"),
    ]
    attempts = _attempts(events)
    assert attempts[0].outcome == "error"
    assert attempts[0].reason == "pane_lost"
    assert attempts[0].detail == "sumiu"
