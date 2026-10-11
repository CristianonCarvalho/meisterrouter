from __future__ import annotations

import json

import pytest

from meister.locales.en_cli import MESSAGES
from tests.timeline_js import TIMELINE_TEMPLATE, run_js

PREFIX = "cli.timeline.web."
EN_TEXT = {key[len(PREFIX):]: value for key, value in MESSAGES.items() if key.startswith(PREFIX)}


def _js(value: object) -> str:
    return json.dumps(value, ensure_ascii=False)


@pytest.mark.parametrize(
    ("run", "expected"),
    [
        ({"id": "abcdef0123456789", "title": "Timeline names"}, "Timeline names"),
        ({"id": "abcdef0123456789", "title": "  spaced  "}, "spaced"),
        ({"id": "abcdef0123456789", "title": ""}, "abcdef01"),
        ({"id": "abcdef0123456789"}, "abcdef01"),
        ({"run_id": "1234567890ab", "title": None}, "12345678"),
        ({"id": "abcdef0123456789", "title": '[{"id": "task_1"}]'}, "abcdef01"),
        ({}, ""),
        (None, ""),
    ],
)
def test_run_display_name_falls_back_to_short_id(run: object, expected: str) -> None:
    assert run_js(f"runDisplayName({_js(run)})") == expected


@pytest.mark.parametrize(
    ("name", "limit", "expected"),
    [
        ("short", 10, "short"),
        ("exactly10!", 10, "exactly10!"),
        ("a longer run name", 10, "a longer …"),
        ("", 10, ""),
        ("abc", 0, "abc"),
    ],
)
def test_clip_run_name(name: str, limit: int, expected: str) -> None:
    assert run_js(f"clipRunName({_js(name)}, {limit})") == expected


def test_header_run_name_keeps_full_text_for_title_attribute() -> None:
    long_name = "x" * 200
    result = run_js(f"headerRunName({_js({'id': 'abcdef0123', 'title': long_name})})")
    assert isinstance(result, dict)
    assert result["full"] == long_name
    assert result["text"].endswith("…")
    assert len(result["text"]) < len(long_name)


def test_header_run_name_hidden_in_all_runs_mode_and_without_run() -> None:
    assert run_js("headerRunName(null)") == {"text": "", "full": ""}
    assert run_js(f"headerRunName({_js({'id': 'abcdef0123', 'title': 'Name'})}, 'all')") == {
        "text": "",
        "full": "",
    }


def test_document_title_includes_run_name() -> None:
    run = {"id": "abcdef0123", "title": "Timeline names"}
    assert run_js(f"documentTitle('proj', {_js(run)}, 'live')", EN_TEXT) == "Meister · proj · Timeline names"


def test_document_title_without_run_uses_waiting_text() -> None:
    assert run_js("documentTitle('proj', null, 'live')", EN_TEXT) == "Meister · proj · waiting for the first run"


def test_document_title_in_all_runs_mode_has_no_run_name() -> None:
    run = {"id": "abcdef0123", "title": "Timeline names"}
    assert run_js(f"documentTitle('proj', {_js(run)}, 'all')", EN_TEXT) == "Meister · proj · All runs"


def test_run_option_label_uses_name_and_status() -> None:
    run = {"run_id": "abcdef0123", "title": "Timeline names", "status": "completed"}
    assert run_js(f"runOptionLabel({_js(run)})", EN_TEXT) == "Timeline names (Completed)"


def test_run_option_label_never_shows_raw_json() -> None:
    run = {"run_id": "abcdef0123", "title": '[{"id": "task_1", "description": "x"}]'}
    assert run_js(f"runOptionLabel({_js(run)})", EN_TEXT) == "abcdef01"


def test_page_has_run_name_span_next_to_project_name() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")
    assert '<span id="project-name">' in source
    assert 'id="run-name"' in source
    assert source.index('id="project-name"') < source.index('id="run-name"')
    assert "#run-name" in source and "text-overflow: ellipsis" in source
