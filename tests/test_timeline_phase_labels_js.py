from __future__ import annotations

import json

from tests.timeline_js import run_js

TEXT = {
    "phase_worker": "worker",
    "phase_gate": "gate",
    "phase_integrate": "integrate",
    "phase_lock_wait": "integration queue",
    "phase_gate_task": "gate: task worktree",
    "phase_gate_integrated": "gate: after integrating the base",
}


def _labels(phases: list) -> list:
    segs = [{"phase": phase} for phase in phases]
    expression = (
        f"{json.dumps(segs)}.map((_, i, all) => phaseDisplayLabel(all, i, text))"
    )
    result = run_js(expression, TEXT)
    assert isinstance(result, list)
    return result


def test_row_with_two_gates_names_each_one() -> None:
    labels = _labels(["worker", "gate", "lock_wait", "gate", "integrate"])

    assert labels == [
        "worker",
        "gate: task worktree",
        "integration queue",
        "gate: after integrating the base",
        "integrate",
    ]


def test_row_with_a_single_gate_keeps_the_plain_label() -> None:
    assert _labels(["worker", "gate", "integrate"]) == ["worker", "gate", "integrate"]


def test_unknown_phases_come_back_raw() -> None:
    assert _labels(["wait", "queue", "mystery"]) == ["wait", "queue", "mystery"]


def test_out_of_range_index_is_undefined() -> None:
    assert run_js("phaseDisplayLabel([], 0, text) === undefined", TEXT) is True
