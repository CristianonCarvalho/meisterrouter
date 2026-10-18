from __future__ import annotations

import json

from tests.config_js import CONFIG_TEMPLATE, run_js


def _js(value: object) -> str:
    return json.dumps(value)


MODELS = [
    {"id": "gpt-6-luna", "label": "GPT-6 Luna", "cost_per_m_tokens": 0.2, "source": "codex cache"},
    {"id": "sonnet", "label": None, "cost_per_m_tokens": None, "source": "claude aliases"},
]


def test_model_known_finds_listed_id() -> None:
    assert run_js(f"modelKnown({_js(MODELS)}, 'gpt-6-luna')") is True


def test_model_known_is_false_for_unlisted_id() -> None:
    assert run_js(f"modelKnown({_js(MODELS)}, 'gpt-5')") is False


def test_model_known_is_false_for_empty_id_or_missing_list() -> None:
    assert run_js(f"modelKnown({_js(MODELS)}, '')") is False
    assert run_js("modelKnown(null, 'gpt-6-luna')") is False


def test_merge_catalog_choice_sets_id_and_known_cost() -> None:
    lane = {"name": "tier_1", "harness": "codex", "model": "old", "cost_per_m_tokens": 9}
    merged = run_js(f"mergeCatalogChoice({_js(lane)}, {_js(MODELS[0])})")
    assert isinstance(merged, dict)
    assert merged["model"] == "gpt-6-luna"
    assert merged["cost_per_m_tokens"] == 0.2
    assert merged["harness"] == "codex"
    assert merged["name"] == "tier_1"


def test_merge_catalog_choice_keeps_typed_cost_when_catalog_cost_is_null() -> None:
    lane = {"name": "tier_1", "harness": "claude", "model": "", "cost_per_m_tokens": 4.5}
    merged = run_js(f"mergeCatalogChoice({_js(lane)}, {_js(MODELS[1])})")
    assert isinstance(merged, dict)
    assert merged["model"] == "sonnet"
    assert merged["cost_per_m_tokens"] == 4.5


def test_merge_catalog_choice_does_not_mutate_the_lane() -> None:
    lane = {"name": "tier_1", "harness": "codex", "model": "old", "cost_per_m_tokens": 9}
    result = run_js(
        f"(() => {{ const lane = {_js(lane)}; mergeCatalogChoice(lane, {_js(MODELS[0])}); return lane; }})()"
    )
    assert result == lane


def test_template_fetches_models_once_per_harness_through_the_api() -> None:
    html = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    assert "/api/config/models?harness=${encodeURIComponent(harness)}" in html
    assert html.count("async function loadModels(") == 1
    assert "state.modelCache = new Map();" in html
