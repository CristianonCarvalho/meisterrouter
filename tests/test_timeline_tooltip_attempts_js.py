from __future__ import annotations

import json

import pytest

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


def _lines(row: object, text: dict[str, str] = EN_TEXT) -> object:
    return run_js(f"taskTooltipExtraLines({_js(row)})", text)


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
