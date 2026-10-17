from __future__ import annotations

import json
from typing import Any

import pytest

from meister.dashboard.server import app
from meister.i18n import reset_language_cache, set_language
from meister.locales.en_cli import MESSAGES as EN
from meister.locales.pt_br_cli import MESSAGES as PT
from tests.timeline_js import BEGIN_MARKER, END_MARKER, TIMELINE_TEMPLATE, extract_helpers, run_js

KEY = "cli.timeline.web.legend_reused"


def _js(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


def _reused_marker_width(row: object) -> Any:
    return run_js(f"reusedMarkerWidth({_js(row)})")


def test_reused_marker_width_returns_36_for_reused() -> None:
    assert _reused_marker_width({"status": "reused"}) == 36


@pytest.mark.parametrize("status", ["completed", "running", "failed", "stalled", "pending"])
def test_reused_marker_width_returns_null_for_other_statuses(status: str) -> None:
    assert _reused_marker_width({"status": status}) is None


def test_reused_marker_width_returns_null_for_empty_and_none() -> None:
    assert _reused_marker_width({}) is None
    assert _reused_marker_width(None) is None


def test_helpers_block_appears_exactly_once() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")
    assert source.count(BEGIN_MARKER) == 1
    assert source.count(END_MARKER) == 1
    helpers = extract_helpers()
    assert "function reusedMarkerWidth" in helpers


def test_locale_keys_exist_in_both_catalogs() -> None:
    assert KEY in EN
    assert KEY in PT
    assert EN[KEY] == "Reused"
    assert PT[KEY] == "Reaproveitada"


def test_served_html_contains_swatch_reused_and_english_legend() -> None:
    set_language("en")
    try:
        client = app.test_client()
        response = client.get("/timeline")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "swatch-reused" in html
        assert "Reused" in html
    finally:
        reset_language_cache()


def test_served_html_contains_swatch_reused_and_portuguese_legend() -> None:
    set_language("pt-BR")
    try:
        client = app.test_client()
        response = client.get("/timeline")
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        assert "swatch-reused" in html
        assert "Reaproveitada" in html
    finally:
        reset_language_cache()
