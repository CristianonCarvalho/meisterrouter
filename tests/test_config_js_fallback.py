from __future__ import annotations

import json
import re

from tests.config_js import CONFIG_TEMPLATE, run_js


def _js(value: object) -> str:
    return json.dumps(value)


def test_required_keys_covers_every_text_key_used_in_template() -> None:
    html = CONFIG_TEMPLATE.read_text(encoding="utf-8")
    used_keys = set(re.findall(r"\btext\.(\w+)", html))
    assert used_keys

    declared_keys = run_js("requiredKeys")
    assert isinstance(declared_keys, list)
    assert len(declared_keys) == len(set(declared_keys)), "requiredKeys must not contain duplicates"
    assert set(declared_keys) == used_keys


def test_missing_text_keys_returns_empty_when_all_present() -> None:
    text = {"a": "Alpha", "b": "Beta"}
    assert run_js(f"missingTextKeys({_js(text)}, ['a', 'b'])") == []


def test_missing_text_keys_returns_missing_keys_in_order() -> None:
    text = {"a": "Alpha", "c": "Gamma"}
    assert run_js(f"missingTextKeys({_js(text)}, ['a', 'b', 'c', 'd'])") == ["b", "d"]


def test_missing_text_keys_considers_undefined_values_as_missing() -> None:
    assert run_js("missingTextKeys({ a: 'ok', b: undefined }, ['a', 'b'])") == ["b"]


def test_missing_text_keys_handles_empty_or_null_target() -> None:
    assert run_js("missingTextKeys({}, ['x', 'y'])") == ["x", "y"]
    assert run_js("missingTextKeys(null, ['x', 'y'])") == ["x", "y"]
    assert run_js("missingTextKeys(undefined, ['x', 'y'])") == ["x", "y"]


def test_missing_text_keys_defaults_to_required_keys() -> None:
    all_keys = run_js("requiredKeys")
    assert isinstance(all_keys, list)
    assert run_js("missingTextKeys({})") == all_keys

    # With all keys provided, missingTextKeys defaults to requiredKeys and returns empty
    full_text = {k: f"val_{k}" for k in all_keys}
    assert run_js(f"missingTextKeys({_js(full_text)})") == []


def test_with_text_fallback_does_not_mutate_input() -> None:
    expression = (
        "(() => { "
        "  const orig = { a: 'orig_a' }; "
        "  const res = withTextFallback(orig, ['a', 'b']); "
        "  return { origHasB: ('b' in orig), resB: res.b }; "
        "})()"
    )
    result = run_js(expression)
    assert result == {"origHasB": False, "resB": "b"}


def test_with_text_fallback_preserves_existing_keys_and_extra_fields() -> None:
    text = {"a": "Custom A", "extra": "Keep Me"}
    result = run_js(f"withTextFallback({_js(text)}, ['a', 'b'])")
    assert result == {"a": "Custom A", "extra": "Keep Me", "b": "b"}


def test_with_text_fallback_fills_missing_keys_with_own_name() -> None:
    result = run_js("withTextFallback({}, ['btn_save', 'btn_cancel'])")
    assert result == {"btn_save": "btn_save", "btn_cancel": "btn_cancel"}


def test_with_text_fallback_handles_null_and_undefined() -> None:
    assert run_js("withTextFallback(null, ['foo'])") == {"foo": "foo"}
    assert run_js("withTextFallback(undefined, ['foo'])") == {"foo": "foo"}


def test_with_text_fallback_defaults_to_required_keys() -> None:
    all_keys = run_js("requiredKeys")
    assert isinstance(all_keys, list)

    partial = {"save": "Salvar"}
    result = run_js(f"withTextFallback({_js(partial)})")
    assert result["save"] == "Salvar"
    for k in all_keys:
        if k == "save":
            assert result[k] == "Salvar"
        else:
            assert result[k] == k
