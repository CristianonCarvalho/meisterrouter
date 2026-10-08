from __future__ import annotations

import json

import pytest

from meister.dashboard.server import _timeline_messages
from meister.locales.en_cli import MESSAGES as EN_MESSAGES
from meister.locales.pt_br_cli import MESSAGES as PT_MESSAGES
from tests.timeline_js import TIMELINE_TEMPLATE, run_js

TIMING_KEYS = (
    "cli.timeline.web.tooltip_phase_duration",
    "cli.timeline.web.tooltip_total_duration",
    "cli.timeline.web.tooltip_wait_duration",
)
NOW_MS = 1_700_000_000_000


def _iso(offset_seconds: int) -> str:
    from datetime import datetime, timedelta, timezone

    base = datetime.fromtimestamp(NOW_MS / 1000, tz=timezone.utc)
    return (base + timedelta(seconds=offset_seconds)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _timing(row: dict, seg: dict | None, now_ms: int = NOW_MS) -> dict:
    expression = f"taskTiming({json.dumps(row)}, {json.dumps(seg)}, {now_ms})"
    return run_js(expression)  # type: ignore[return-value]


def test_completed_task_reports_phase_total_and_wait() -> None:
    row = {
        "start": _iso(0),
        "end": _iso(100),
        "status": "completed",
        "segments": [
            {"phase": "wait", "start": _iso(0), "end": _iso(20)},
            {"phase": "worker", "start": _iso(20), "end": _iso(70)},
            {"phase": "gate", "start": _iso(70), "end": _iso(100)},
        ],
    }
    worker = row["segments"][1]
    assert _timing(row, worker) == {"phaseSec": 50, "totalSec": 80, "waitSec": 20}


def test_running_task_uses_now_for_open_segment_and_total() -> None:
    row = {
        "start": _iso(0),
        "end": None,
        "status": "running",
        "segments": [{"phase": "worker", "start": _iso(10), "end": None}],
    }
    timing = _timing(row, row["segments"][0], now_ms=NOW_MS + 30_000)
    assert timing == {"phaseSec": 20, "totalSec": 20, "waitSec": 0}


def test_wait_only_task_has_no_work_total() -> None:
    row = {
        "start": _iso(0),
        "end": _iso(30),
        "status": "completed",
        "segments": [{"phase": "wait", "start": _iso(0), "end": _iso(30)}],
    }
    timing = _timing(row, row["segments"][0])
    assert timing["phaseSec"] == 30
    assert timing["totalSec"] is None
    assert timing["waitSec"] == 30


def test_undefined_segment_yields_no_phase_duration() -> None:
    row = {
        "start": _iso(0),
        "end": _iso(60),
        "status": "completed",
        "segments": [{"phase": "worker", "start": _iso(0), "end": _iso(60)}],
    }
    timing = _timing(row, None)
    assert timing["phaseSec"] is None
    assert timing["totalSec"] == 60
    assert timing["waitSec"] == 0


def test_end_before_start_is_never_negative_or_nan() -> None:
    row = {
        "start": _iso(50),
        "end": _iso(10),
        "status": "completed",
        "segments": [{"phase": "worker", "start": _iso(50), "end": _iso(10)}],
    }
    timing = _timing(row, row["segments"][0])
    for key in ("phaseSec", "totalSec", "waitSec"):
        value = timing[key]
        assert value is None or value >= 0


def test_missing_timestamps_return_nulls() -> None:
    timing = _timing({"status": "queued"}, None)
    assert timing == {"phaseSec": None, "totalSec": None, "waitSec": None}


@pytest.mark.parametrize("keys", [TIMING_KEYS])
def test_timing_labels_exist_in_both_catalogs(keys: tuple[str, ...]) -> None:
    for key in keys:
        assert key in EN_MESSAGES
        assert key in PT_MESSAGES
        assert EN_MESSAGES[key].strip()
        assert PT_MESSAGES[key].strip()


def test_english_timing_labels_have_no_accents() -> None:
    for key in TIMING_KEYS:
        assert EN_MESSAGES[key].isascii(), key


def test_timing_labels_reach_the_timeline_page() -> None:
    messages = _timeline_messages()
    for key in TIMING_KEYS:
        assert key[len("cli.timeline.web."):] in messages


def test_timeline_page_wires_timing_into_tooltip() -> None:
    source = TIMELINE_TEMPLATE.read_text(encoding="utf-8")
    assert "function taskTiming(row, seg, nowMs)" in source
    assert "taskTiming(row, seg, Date.now())" in source
    assert "tooltip_phase_duration" in source
    assert "innerHTML" not in source
