from __future__ import annotations

import json

import pytest

from tests.config_js import run_js


def _js(value: object) -> str:
    return json.dumps(value)


def _lane(name: str, **extra: object) -> dict:
    return {"name": name, "harness": "codex", "model": f"m-{name}", "effort": "low", "enabled": True,
            "cost_per_m_tokens": 1.0, **extra}


CHAIN = [_lane("tier_1"), _lane("tier_1b"), _lane("tier_2", cost_per_m_tokens=4.0), _lane("tier_3")]


@pytest.mark.parametrize(
    ("name", "expected"),
    [("tier_1", 1), ("tier_1b", 1), ("tier_12c", 12), ("tier", None), ("tier_x", None), ("", None)],
)
def test_level_of(name: str, expected: int | None) -> None:
    assert run_js(f"levelOf({_js(name)})") == expected


def test_level_of_non_string_is_null() -> None:
    assert run_js("levelOf(undefined)") is None


def test_group_by_level_groups_consecutive_lanes() -> None:
    assert run_js(f"groupByLevel({_js(CHAIN)})") == [
        {"level": 1, "indexes": [0, 1]},
        {"level": 2, "indexes": [2]},
        {"level": 3, "indexes": [3]},
    ]
    assert run_js("groupByLevel([])") == []


def test_swap_content_swaps_only_content_fields_without_mutating() -> None:
    lanes = [
        _lane("a1", harness="claude", model="x", effort="high", credit_usd=5, max_parallel=1, eligible_classes=["HIGH"]),
        _lane("b2", cost_per_m_tokens=9.0, enabled=False, max_parallel=4),
    ]
    result = run_js(
        f"(() => {{ const lanes = {_js(lanes)}; const out = swapContent(lanes, 0, 1); "
        f"return {{out, kept: lanes[0].model}}; }})()"
    )
    assert result["kept"] == "x"
    first, second = result["out"]
    assert (first["name"], first["enabled"], first["max_parallel"]) == ("a1", True, 1)
    assert first["eligible_classes"] == ["HIGH"]
    assert (first["harness"], first["model"], first["effort"]) == ("codex", "m-b2", "low")
    assert first["cost_per_m_tokens"] == 9.0
    assert first.get("credit_usd") is None
    assert (second["name"], second["enabled"], second["max_parallel"]) == ("b2", False, 4)
    assert (second["harness"], second["model"], second["effort"]) == ("claude", "x", "high")
    assert second["cost_per_m_tokens"] == 1.0
    assert second["credit_usd"] == 5


def test_swap_content_ignores_same_or_invalid_index() -> None:
    assert run_js(f"swapContent({_js(CHAIN)}, 0, 0)") == CHAIN
    assert run_js(f"swapContent({_js(CHAIN)}, 0, 9)") == CHAIN


def test_level_cost_steps_uses_cheapest_enabled_lane_per_level() -> None:
    lanes = [
        _lane("tier_1", cost_per_m_tokens=2.0),
        _lane("tier_1b", cost_per_m_tokens=0.5),
        _lane("tier_1c", cost_per_m_tokens=0.1, enabled=False),
        _lane("tier_2", cost_per_m_tokens=4.0),
        _lane("tier_3", cost_per_m_tokens=2.0),
    ]
    assert run_js(f"levelCostSteps({_js(lanes)})") == [
        {"from": 1, "to": 2, "fromCost": 0.5, "toCost": 4.0, "ratio": 8.0},
        {"from": 2, "to": 3, "fromCost": 4.0, "toCost": 2.0, "ratio": 0.5},
    ]


def test_level_cost_steps_without_active_cost_has_null_ratio() -> None:
    lanes = [_lane("tier_1"), _lane("tier_2", enabled=False)]
    assert run_js(f"levelCostSteps({_js(lanes)})") == [
        {"from": 1, "to": 2, "fromCost": 1.0, "toCost": None, "ratio": None}
    ]
    assert run_js(f"levelCostSteps({_js(lanes[:1])})") == []


SIM = [
    _lane("tier_1", eligible_classes=["SMALL"]),
    _lane("tier_1b", eligible_classes=["SMALL", "MEDIUM"]),
    _lane("tier_2", enabled=False),
    _lane("tier_3", eligible_classes=["HIGH"]),
    _lane("tier_4", eligible_classes=[]),
]


def _sim(cls: str, start: str | None = None, failed: list[str] | None = None) -> dict:
    return run_js(f"simulatePath({_js(SIM)}, {_js(cls)}, {_js(start)}, {_js(failed or [])})")


def test_simulate_path_starts_at_first_eligible_lane_for_class() -> None:
    assert _sim("SMALL") == {"startIndex": 0, "executedIndex": 0, "tried": ["tier_1"], "escalated": False,
                             "state": "ok"}
    assert _sim("MEDIUM")["startIndex"] == 1
    assert _sim("HIGH")["startIndex"] == 3
    assert _sim("ESCALATE")["startIndex"] == 4


def test_simulate_path_chained_failures_fall_forward_skipping_disabled_and_ignoring_class() -> None:
    result = _sim("SMALL", failed=["tier_1", "tier_1b"])
    assert result == {"startIndex": 0, "executedIndex": 3, "tried": ["tier_1", "tier_1b", "tier_3"],
                      "escalated": True, "state": "ok"}


def test_simulate_path_all_failed() -> None:
    result = _sim("HIGH", failed=["tier_3", "tier_4"])
    assert result == {"startIndex": 3, "executedIndex": None, "tried": ["tier_3", "tier_4"], "escalated": True,
                      "state": "all_failed"}


def test_simulate_path_never_falls_backwards() -> None:
    assert _sim("SMALL", failed=["tier_4"])["executedIndex"] == 0
    assert _sim("HIGH", failed=["tier_3"])["tried"] == ["tier_3", "tier_4"]


def test_simulate_path_start_name_is_where_the_search_begins() -> None:
    assert _sim("SMALL", start="tier_1b")["startIndex"] == 1
    assert _sim("SMALL", start="tier_3")["startIndex"] == 4


def test_simulate_path_no_eligible_lane() -> None:
    lanes = [_lane("tier_1", eligible_classes=["SMALL"]), _lane("tier_2", eligible_classes=["HIGH"], enabled=False)]
    result = run_js(f"simulatePath({_js(lanes)}, 'HIGH', null, [])")
    assert result == {"startIndex": None, "executedIndex": None, "tried": [], "escalated": False,
                      "state": "no_eligible"}
    assert run_js("simulatePath([], 'SMALL', null, [])")["state"] == "no_eligible"


def test_next_lane_name_and_next_level_name() -> None:
    assert run_js(f"nextLaneName({_js(CHAIN)}, 1)") == "tier_1c"
    assert run_js(f"nextLaneName({_js(CHAIN)}, 2)") == "tier_2b"
    assert run_js(f"nextLaneName({_js(CHAIN)}, 5)") == "tier_5"
    assert run_js(f"nextLevelName({_js(CHAIN)})") == "tier_4"
    assert run_js("nextLevelName([])") == "tier_1"


def test_new_lane_from_copies_and_marks_new() -> None:
    source = _lane("tier_1", original_name="tier_1")
    result = run_js(f"(() => {{ const s = {_js(source)}; const c = newLaneFrom(s, 'tier_1c'); return [c, s]; }})()")
    copy, original = result
    assert copy["name"] == "tier_1c"
    assert copy["_new"] is True
    assert "original_name" not in copy
    assert copy["model"] == source["model"]
    assert original == source
    body = run_js(f"buildLanesBody('s', [newLaneFrom({_js(source)}, 'tier_1c')])")
    assert "original_name" not in body["lanes"][0]


def _codes(lanes: list[dict]) -> list[tuple]:
    issues = run_js(f"validateChain({_js(lanes)})")
    return [(issue["code"], issue["index"], issue.get("field")) for issue in issues]


def test_validate_chain_accepts_valid_chain() -> None:
    assert _codes(CHAIN) == []


def test_validate_chain_name_rule_only_for_new_or_renamed_lanes() -> None:
    legacy = [_lane("Legacy"), _lane("worker-a")]
    assert _codes(legacy) == []
    assert _codes([_lane("Legacy", original_name="old_1"), _lane("tier_2")]) == [("invalid_name", 0, "name")]
    assert _codes([_lane("tier_1"), _lane("bad", _new=True)]) == [("invalid_name", 1, "name")]


def test_validate_chain_level_order_only_when_a_lane_is_new_or_renamed() -> None:
    assert _codes([_lane("tier_2"), _lane("tier_1")]) == []
    assert _codes([_lane("tier_2"), _lane("tier_1", _new=True)]) == [("level_order", 1, "name")]
    assert _codes([_lane("tier_3"), _lane("tier_2", original_name="tier_9")]) == [("level_order", 1, "name")]


def test_validate_chain_duplicate_name() -> None:
    assert ("duplicate_name", 1, "name") in _codes([_lane("tier_1"), _lane("tier_1")])


def test_validate_chain_empty_model_and_numbers() -> None:
    lanes = [
        _lane("tier_1", model="  ", max_parallel=0, cost_per_m_tokens=-1, max_retries=1.5, idle_timeout_seconds=0),
        _lane("tier_2", max_parallel="x", credit_usd="", max_runtime_seconds=10, max_retries=0),
    ]
    assert _codes(lanes) == [
        ("empty_model", 0, "model"),
        ("invalid_number", 0, "max_parallel"),
        ("invalid_number", 0, "cost_per_m_tokens"),
        ("invalid_number", 0, "max_retries"),
        ("invalid_number", 0, "idle_timeout_seconds"),
        ("invalid_number", 1, "max_parallel"),
    ]


def test_validate_chain_new_lane_requires_cost() -> None:
    assert _codes([_lane("tier_1"), _lane("tier_2", _new=True, cost_per_m_tokens=None)]) == [
        ("required", 1, "cost_per_m_tokens")
    ]


def test_validate_chain_needs_an_enabled_lane() -> None:
    assert _codes([_lane("tier_1", enabled=False)]) == [("no_enabled_lane", None, None)]
    assert _codes([]) == [("no_enabled_lane", None, None)]


def test_diff_chain_no_changes() -> None:
    assert run_js(f"diffChain({_js(CHAIN)}, {_js(CHAIN)})") == {
        "added": [], "removed": [], "changed": [], "reordered": False,
    }


def test_diff_chain_added_removed_changed_and_renamed() -> None:
    current = [
        {**CHAIN[0], "model": "other", "enabled": False},
        {**CHAIN[2], "name": "tier_2z", "original_name": "tier_2"},
        {**CHAIN[3], "max_parallel": ""},
        _lane("tier_4", _new=True),
    ]
    result = run_js(f"diffChain({_js(CHAIN)}, {_js(current)})")
    assert result["added"] == ["tier_4"]
    assert result["removed"] == ["tier_1b"]
    assert result["changed"] == [
        {"name": "tier_1", "original_name": "tier_1", "fields": ["enabled", "model"]},
        {"name": "tier_2z", "original_name": "tier_2", "fields": ["name"]},
    ]
    assert result["reordered"] is False


def test_diff_chain_detects_reorder() -> None:
    result = run_js(f"diffChain({_js(CHAIN)}, moveLane({_js(CHAIN)}, 0, 1))")
    assert result["reordered"] is True
    assert result["added"] == [] and result["removed"] == []
    removed_only = run_js(f"diffChain({_js(CHAIN)}, {_js(CHAIN[1:])})")
    assert removed_only["removed"] == ["tier_1"]
    assert removed_only["reordered"] is False
