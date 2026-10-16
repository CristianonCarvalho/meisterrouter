from __future__ import annotations

from meister.i18n import t
from meister.timeline import build_timeline
from tests.timeline_fixtures import at, ev, phase
from tests.test_timeline_repair_reason import _attempts, _repair_events


def _terminal_attempts(**fields):
    events = [
        ev("worker_spawn", "t", 1, tier="tier_1"),
        phase("t", 5, "worker", 4),
        ev("subtask_rejected", "t", 6, tier="tier_1", reason="gate", **fields),
    ]
    timeline = build_timeline(events, "r1", at(30))
    row = next(row for row in timeline.rows if row.task_id == "t")
    return row, row.attempt_details


def test_pytest_failed_tests_keeps_reason():
    attempts = _attempts(
        _repair_events(reason="gate", failed_tests=["tests/test_a.py::test_one"])
    )
    assert attempts[0].reason == t("gate_repair.timeline.failure_singular", tests="test_one")


def test_js_failed_tests_plural_uses_last_segment():
    failed = [
        "ShareCard > renders > shows button",
        "ShareCard > copies > writes clipboard",
    ]
    attempts = _attempts(_repair_events(reason="gate", js_failed_tests=failed))
    assert attempts[0].reason == t(
        "gate_repair.timeline.failure_plural",
        count=2,
        tests="shows button, writes clipboard",
    )


def test_failure_summary_vitest_location_line():
    summary = (
        "\x1b[2m AssertionError: expected 'a' to be 'b'\x1b[22m\n"
        "  \x1b[31m❯\x1b[39m src/ShareCard.test.tsx:95:5\n"
    )
    attempts = _attempts(_repair_events(reason="gate", failure_summary=summary))
    assert attempts[0].reason == "❯ src/ShareCard.test.tsx:95:5"
    assert attempts[0].outcome == "gate_rejected"


def test_failure_summary_fail_line_wins_over_location():
    summary = (
        "\x1b[31mFAIL\x1b[39m  src/ShareCard.test.tsx > ShareCard > works\n"
        "  ❯ src/ShareCard.test.tsx:95:5\n"
    )
    attempts = _attempts(_repair_events(reason="gate", failure_summary=summary))
    assert attempts[0].reason == "FAIL  src/ShareCard.test.tsx > ShareCard > works"


def test_failure_summary_error_line_when_no_location():
    summary = "noise\n  TypeError: cannot read properties of undefined\n"
    attempts = _attempts(_repair_events(reason="gate", failure_summary=summary))
    assert attempts[0].reason == "TypeError: cannot read properties of undefined"


def test_failure_summary_without_known_pattern_keeps_old_reason():
    attempts = _attempts(
        _repair_events(reason="gate", failure_summary="something odd happened")
    )
    assert attempts[0].outcome == "gate_rejected"
    assert attempts[0].reason == "gate_repair"


def test_terminal_event_uses_js_failed_tests():
    row, attempts = _terminal_attempts(js_failed_tests=["Suite > case"])
    assert attempts[0].reason == t(
        "gate_repair.timeline.failure_singular", tests="case"
    )


def test_terminal_event_uses_failure_summary():
    row, attempts = _terminal_attempts(
        failure_summary="FAIL  src/a.test.ts:12:3 boom"
    )
    assert attempts[0].reason == "FAIL  src/a.test.ts:12:3 boom"
