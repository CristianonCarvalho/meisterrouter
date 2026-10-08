from __future__ import annotations

import re

import pytest

from meister.locales.en_cli import MESSAGES
from tests.timeline_js import extract_helpers, run_js

PREFIX = "cli.timeline.web."
EN_TEXT = {key[len(PREFIX):]: value for key, value in MESSAGES.items() if key.startswith(PREFIX)}


@pytest.mark.parametrize(
    ("expression", "expected"),
    [
        ("formatDuration(5)", "5s"),
        ("formatDuration(75)", "1m 15s"),
        ("formatDuration(3700)", "1h 1m"),
        ("formatDuration(NaN)", "—"),
    ],
)
def test_format_duration_uses_page_text(expression: str, expected: str) -> None:
    assert run_js(expression, EN_TEXT) == expected


def test_helpers_block_appears_once_in_timeline_html() -> None:
    from tests.timeline_js import TIMELINE_TEMPLATE

    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")
    assert source.count("// helpers:begin") == 1
    assert source.count("// helpers:end") == 1


def test_helpers_block_has_no_browser_dependencies() -> None:
    block = extract_helpers()
    assert "formatDuration" in block
    assert not re.search(r"\bdocument\b", block)
    assert not re.search(r"\bwindow\b", block)
    assert "innerHTML" not in block


def test_extract_helpers_reports_missing_markers(tmp_path) -> None:
    page = tmp_path / "page.html"
    page.write_text("<script>function f() {}</script>", encoding="utf-8")
    with pytest.raises(ValueError, match="helpers:begin"):
        extract_helpers(page)
