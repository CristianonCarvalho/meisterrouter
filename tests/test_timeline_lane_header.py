from __future__ import annotations

import json

import pytest

from tests.timeline_js import TIMELINE_TEMPLATE, run_js


def _js(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


@pytest.mark.parametrize(
    ("effort", "expected"),
    [
        ("none", "N"),
        ("minimal", "MIN"),
        ("low", "L"),
        ("medium", "M"),
        ("high", "H"),
        ("xhigh", "XH"),
        ("max", "MAX"),
        ("ultra", "ULTRA"),
        ("", ""),
        (None, ""),
    ],
)
def test_effort_abbreviation_table(effort: str | None, expected: str) -> None:
    assert run_js(f"effortAbbreviation({_js(effort)})") == expected


def test_detail_text_has_harness_model_and_effort_abbreviation() -> None:
    lane = {"harness": "copilot", "model": "claude-haiku-5.5", "effort": "high"}
    assert run_js(f"laneDetailText({_js(lane)})") == "copilot · claude-haiku-5.5 (H)"


def test_detail_text_omits_missing_effort() -> None:
    lane = {"harness": "copilot", "model": "claude-haiku-5.5", "effort": None}
    assert run_js(f"laneDetailText({_js(lane)})") == "copilot · claude-haiku-5.5"


def test_detail_text_omits_missing_model() -> None:
    lane = {"harness": "agy", "model": None, "effort": "low"}
    assert run_js(f"laneDetailText({_js(lane)})") == "agy"


def test_detail_text_is_empty_without_harness_and_model() -> None:
    assert run_js(f"laneDetailText({_js({'effort': 'high'})})") == ""
    assert run_js(f"laneDetailText({_js({'via': 'unassigned'})})") == ""


def test_short_header_fits_on_one_line() -> None:
    lane = {"via": "tier_1b", "harness": "copilot", "model": "m", "effort": "low"}
    layout = run_js(f"laneHeaderLayout({_js(lane)}, 220, 6.2)")
    assert layout == {"name": "tier_1b", "detail": "copilot · m (L)", "twoLines": False}


def test_long_header_wraps_to_two_lines() -> None:
    lane = {
        "via": "tier_1b",
        "harness": "copilot",
        "model": "claude-haiku-5.5",
        "effort": "high",
    }
    layout = run_js(f"laneHeaderLayout({_js(lane)}, 220, 6.2)")
    assert layout["twoLines"] is True
    assert layout["name"] == "tier_1b"
    assert layout["detail"] == "copilot · claude-haiku-5.5 (H)"


def test_header_without_detail_is_one_line() -> None:
    layout = run_js(f"laneHeaderLayout({_js({'via': 'unassigned'})}, 220, 6.2)")
    assert layout == {"name": "unassigned", "detail": "", "twoLines": False}


def test_page_draws_lane_header_without_inner_html() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")
    assert "laneHeaderLayout(lane, LABEL_W, 6.2)" in source
    assert "showLaneTooltip(event, layout.name, layout.detail)" in source
    assert "innerHTML" not in source
