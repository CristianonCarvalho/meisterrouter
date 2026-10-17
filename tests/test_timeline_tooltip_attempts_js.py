from __future__ import annotations

import json

import pytest

from typing import Any

from meister.locales.en_cli import MESSAGES as EN
from meister.locales.pt_br_cli import MESSAGES as PT
from tests.timeline_js import TIMELINE_TEMPLATE, run_js

PREFIX = "cli.timeline.web."
EN_TEXT = {key[len(PREFIX):]: value for key, value in EN.items() if key.startswith(PREFIX)}
PT_TEXT = {key[len(PREFIX):]: value for key, value in PT.items() if key.startswith(PREFIX)}
NEW_KEYS = (
    "tooltip_attempt",
    "tooltip_reused_from",
    "tooltip_original_duration",
    "outcome_completed",
    "outcome_error",
    "outcome_quota",
    "outcome_timeout",
    "outcome_gate_rejected",
    "outcome_scope",
)


def _js(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _lines(row: object, text: dict[str, str] = EN_TEXT) -> list[str]:
    return run_js(f"taskTooltipExtraLines({_js(row)})", text)  # type: ignore[return-value]


def _placement_row(row: object, via: str) -> dict[str, Any]:
    return run_js(f"placementTooltipRow({_js(row)}, {_js(via)})")  # type: ignore[return-value]


def _same_object(row: object, via: str) -> bool:
    return bool(
        run_js(
            f"(() => {{ const r = {_js(row)}; return placementTooltipRow(r, {_js(via)}) === r; }})()"
        )
    )


def test_single_completed_attempt() -> None:
    row = {"attempt_details": [{"via": "tier_1", "outcome": "completed", "reason": None, "detail": None}]}
    assert _lines(row) == ["Attempt 1: tier_1, completed"]


def test_fallback_lists_each_attempt_with_reason_and_detail() -> None:
    row = {
        "attempt_details": [
            {"via": "tier_1", "outcome": "quota", "reason": "quota_error", "detail": "rate limit hit"},
            {"via": "tier_1b", "outcome": "completed", "reason": None, "detail": None},
        ]
    }
    assert _lines(row) == [
        "Attempt 1: tier_1, quota, quota_error (rate limit hit)",
        "Attempt 2: tier_1b, completed",
    ]


def test_attempt_lines_are_translated() -> None:
    row = {"attempt_details": [{"via": "tier_2", "outcome": "gate_rejected", "reason": "gate", "detail": None}]}
    assert _lines(row, PT_TEXT) == ["Tentativa 1: tier_2, rejeitada no gate, gate"]


def test_detail_equal_to_reason_is_shown_once() -> None:
    row = {"attempt_details": [{"via": "tier_1", "outcome": "error", "reason": "boom", "detail": "boom"}]}
    assert _lines(row) == ["Attempt 1: tier_1, error, boom"]


def test_very_long_detail_is_cut() -> None:
    detail = "x" * 500
    row = {"attempt_details": [{"via": "tier_1", "outcome": "scope", "reason": "scope", "detail": detail}]}
    (line,) = _lines(row)  # type: ignore[misc]
    assert line.startswith("Attempt 1: tier_1, outside scope, scope (")
    assert line.endswith("…)")
    assert len(line) < 200


def test_unknown_outcome_is_shown_raw_and_missing_fields_skipped() -> None:
    row = {"attempt_details": [{"outcome": "weird"}, None]}
    assert _lines(row) == ["Attempt 1: weird", "Attempt 2"]


def test_old_log_without_attempts_has_no_lines() -> None:
    assert _lines({"attempts": 2}) == []
    assert _lines({}) == []
    assert _lines(None) == []


def test_reused_task_with_original_duration() -> None:
    row = {"reused_from": "abcdef0123456789", "reused_duration_s": 125}
    assert _lines(row) == ["Reused from abcdef01", "Original duration: 2m 5s"]


def test_reused_task_without_duration() -> None:
    row = {"reused_from": "abcdef0123456789", "reused_duration_s": None}
    assert _lines(row, PT_TEXT) == ["Reaproveitada de abcdef01"]


@pytest.mark.parametrize("keys", [NEW_KEYS])
def test_labels_exist_in_both_catalogs(keys: tuple[str, ...]) -> None:
    for key in keys:
        assert PREFIX + key in EN
        assert PREFIX + key in PT


def test_english_labels_have_no_accents() -> None:
    for key in NEW_KEYS:
        assert EN[PREFIX + key].isascii()


def test_tooltip_writes_extra_lines_with_text_content() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")
    body = source[source.index("function showTaskTooltip"):source.index("function showLaneTooltip")]
    assert "taskTooltipExtraLines(row)" in body
    assert "innerHTML" not in body


MULTI_VIA_ROW: dict[str, Any] = {
    "task_id": "task-test",
    "tier": "tier_1",
    "status": "completed",
    "start": "2026-10-17T10:00:00Z",
    "end": "2026-10-17T10:01:00Z",
    "segments": [
        {"via": "tier_1", "phase": "worker", "start": "2026-10-17T10:00:00Z", "end": "2026-10-17T10:00:10Z"},
        {"via": "tier_1", "phase": "worker", "start": "2026-10-17T10:00:10Z", "end": "2026-10-17T10:00:20Z"},
        {"via": "tier_1b", "phase": "worker", "start": "2026-10-17T10:00:20Z", "end": "2026-10-17T10:00:35Z"},
        {"via": "tier_2", "phase": "worker", "start": "2026-10-17T10:00:35Z", "end": "2026-10-17T10:01:00Z"},
    ],
    "attempt_details": [
        {"via": "tier_1", "outcome": "error", "reason": "syntax error", "detail": "fail 1"},
        {"via": "tier_1", "outcome": "quota", "reason": "rate limit", "detail": "fail 2"},
        {"via": "tier_1b", "outcome": "error", "reason": "model error", "detail": "fail 3"},
        {"via": "tier_2", "outcome": "completed", "reason": None, "detail": None},
    ],
}


def test_placement_tooltip_row_tier_1b_isolates_attempt_and_numbers() -> None:
    res = _placement_row(MULTI_VIA_ROW, "tier_1b")
    assert len(res["attempt_details"]) == 1
    attempt = res["attempt_details"][0]
    assert attempt["via"] == "tier_1b"
    assert attempt["n"] == 3
    assert res["status"] == "error"
    assert res["end"] == "2026-10-17T10:00:35Z"
    lines = _lines(res)
    assert len(lines) == 1
    assert lines[0].startswith("Attempt 3:")
    assert lines[0] == "Attempt 3: tier_1b, error, model error (fail 3)"


def test_placement_tooltip_row_tier_1_brings_two_attempts_and_last_outcome() -> None:
    res = _placement_row(MULTI_VIA_ROW, "tier_1")
    assert len(res["attempt_details"]) == 2
    assert [a["n"] for a in res["attempt_details"]] == [1, 2]
    assert res["status"] == "quota"
    assert res["end"] == "2026-10-17T10:00:20Z"
    lines = _lines(res)
    assert len(lines) == 2
    assert lines[0].startswith("Attempt 1:")
    assert lines[1].startswith("Attempt 2:")


def test_placement_tooltip_row_last_placement_preserves_task_status_and_end() -> None:
    res = _placement_row(MULTI_VIA_ROW, "tier_2")
    assert len(res["attempt_details"]) == 1
    assert [a["n"] for a in res["attempt_details"]] == [4]
    assert res["status"] == MULTI_VIA_ROW["status"]
    assert res["end"] == MULTI_VIA_ROW["end"]
    lines = _lines(res)
    assert len(lines) == 1
    assert lines[0].startswith("Attempt 4:")


def test_placement_tooltip_row_single_via_returns_same_object() -> None:
    row = {
        "task_id": "single-task",
        "tier": "tier_1",
        "status": "completed",
        "start": "2026-10-17T10:00:00Z",
        "end": "2026-10-17T10:00:10Z",
        "segments": [{"via": "tier_1", "phase": "worker", "start": "2026-10-17T10:00:00Z", "end": "2026-10-17T10:00:10Z"}],
        "attempt_details": [{"via": "tier_1", "outcome": "completed"}],
    }
    assert _same_object(row, "tier_1") is True


def test_placement_tooltip_row_nonexistent_or_empty_via_returns_same_object() -> None:
    assert _same_object(MULTI_VIA_ROW, "tier_99") is True
    assert _same_object(MULTI_VIA_ROW, "") is True


def test_attempt_tooltip_line_uses_positive_integer_n() -> None:
    assert run_js(f"attemptTooltipLine({_js({'via': 'tier_1', 'n': 3})}, 0)", EN_TEXT) == "Attempt 3: tier_1"
    assert run_js(f"attemptTooltipLine({_js({'via': 'tier_1', 'n': 0})}, 0)", EN_TEXT) == "Attempt 1: tier_1"
    assert run_js(f"attemptTooltipLine({_js({'via': 'tier_1', 'n': -1})}, 0)", EN_TEXT) == "Attempt 1: tier_1"
    assert run_js(f"attemptTooltipLine({_js({'via': 'tier_1'})}, 2)", EN_TEXT) == "Attempt 3: tier_1"

