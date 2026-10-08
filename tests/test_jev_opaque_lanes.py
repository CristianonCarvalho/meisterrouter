"""Chaves opacas das vias enviadas ao Jev, traduzidas de volta para os nomes reais."""

import json
from typing import Callable, List
from unittest.mock import patch

from meister.config import WorkerTier
from meister.jev import _opaque_lane_keys, classify_task

REAL_NAMES = ["alpha-lane-real", "beta-lane-real", "gamma-lane-real", "delta-lane-real"]
IMPLEMENTERS = [
    WorkerTier(name=REAL_NAMES[0], harness="copilot", model="haiku-model", cost_per_m_tokens=0.2, best_for=["small_edits"]),
    WorkerTier(name=REAL_NAMES[1], harness="codex", model="luna-model", cost_per_m_tokens=0.2),
    WorkerTier(name=REAL_NAMES[2], harness="agy", model="flash-model", cost_per_m_tokens=1.5, best_for=["deep_reasoning"]),
    WorkerTier(name=REAL_NAMES[3], harness="claude", model="sonnet-model", cost_per_m_tokens=4.0),
]
DEFAULT_REAL_NAMES = ["tier_1", "tier_1b", "tier_2", "tier_3"]


def _jev_choosing(choice_of: Callable[[List[str]], object]):
    """Dublê de call_decisions que escolhe a via a partir das chaves recebidas."""

    def fake(**kwargs):
        criteria = kwargs["questions"]["recommended_implementer"]["criteria"]
        return {
            "answers": {
                "complexity": {"choice": "medium", "confidence": 0.9},
                "recommended_implementer": {"choice": choice_of(list(criteria)), "confidence": 0.9},
            },
            "usage": {},
        }

    return fake


def _classify(choice_of, implementers=IMPLEMENTERS):
    with patch("meister.jev.call_decisions", side_effect=_jev_choosing(choice_of)) as call:
        result = classify_task("Some configured task", implementers=implementers)
    return result, call


def test_opaque_keys_three_lanes():
    assert _opaque_lane_keys(3) == ["lane_a", "lane_b", "lane_c"]


def test_opaque_keys_ends_at_z():
    keys = _opaque_lane_keys(26)
    assert keys[0] == "lane_a"
    assert keys[-1] == "lane_z"


def test_opaque_keys_spreadsheet_style_after_z():
    keys = _opaque_lane_keys(28)
    assert keys[26] == "lane_aa"
    assert keys[27] == "lane_ab"


def test_opaque_keys_unique_deterministic_and_empty():
    keys = _opaque_lane_keys(100)
    assert len(set(keys)) == 100
    assert keys == _opaque_lane_keys(100)
    assert _opaque_lane_keys(0) == []


def test_opaque_keys_never_numeric():
    assert not any(any(ch.isdigit() for ch in key) for key in _opaque_lane_keys(100))


def test_criteria_sent_to_jev_are_opaque_keys_in_order():
    _, call = _classify(lambda keys: keys[0])
    criteria = call.call_args.kwargs["questions"]["recommended_implementer"]["criteria"]
    assert list(criteria) == ["lane_a", "lane_b", "lane_c", "lane_d"]


def test_real_names_never_reach_jev_payload():
    _, call = _classify(lambda keys: keys[0])
    dumped = json.dumps(call.call_args.kwargs["questions"], ensure_ascii=False)
    for name in REAL_NAMES:
        assert name not in dumped


def test_default_config_real_names_never_reach_jev_payload():
    with patch("meister.jev.call_decisions", side_effect=_jev_choosing(lambda keys: keys[0])) as call:
        classify_task("Default configured task")
    dumped = json.dumps(call.call_args.kwargs["questions"], ensure_ascii=False)
    for name in DEFAULT_REAL_NAMES:
        assert name not in dumped


def test_descriptions_keep_model_price_and_best_for():
    _, call = _classify(lambda keys: keys[0])
    criteria = call.call_args.kwargs["questions"]["recommended_implementer"]["criteria"]
    assert criteria["lane_a"] == "haiku-model via copilot ($0.2/M): small_edits"
    assert criteria["lane_c"] == "flash-model via agy ($1.5/M): deep_reasoning"


def test_choice_third_lane_translates_to_real_name_and_chain():
    result, _ = _classify(lambda keys: "lane_c")
    assert result["recommended_implementer"] == REAL_NAMES[2]
    assert result["fallback_chain"] == [REAL_NAMES[3]]
    assert result["fallback_rule_applied"] is False


def test_choice_first_lane_gives_all_others_as_chain():
    result, _ = _classify(lambda keys: "lane_a")
    assert result["recommended_implementer"] == REAL_NAMES[0]
    assert result["fallback_chain"] == REAL_NAMES[1:]


def test_real_name_choice_is_tolerated():
    result, _ = _classify(lambda keys: REAL_NAMES[2])
    assert result["recommended_implementer"] == REAL_NAMES[2]
    assert result["fallback_chain"] == [REAL_NAMES[3]]
    assert result["fallback_rule_applied"] is False


def test_unknown_choice_falls_back_to_first_lane():
    result, _ = _classify(lambda keys: "lane_z")
    assert result["recommended_implementer"] == REAL_NAMES[0]
    assert result["fallback_chain"] == REAL_NAMES[1:]
    assert result["fallback_rule_applied"] is True


def test_missing_choice_falls_back_to_first_lane():
    result, _ = _classify(lambda keys: None)
    assert result["recommended_implementer"] == REAL_NAMES[0]
    assert result["fallback_rule_applied"] is True


def test_only_enabled_lanes_are_offered():
    enabled = IMPLEMENTERS[:2]
    result, call = _classify(lambda keys: keys[1], implementers=enabled)
    criteria = call.call_args.kwargs["questions"]["recommended_implementer"]["criteria"]
    assert list(criteria) == ["lane_a", "lane_b"]
    assert "lane_c" not in criteria
    assert result["recommended_implementer"] == REAL_NAMES[1]
    assert result["fallback_chain"] == []


def test_telemetry_receives_real_name_not_key():
    with patch("meister.jev.log_classify") as log:
        _classify(lambda keys: "lane_b")
    assert log.call_args.kwargs["recommended_implementer"] == REAL_NAMES[1]


def test_api_unavailable_uses_real_names_in_deterministic_fallback():
    with patch("meister.jev.call_decisions", side_effect=RuntimeError("offline")):
        result = classify_task("Security migration", implementers=IMPLEMENTERS)
    assert result["api_unavailable"] is True
    assert result["fallback_rule_applied"] is True
    assert result["recommended_implementer"] == REAL_NAMES[0]
    assert result["fallback_chain"] == REAL_NAMES[1:]
