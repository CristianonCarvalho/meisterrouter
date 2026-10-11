from __future__ import annotations

import json

import pytest

from tests.timeline_js import run_js


def _js(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _seg(phase: str, start: int, end: int, via: str | None = None) -> dict:
    seg = {"phase": phase, "start": f"2026-10-05T12:00:{start:02d}Z", "end": f"2026-10-05T12:00:{end:02d}Z"}
    if via is not None:
        seg["via"] = via
    return seg


def _attempt(via: str, outcome: str) -> dict:
    return {"via": via, "outcome": outcome, "reason": None, "detail": None}


def _placements(row: dict) -> list:
    result = run_js(f"rowLanePlacements({_js(row)})")
    assert isinstance(result, list)
    return result


def test_single_via_stays_on_the_row_tier() -> None:
    segments = [_seg("wait", 0, 1), _seg("worker", 1, 5, "tier_1"), _seg("gate", 5, 6, "tier_1")]
    row = {"tier": "tier_1", "segments": segments, "attempt_details": [_attempt("tier_1", "completed")]}

    placements = _placements(row)

    assert [p["via"] for p in placements] == ["tier_1"]
    assert placements[0]["segments"] == segments


def test_fallback_between_two_vias_splits_segments_by_via() -> None:
    segments = [
        _seg("wait", 0, 1),
        _seg("worker", 1, 5, "tier_1"),
        _seg("wait", 5, 8, "tier_1"),
        _seg("worker", 8, 15, "tier_1b"),
        _seg("gate", 15, 16, "tier_1b"),
    ]
    row = {
        "tier": "tier_1b",
        "segments": segments,
        "attempt_details": [_attempt("tier_1", "quota"), _attempt("tier_1b", "completed")],
    }

    placements = _placements(row)

    assert [p["via"] for p in placements] == ["tier_1", "tier_1b"]
    assert placements[0]["segments"] == segments[:3]
    assert placements[1]["segments"] == segments[3:]


def test_three_vias_keep_attempt_order() -> None:
    segments = [
        _seg("worker", 1, 3, "tier_1"),
        _seg("wait", 3, 4, "tier_1"),
        _seg("worker", 4, 6, "tier_2"),
        _seg("worker", 6, 8, "tier_3"),
        _seg("gate", 8, 9, "tier_3"),
    ]
    row = {
        "tier": "tier_3",
        "segments": segments,
        "attempt_details": [
            _attempt("tier_1", "error"),
            _attempt("tier_2", "gate_rejected"),
            _attempt("tier_3", "completed"),
        ],
    }

    placements = _placements(row)

    assert [p["via"] for p in placements] == ["tier_1", "tier_2", "tier_3"]
    assert [len(p["segments"]) for p in placements] == [2, 1, 2]


def test_old_log_without_attempts_draws_as_today() -> None:
    segments = [_seg("worker", 1, 5), _seg("gate", 5, 6)]
    row = {"tier": "tier_2", "segments": segments, "attempts": 1}

    placements = _placements(row)

    assert placements == [{"via": "tier_2", "segments": segments}]


def test_row_without_segments_or_tier() -> None:
    assert _placements({"tier": None}) == [{"via": None, "segments": []}]


@pytest.mark.parametrize(
    ("index", "expected"),
    [(0, False), (1, False), (2, True), (3, False)],
)
def test_failed_attempt_gap_is_wait_after_a_failed_attempt(index: int, expected: bool) -> None:
    row = {
        "segments": [
            _seg("wait", 0, 1),
            _seg("worker", 1, 5, "tier_1"),
            _seg("wait", 5, 8, "tier_1"),
            _seg("worker", 8, 15, "tier_1b"),
        ],
        "attempt_details": [_attempt("tier_1", "quota"), _attempt("tier_1b", "completed")],
    }
    assert run_js(f"isFailedAttemptGap({_js(row)}, {index})") is expected


def test_failed_attempt_gap_needs_attempt_details() -> None:
    row = {"segments": [_seg("worker", 1, 5, "tier_1"), _seg("wait", 5, 8, "tier_1")]}
    assert run_js(f"isFailedAttemptGap({_js(row)}, 1)") is False


@pytest.mark.parametrize(
    ("src", "dst", "expected"),
    [
        ("tier_1", "tier_1b", "lateral"),
        ("tier_1b", "tier_1", "lateral"),
        ("tier_1", "tier_2", "escalation"),
        ("tier_2b", "tier_3", "escalation"),
        ("codex", "claude", "escalation"),
    ],
)
def test_bridge_kind_distinguishes_lateral_fallback(src: str, dst: str, expected: str) -> None:
    assert run_js(f"bridgeKind({_js(src)}, {_js(dst)})") == expected


def test_placement_bridges_link_consecutive_lanes() -> None:
    row = {
        "tier": "tier_2",
        "segments": [
            _seg("worker", 1, 3, "tier_1"),
            _seg("worker", 4, 6, "tier_1b"),
            _seg("worker", 6, 8, "tier_2"),
        ],
        "attempt_details": [
            _attempt("tier_1", "error"),
            _attempt("tier_1b", "error"),
            _attempt("tier_2", "completed"),
        ],
    }
    bridges = run_js(f"placementBridges({_js(row)})")
    assert bridges == [
        {"from_via": "tier_1", "to_via": "tier_1b", "at": "2026-10-05T12:00:04Z", "kind": "lateral"},
        {"from_via": "tier_1b", "to_via": "tier_2", "at": "2026-10-05T12:00:06Z", "kind": "escalation"},
    ]


def test_placement_bridges_follow_a_return_to_an_earlier_via() -> None:
    row = {
        "tier": "tier_1",
        "segments": [_seg("worker", 1, 3, "tier_1"), _seg("worker", 4, 6, "tier_2"), _seg("worker", 7, 8, "tier_1")],
        "attempt_details": [_attempt("tier_1", "error"), _attempt("tier_2", "error"), _attempt("tier_1", "completed")],
    }
    assert [p["via"] for p in _placements(row)] == ["tier_1", "tier_2"]
    bridges = run_js(f"placementBridges({_js(row)})")
    assert [(b["from_via"], b["to_via"]) for b in bridges] == [("tier_1", "tier_2"), ("tier_2", "tier_1")]


def test_placement_bridges_empty_for_single_via() -> None:
    row = {"tier": "tier_1", "segments": [_seg("worker", 1, 3)]}
    assert run_js(f"placementBridges({_js(row)})") == []
