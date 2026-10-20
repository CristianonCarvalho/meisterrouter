from __future__ import annotations

import json

from tests.config_js import run_js


def _js(value: object) -> str:
    return json.dumps(value)


LANES = [
    {
        "name": "tier_1",
        "harness": "codex",
        "model": "gpt-6-luna",
        "cost_per_m_tokens": 0.2,
        "enabled": True,
        "original_name": "tier_1",
    },
    {
        "name": "tier_2",
        "harness": "claude",
        "model": "sonnet",
        "cost_per_m_tokens": 4,
        "enabled": True,
        "_new": True,
    },
]


def test_replicate_request_is_a_write_with_the_config_headers() -> None:
    request = run_js(f"replicateRequest('s', {_js(LANES)}, 'aaa111222333')")
    assert request["method"] == "POST"
    assert request["headers"] == {"Content-Type": "application/json", "X-Meister-Config": "1"}


def test_replicate_request_body_has_stamp_lanes_and_project() -> None:
    request = run_js(f"replicateRequest('s', {_js(LANES)}, 'aaa111222333')")
    body = json.loads(request["body"])
    assert body["stamp"] == "s"
    assert body["project"] == "aaa111222333"
    assert [lane["name"] for lane in body["lanes"]] == ["tier_1", "tier_2"]


def test_replicate_request_keeps_original_name_only_for_existing_lanes() -> None:
    request = run_js(f"replicateRequest('s', {_js(LANES)}, 'aaa111222333')")
    lanes = json.loads(request["body"])["lanes"]
    assert lanes[0]["original_name"] == "tier_1"
    assert "original_name" not in lanes[1]


def test_replicate_request_without_project_has_no_project_key() -> None:
    request = run_js(f"replicateRequest('s', {_js(LANES)}, null)")
    body = json.loads(request["body"])
    assert "project" not in body
    assert body["stamp"] == "s"


def test_replicate_request_does_not_mutate_the_lanes() -> None:
    result = run_js(
        f"(() => {{ const lanes = {_js(LANES)}; replicateRequest('s', lanes, 'aaa'); return lanes; }})()"
    )
    assert result == LANES


def test_replicate_summary_counts_each_state() -> None:
    results = [
        {"id": "a", "status": "saved"},
        {"id": "b", "status": "saved"},
        {"id": "c", "status": "skipped_run_active"},
        {"id": "d", "status": "skipped_missing"},
        {"id": "e", "status": "failed"},
    ]
    assert run_js(f"replicateSummary({_js(results)})") == {
        "saved": 2,
        "skipped_run_active": 1,
        "skipped_missing": 1,
        "failed": 1,
    }


def test_replicate_summary_of_nothing_is_all_zero() -> None:
    expected = {"saved": 0, "skipped_run_active": 0, "skipped_missing": 0, "failed": 0}
    assert run_js("replicateSummary([])") == expected
    assert run_js("replicateSummary(null)") == expected


def test_replicate_summary_ignores_unknown_states() -> None:
    results = [{"id": "a", "status": "weird"}, {"id": "b", "status": "failed"}, None]
    counts = run_js(f"replicateSummary({_js(results)})")
    assert counts["failed"] == 1
    assert sum(counts.values()) == 1
