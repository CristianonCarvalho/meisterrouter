from __future__ import annotations

import json

from meister.timeline import build_timeline
from tests.timeline_fixtures import PLAN, at, ev, parallel_events, phase


def _row(timeline, task_id):
    return next(row for row in timeline.rows if row.task_id == task_id)


def _start(**fields):
    return ev("orchestration_start", "orchestrator", 0, **fields)


def test_title_prefers_run_name():
    events = [_start(task=json.dumps(PLAN), run_name="Nome da run", run_name_derived="Derivado")]
    assert build_timeline(events, "r1", at(5)).title == "Nome da run"


def test_title_falls_back_to_derived_name():
    events = [_start(task=json.dumps(PLAN), run_name_derived="Task 1: Base")]
    assert build_timeline(events, "r1", at(5)).title == "Task 1: Base"


def test_title_of_raw_json_plan_is_never_the_raw_json():
    # log antigo (sem run_name): mantém o título de hoje, que o quadro do terminal congela
    assert build_timeline(parallel_events(), "r1", at(30)).title == ""
    plan = json.dumps([{"id": "a", "description": "Fazer X"}])
    assert build_timeline([_start(task=plan)], "r1", at(5)).title == "Fazer X"


def test_title_of_old_free_text_log_is_unchanged():
    events = [_start(task="Corrigir o login\nmais detalhes")]
    assert build_timeline(events, "r1", at(5)).title == "Corrigir o login"


def _fallback_events():
    return [
        _start(task="t"),
        ev("worker_spawn", "t", 1, tier="tier_1"),
        phase("t", 5, "worker", 4),
        ev(
            "quota_error",
            "t",
            6,
            tier="tier_1",
            exit_code=429,
            error="Terminal quota detected",
            matched_line="LINHA_X",
        ),
        ev("worker_spawn", "t", 8, tier="tier_1b"),
        phase("t", 15, "worker", 7),
        phase("t", 16, "gate", 1),
        phase("t", 17, "integrate", 1),
        ev("subtask_completed", "t", 17, tier="tier_1b"),
    ]


def test_fallback_has_two_attempts_with_via_and_outcome():
    row = _row(build_timeline(_fallback_events(), "r1", at(60)), "t")
    assert row.attempts == 2
    tries = row.attempt_details
    assert [(item.via, item.outcome) for item in tries] == [
        ("tier_1", "quota"),
        ("tier_1b", "completed"),
    ]
    assert tries[0].detail == "LINHA_X"
    assert (tries[0].start, tries[0].end) == (at(1), at(6))
    assert (tries[1].start, tries[1].end) == (at(8), at(17))
    assert tries[1].detail is None
    assert row.status == "completed"


def test_gap_after_quota_is_not_gate_and_segments_carry_via():
    row = _row(build_timeline(_fallback_events(), "r1", at(60)), "t")
    spans = [(seg.phase, seg.start, seg.end, seg.via) for seg in row.segments]
    assert spans == [
        ("worker", at(1), at(5), "tier_1"),
        ("wait", at(5), at(8), "tier_1"),
        ("worker", at(8), at(15), "tier_1b"),
        ("gate", at(15), at(16), "tier_1b"),
        ("integrate", at(16), at(17), "tier_1b"),
    ]


def test_rejection_outcomes_and_detail():
    events = [
        ev("worker_spawn", "a", 1, tier="tier_1"),
        phase("a", 5, "worker", 4),
        ev("subtask_rejected", "a", 6, reason="scope", error="fora", detail="x.py, y.py"),
        ev("worker_spawn", "b", 1, tier="tier_1"),
        phase("b", 5, "worker", 4),
        ev("subtask_rejected", "b", 6, reason="gate", error="ruff: E501 line too long"),
        ev("worker_spawn", "c", 1, tier="tier_1"),
        ev("worker_timeout", "c", 9, tier="tier_1", seconds=8),
        ev("worker_spawn", "d", 1, tier="tier_1"),
        ev("worker_error", "d", 3, tier="tier_1", error="boom"),
    ]
    timeline = build_timeline(events, "r1", at(60))
    got = {
        row.task_id: (row.attempt_details[0].outcome, row.attempt_details[0].detail)
        for row in timeline.rows
    }
    assert got == {
        "a": ("scope", "x.py, y.py"),
        "b": ("gate_rejected", "ruff: E501 line too long"),
        "c": ("timeout", None),
        "d": ("error", "boom"),
    }


def test_detail_is_limited_to_200_characters():
    events = [
        ev("worker_spawn", "t", 1, tier="tier_1"),
        ev("worker_error", "t", 3, tier="tier_1", error="e" * 500),
    ]
    row = _row(build_timeline(events, "r1", at(60)), "t")
    assert len(row.attempt_details[0].detail) == 200


def test_old_log_without_new_fields_still_works():
    timeline = build_timeline(parallel_events(), "r1", at(30))
    row = _row(timeline, "task_3")
    assert row.status == "running"
    assert [(item.via, item.outcome, item.end) for item in row.attempt_details] == [
        ("tier_1b", None, None)
    ]
    done = _row(timeline, "task_1")
    assert [(item.via, item.outcome) for item in done.attempt_details] == [
        ("tier_1b", "completed")
    ]
    assert {seg.via for seg in done.segments} == {"tier_1b"}
    assert _row(timeline, "task_1").segments[0].phase == "worker"


def test_open_attempt_after_quota_is_not_inferred_as_gate():
    events = _fallback_events()[:4]
    row = _row(build_timeline(events, "r1", at(7)), "t")
    assert row.segments[-1].phase == "wait" and row.segments[-1].end is None
    assert row.attempt_details[0].outcome == "quota"
