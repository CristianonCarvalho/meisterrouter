from __future__ import annotations

import pytest

from tests.timeline_js import TIMELINE_TEMPLATE, run_js


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (0, "+0s"),
        (59, "+59s"),
        (60, "+1m"),
        (90, "+1m30s"),
        (3600, "+1h00m"),
        (3725, "+1h02m"),
        (-5, "+0s"),
        ("NaN", "+0s"),
    ],
)
def test_format_tick_label(seconds: object, expected: str) -> None:
    expression = f"formatTickLabel({'NaN' if seconds == 'NaN' else seconds})"
    assert run_js(expression) == expected


def test_axis_no_longer_builds_label_by_string_concatenation() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")
    assert '"+" + tSec + "s"' not in source
    assert "formatTickLabel(tSec)" in source
