from __future__ import annotations

import json
import re

import pytest

from meister.harness_effort import HARNESS_EFFORT_VALUES
from meister.locales.en_cli import MESSAGES as EN_MESSAGES
from meister.locales.pt_br_cli import MESSAGES as PT_MESSAGES
from tests.config_js import CONFIG_TEMPLATE, extract_helpers, run_js

PREFIX = "cli.config.web."
LANES = [
    {"name": "a", "harness": "codex", "model": "m1", "effort": "low", "enabled": True, "max_parallel": 2,
     "cost_per_m_tokens": 1.0},
    {"name": "b", "harness": "claude", "model": "m2", "effort": None, "enabled": False},
    {"name": "c", "harness": "agy", "model": "m3", "effort": "high", "enabled": True, "max_parallel": None},
]


def _js(value: object) -> str:
    return json.dumps(value)


@pytest.mark.parametrize(
    ("index", "delta", "expected"),
    [
        (1, -1, ["b", "a", "c"]),
        (1, 1, ["a", "c", "b"]),
        (0, -1, ["a", "b", "c"]),
        (2, 1, ["a", "b", "c"]),
    ],
)
def test_move_lane(index: int, delta: int, expected: list[str]) -> None:
    result = run_js(f"moveLane({_js(LANES)}, {index}, {delta}).map((lane) => lane.name)")
    assert result == expected


def test_move_lane_does_not_mutate_input() -> None:
    expression = f"(() => {{ const lanes = {_js(LANES)}; moveLane(lanes, 0, 1); return lanes[0].name; }})()"
    assert run_js(expression) == "a"


def test_build_lanes_body_sends_all_editable_fields_and_original_name() -> None:
    lanes = [
        {**LANES[0], "max_parallel": "3", "effort": "", "original_name": "old_a", "cost_per_m_tokens": "2.5",
         "eligible_classes": ["SMALL"], "extra": "ignored"},
        {**LANES[1], "max_parallel": ""},
        {**LANES[2], "_new": True, "original_name": "stale"},
    ]
    body = run_js(f"buildLanesBody('stamp-1', {_js(lanes)})")
    empty = {"credit_usd": None, "max_retries": None, "idle_timeout_seconds": None, "max_runtime_seconds": None,
             "eligible_classes": None, "best_for": None}
    assert body == {
        "stamp": "stamp-1",
        "lanes": [
            {"name": "a", "enabled": True, "harness": "codex", "model": "m1", "effort": None,
             "cost_per_m_tokens": 2.5, "max_parallel": 3, **{**empty, "eligible_classes": ["SMALL"]},
             "original_name": "old_a"},
            {"name": "b", "enabled": False, "harness": "claude", "model": "m2", "effort": None,
             "cost_per_m_tokens": None, "max_parallel": None, **empty, "original_name": "b"},
            {"name": "c", "enabled": True, "harness": "agy", "model": "m3", "effort": "high",
             "cost_per_m_tokens": None, "max_parallel": None, **empty},
        ],
    }


def test_build_lanes_body_keeps_invalid_max_parallel_for_server_validation() -> None:
    body = run_js(f"buildLanesBody('s', [{{...{_js(LANES[0])}, max_parallel: 'x'}}])")
    assert body["lanes"][0]["max_parallel"] == "x"


def test_save_request_uses_put_json_and_csrf_header() -> None:
    request = run_js(f"saveRequest('s', {_js(LANES)})")
    assert request["method"] == "PUT"
    assert request["headers"] == {"Content-Type": "application/json", "X-Meister-Config": "1"}
    assert json.loads(request["body"])["stamp"] == "s"


def test_restore_request_sends_stamp_with_csrf_header() -> None:
    request = run_js("restoreRequest('s')")
    assert request["method"] == "POST"
    assert request["headers"] == {"Content-Type": "application/json", "X-Meister-Config": "1"}
    assert json.loads(request["body"]) == {"stamp": "s"}


def test_map_field_errors_groups_by_lane_and_field() -> None:
    fields = {
        "lanes[0].model": ["empty"],
        "workers.tier_order[2].harness": ["bad harness"],
        "lanes[0].max_parallel": ["positive"],
        "lanes": ["one enabled"],
        "stamp": ["stamp"],
    }
    result = run_js(f"mapFieldErrors({_js(fields)})")
    assert result == {
        "lanes": {"0": {"model": ["empty"], "max_parallel": ["positive"]}, "2": {"harness": ["bad harness"]}},
        "general": ["one enabled", "stamp"],
    }


def test_map_field_errors_accepts_missing_fields() -> None:
    assert run_js("mapFieldErrors(undefined)") == {"lanes": {}, "general": []}


def test_effort_options_follow_harness_effort_py() -> None:
    for harness, values in HARNESS_EFFORT_VALUES.items():
        assert run_js(f"effortOptions({_js(harness)})") == list(values)
    assert run_js("effortOptions('unknown')") == []
    assert run_js("harnessOptions()") == list(HARNESS_EFFORT_VALUES)


@pytest.mark.parametrize(
    ("status", "body", "expected"),
    [
        (200, {"lanes": []}, "saved"),
        (409, {"reason": "stale"}, "stale"),
        (409, {"reason": "run_active"}, "run_active"),
        (403, {"reason": "remote"}, "remote"),
        (422, {"reason": "invalid", "fields": {}}, "invalid"),
        (400, {"reason": "csrf"}, "error"),
        (500, None, "error"),
    ],
)
def test_save_outcome(status: int, body: object, expected: str) -> None:
    assert run_js(f"saveOutcome({status}, {_js(body)})") == expected


def test_lanes_changed_detects_edits_and_order() -> None:
    assert run_js(f"lanesChanged({_js(LANES)}, {_js(LANES)})") is False
    assert run_js(f"lanesChanged({_js(LANES)}, moveLane({_js(LANES)}, 0, 1))") is True
    assert run_js(f"lanesChanged({_js(LANES)}, [{{...{_js(LANES[0])}, model: 'x'}}, ...{_js(LANES[1:])}])") is True


def test_helpers_block_has_no_browser_dependencies() -> None:
    block = extract_helpers()
    assert "moveLane" in block
    assert not re.search(r"\bdocument\b", block)
    assert not re.search(r"\bwindow\b", block)
    assert "innerHTML" not in block


def test_config_page_uses_write_endpoints() -> None:
    source = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    assert "/api/config/lanes" in source
    assert "/api/config/restore-default" in source
    assert "alert(" not in source
    assert "confirm(" not in source


def test_config_web_catalogs_have_same_keys_and_ascii_english() -> None:
    en = {key for key in EN_MESSAGES if key.startswith(PREFIX)}
    pt = {key for key in PT_MESSAGES if key.startswith(PREFIX)}
    assert en == pt
    for key in en:
        assert EN_MESSAGES[key].isascii(), key
    for key in ("save", "revert", "restore_default", "stale", "reload", "project_list_notice"):
        assert PREFIX + key in en
