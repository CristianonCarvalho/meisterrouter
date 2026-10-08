from __future__ import annotations

import json
import re

from meister.dashboard.server import _timeline_messages
from meister.locales.en_cli import MESSAGES as EN_MESSAGES
from meister.locales.pt_br_cli import MESSAGES as PT_MESSAGES
from tests.timeline_js import TIMELINE_TEMPLATE, run_js

PREFIX = "cli.timeline.web."
SOUND_KEYS = ("sound_on", "sound_off", "sound_title", "shortcut_sound")


def _transitions(prev: object, rows: list) -> dict:
    expression = f"finishedTransitions({json.dumps(prev)}, {json.dumps(rows)})"
    return run_js(expression)  # type: ignore[return-value]


def test_first_load_plays_nothing_even_if_tasks_already_completed() -> None:
    rows = [{"task_id": "a", "status": "completed"}, {"task_id": "b", "status": "failed"}]
    result = _transitions(None, rows)
    assert result["completed"] == []
    assert result["failed"] == []
    assert result["next"] == {"a": "completed", "b": "failed"}


def test_empty_previous_map_is_treated_as_first_load() -> None:
    result = _transitions({}, [{"task_id": "a", "status": "completed"}])
    assert result["completed"] == []
    assert result["failed"] == []


def test_task_that_completes_between_reads_is_reported() -> None:
    result = _transitions({"a": "running"}, [{"task_id": "a", "status": "completed"}])
    assert result["completed"] == ["a"]
    assert result["failed"] == []


def test_reused_task_counts_as_completed() -> None:
    result = _transitions({"a": "running"}, [{"task_id": "a", "status": "reused"}])
    assert result["completed"] == ["a"]


def test_task_that_fails_between_reads_is_reported() -> None:
    result = _transitions({"a": "running"}, [{"task_id": "a", "status": "failed"}])
    assert result["failed"] == ["a"]
    assert result["completed"] == []


def test_task_already_completed_is_not_reported_again() -> None:
    result = _transitions({"a": "completed"}, [{"task_id": "a", "status": "completed"}])
    assert result["completed"] == []
    assert result["failed"] == []


def test_new_task_already_completed_is_silent() -> None:
    result = _transitions({"b": "running"}, [{"task_id": "a", "status": "completed"}])
    assert result["completed"] == []
    assert result["next"] == {"a": "completed"}


def test_run_switch_resets_previous_map_and_is_silent() -> None:
    result = _transitions(None, [{"task_id": "x", "status": "completed"}])
    assert result["completed"] == []
    assert result["failed"] == []


def test_next_map_covers_every_row() -> None:
    rows = [{"task_id": "a", "status": "running"}, {"task_id": "b", "status": "queued"}]
    assert _transitions({"a": "running", "b": "queued"}, rows)["next"] == {"a": "running", "b": "queued"}


def test_sound_keys_exist_in_both_catalogs() -> None:
    for key in SOUND_KEYS:
        assert PREFIX + key in EN_MESSAGES, key
        assert PREFIX + key in PT_MESSAGES, key


def test_english_sound_keys_have_no_accents() -> None:
    for key in SOUND_KEYS:
        assert EN_MESSAGES[PREFIX + key].isascii(), key


def test_sound_keys_reach_the_timeline_page() -> None:
    messages = _timeline_messages()
    for key in SOUND_KEYS:
        assert key in messages, key


def test_timeline_page_has_sound_toggle_and_s_shortcut() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")
    assert 'id="sound-toggle"' in source
    assert 'event.key.toLowerCase() === "s"' in source
    assert "shortcut_sound" in source
    assert "createOscillator" in source
    assert "innerHTML" not in source


def test_local_storage_is_only_used_inside_try_blocks() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")
    occurrences = [m.start() for m in re.finditer(r"localStorage", source)]
    assert occurrences, "localStorage deve guardar a preferência de som"
    for index in occurrences:
        last_try = source.rfind("try {", 0, index)
        next_catch = source.find("catch", index)
        assert last_try != -1 and next_catch != -1, index
        assert next_catch - index < 400, index


def _sound_preference(stored: object) -> object:
    return run_js(f"parseSoundPreference({json.dumps(stored)})")


def test_parse_sound_preference_is_on_only_for_on() -> None:
    assert _sound_preference("on") is True


def test_parse_sound_preference_defaults_to_off() -> None:
    assert run_js("parseSoundPreference(undefined)") is False
    assert run_js("parseSoundPreference(null)") is False
    assert _sound_preference("off") is False
    assert _sound_preference("") is False
    assert _sound_preference("yes") is False
    assert _sound_preference("ON") is False
