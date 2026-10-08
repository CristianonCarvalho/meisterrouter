from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from meister.cli import main
from meister.timeline import build_timeline
from tests.timeline_fixtures import at, ev


def _resumed_run():
    return [
        ev("orchestration_start", "orchestrator", 0, task="plan"),
        ev("orchestration_end", "orchestrator", 5, status="failed"),
        ev("orchestration_start", "orchestrator", 10, task="plan"),
    ]


def test_run_with_start_after_failed_end_is_running():
    timeline = build_timeline(_resumed_run(), "r1", at(11))

    assert timeline.status == "running"
    assert timeline.started_at == at(0)
    assert timeline.ended_at is None


def test_run_with_end_and_no_later_start_stays_ended():
    events = [
        ev("orchestration_start", "orchestrator", 0, task="plan"),
        ev("orchestration_end", "orchestrator", 5, status="failed"),
    ]

    timeline = build_timeline(events, "r1", at(11))

    assert timeline.status == "failed"
    assert timeline.started_at == at(0)
    assert timeline.ended_at == at(5)


def test_two_complete_cycles_use_the_second_end():
    events = [
        ev("orchestration_start", "orchestrator", 0, task="plan"),
        ev("orchestration_end", "orchestrator", 5, status="failed"),
        ev("orchestration_start", "orchestrator", 10, task="plan"),
        ev("orchestration_end", "orchestrator", 15, status="completed"),
    ]

    timeline = build_timeline(events, "r1", at(20))

    assert timeline.status == "completed"
    assert timeline.started_at == at(0)
    assert timeline.ended_at == at(15)


def test_timeline_json_reports_resumed_run_as_running(tmp_path):
    events = _resumed_run()
    base = datetime.now(timezone.utc) - timedelta(seconds=20)
    for event in events:
        seconds = (datetime.fromisoformat(event["ts"]) - at(0)).total_seconds()
        event["ts"] = (base + timedelta(seconds=seconds)).isoformat()
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "orchestration_log.jsonl").write_text(
        "".join(json.dumps(event) + "\n" for event in events),
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        main,
        ["timeline", "--json", "--log-dir", str(log_dir)],
    )

    assert result.exit_code == 0, result.output
    run = json.loads(result.output)["run"]
    assert run["status"] == "running"
    assert run["started_at"] == base.isoformat().replace("+00:00", "Z")
    assert run["ended_at"] is None
