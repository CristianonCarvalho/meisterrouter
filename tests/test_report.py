import json
from datetime import datetime, timedelta, timezone

from click.testing import CliRunner

from meister.cli import main
from meister.report import compute_run_report, render_report


T0 = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def event(kind, run_id="run-alpha-0001", offset=0, **fields):
    timestamp = (T0 + timedelta(seconds=offset)).isoformat()
    return {"event_type": kind, "run_id": run_id, "ts": timestamp, **fields}


def write_log(directory, events):
    directory.mkdir(parents=True, exist_ok=True)
    log = directory / "orchestration_log.jsonl"
    log.write_text("".join(json.dumps(item) + "\n" for item in events), encoding="utf-8")
    return log


def test_full_run_aggregates_phases_and_gate_events():
    events = [
        event("orchestration_start", task="Plano de teste"),
        event("worker_spawn", task_id="task-a", tier="copilot"),
        event("worker_phase", phase="worker", duration_ms=4000, task_id="task-a", attempt=1, tier="copilot",
              offset=5),
        event("worker_phase", phase="gate", duration_ms=200, task_id="task-a", attempt=1, tier="copilot",
              offset=6),
        event("worker_phase", phase="gate", duration_ms=300, task_id="task-a", attempt=1, tier="copilot",
              offset=7),
        event("worker_phase", phase="integrate", duration_ms=3000, task_id="task-a", attempt=1, tier="copilot",
              offset=8),
        event("subtask_completed", task_id="task-a", tier="copilot", cost=0.01, cost_source="reported"),
        event("orchestration_end", status="completed", offset=10),
    ]
    report = compute_run_report(events, "run-alpha-0001")
    assert report["title"] == "Plano de teste"
    assert report["ended"] is True
    assert report["wall_seconds"] == 10
    assert report["tasks"] == {"total": 1, "completed": 1, "failed": 0, "reused": 0}
    assert report["phase_seconds"] == {
        "worker": 4.0, "gate": 0.5, "integrate": 3.0, "merge": 2.5, "setup": None,
    }
    assert report["worker_seconds_sum"] == 4
    assert report["worker_seconds_union"] == 4
    assert report["overhead_ratio"] == 0.6


def test_worker_overlap_and_sequential_parallelism_metrics():
    overlapping = [
        event("orchestration_start"),
        event("worker_phase", phase="worker", duration_ms=4000, task_id="a", offset=5),
        event("worker_phase", phase="worker", duration_ms=4000, task_id="b", offset=7),
        event("orchestration_end", status="completed", offset=10),
    ]
    overlap_report = compute_run_report(overlapping, "run-alpha-0001")
    assert overlap_report["peak_parallel_workers"] == 2
    assert overlap_report["worker_seconds_sum"] == 8
    assert overlap_report["worker_seconds_union"] == 6
    assert overlap_report["overhead_ratio"] == 0.4

    sequential = [
        event("orchestration_start"),
        event("worker_phase", phase="worker", duration_ms=2000, task_id="a", offset=3),
        event("worker_phase", phase="worker", duration_ms=3000, task_id="b", offset=7),
        event("orchestration_end", status="completed", offset=10),
    ]
    sequential_report = compute_run_report(sequential, "run-alpha-0001")
    assert sequential_report["peak_parallel_workers"] == 1
    assert sequential_report["worker_seconds_union"] == sequential_report["worker_seconds_sum"] == 5


def test_old_run_without_phases_or_cost_source_reports_unmeasured():
    events = [
        event("orchestration_start"),
        event("worker_spawn", task_id="a", tier="copilot"),
        event("subtask_completed", task_id="a", tier="copilot", cost=0.0),
        event("orchestration_end", status="completed", offset=3),
    ]
    report = compute_run_report(events, "run-alpha-0001")
    assert report["phase_seconds"]["worker"] is None
    assert report["phase_seconds"]["merge"] is None
    assert report["worker_seconds_sum"] is None
    assert report["worker_seconds_union"] is None
    assert report["overhead_ratio"] is None
    assert report["by_tier"]["copilot"]["events_unknown"] == 1
    assert report["by_tier"]["copilot"]["cost_known_usd"] is None
    assert any("log anterior à E1" in note for note in report["notes"])
    assert "US$ 0.0000" not in render_report({"groups": [], "runs": [report]}, "table")
    assert "não medido" in render_report({"groups": [], "runs": [report]}, "table")


def test_cost_sources_credits_catalog_and_approximation():
    events = [
        event("orchestration_start"),
        event("subtask_completed", task_id="a", tier="copilot", cost=0.01, cost_source="reported",
              tokens_in=100, tokens_out=900, tokens_total=1000, credits=20),
        event("subtask_completed", task_id="b", tier="copilot", cost_source="estimated",
              tokens_in=1000, tokens_out=1000, tokens_total=2000, credits=2),
        event("subtask_completed", task_id="c", tier="copilot", cost=0.0, cost_source="unknown",
              tokens_in=1500, tokens_out=1500, tokens_total=3000, credits=3, approx=True),
        event("orchestration_end", status="completed", offset=5),
    ]
    report = compute_run_report(events, "run-alpha-0001", tier_prices={"copilot": 0.5})
    tier = report["by_tier"]["copilot"]
    assert tier["cost_reported_usd"] == 0.01
    assert tier["cost_estimated_usd"] == 0.001
    assert tier["cost_known_usd"] == 0.011
    assert tier["events_unknown"] == 1
    assert tier["credits"] == 25
    assert tier["tokens_total"] == 6000
    assert tier["approx"] is True
    table = render_report({"groups": [], "runs": [report]}, "table")
    assert "US$ 0.0110 + ?" in table
    assert "~" in table
    assert "créditos" in table

    unknown_only = [
        event("orchestration_start"),
        event("subtask_completed", task_id="a", tier="copilot", cost=0, cost_source="unknown"),
        event("orchestration_end", status="completed", offset=1),
    ]
    unknown_report = compute_run_report(unknown_only, "run-alpha-0001")
    unknown_tier = unknown_report["by_tier"]["copilot"]
    assert unknown_tier["cost_known_usd"] is None
    assert unknown_tier["events_unknown"] == 1
    assert "?" in render_report({"groups": [], "runs": [unknown_report]}, "table")
    assert "US$ 0.0000" not in render_report({"groups": [], "runs": [unknown_report]}, "table")


def test_attempt_failures_reasons_escalation_and_unlinked_setup_note():
    events = [
        event("orchestration_start"),
        event("worker_spawn", task_id="a", tier="copilot"),
        event("worker_retry", task_id="a", tier="copilot"),
        event("worker_spawn", task_id="a", tier="agy"),
        event("subtask_rejected", task_id="b", reason="gate"),
        event("subtask_rejected", task_id="c", reason="scope"),
        event("worker_timeout", task_id="d"),
        event("quota_error", task_id="e"),
        event("worker_error", task_id="f"),
        {"event_type": "worktree_setup_ok", "duration_ms": 10},
        event("worktree_setup_ok", duration_ms=200, task_id="unrelated"),
        event("orchestration_end", status="failed", offset=4),
    ]
    report = compute_run_report(events, "run-alpha-0001")
    assert report["attempts"] == {
        "spawns": 2,
        "retries": 1,
        "escalations": 1,
        "rejections_by_reason": {"gate": 1, "scope": 1},
        "timeouts": 1,
        "quota_errors": 1,
        "worker_errors": 1,
    }
    assert report["phase_seconds"]["setup"] == 0.2
    assert any("setup não ligável" in note for note in report["notes"])


def test_prefix_resolution_and_read_only_log_dir(tmp_path):
    log_dir = tmp_path / "logs"
    events = []
    for run_id in ("run-alpha-0001", "run-alpha-0002", "run-beta-0001"):
        events.extend([
            event("orchestration_start", run_id=run_id),
            event("orchestration_end", run_id=run_id, status="completed", offset=1),
        ])
    log = write_log(log_dir, events)
    before_log = (log.stat().st_size, log.stat().st_mtime_ns)
    before_dir = log_dir.stat().st_mtime_ns
    runner = CliRunner()
    unique = runner.invoke(main, ["report", "--run-id", "run-be", "--log-dir", str(log_dir)])
    assert unique.exit_code == 0, unique.output
    assert "run-beta-0001" in unique.output
    ambiguous = runner.invoke(main, ["report", "--run-id", "run-al", "--log-dir", str(log_dir)])
    assert ambiguous.exit_code == 2
    assert "ambíguo" in ambiguous.output
    assert "run-alpha-0001" in ambiguous.output and "run-beta-0001" in ambiguous.output
    missing = runner.invoke(main, ["report", "--run-id", "missing-1", "--log-dir", str(log_dir)])
    assert missing.exit_code == 2
    assert "inexistente" in missing.output
    assert "run-alpha-0001" in missing.output
    assert (log.stat().st_size, log.stat().st_mtime_ns) == before_log
    assert log_dir.stat().st_mtime_ns == before_dir


def test_missing_log_dir_is_not_created(tmp_path):
    missing = tmp_path / "not-created"
    result = CliRunner().invoke(
        main, ["report", "--run-id", "run-alpha", "--log-dir", str(missing)]
    )
    assert result.exit_code == 2
    assert "não encontrado" in result.output
    assert not missing.exists()


def test_groups_median_null_metrics_and_single_run_warning(tmp_path):
    log_dir = tmp_path / "logs"
    events = []
    for run_id, offset in (("run-alpha-0001", 1), ("run-alpha-0002", 3), ("run-alpha-0003", 5)):
        events.append(event("orchestration_start", run_id=run_id))
        if offset != 3:
            events.append(event("worker_phase", run_id=run_id, phase="worker", duration_ms=1000, offset=offset))
        events.append(event("orchestration_end", run_id=run_id, status="completed", offset=offset + 1))
    write_log(log_dir, events)
    result = CliRunner().invoke(main, [
        "report", "--group", "A=run-alpha-0001,run-alpha-0002,run-alpha-0003",
        "--log-dir", str(log_dir), "--format", "json",
    ])
    assert result.exit_code == 0, result.output
    group = json.loads(result.output)["groups"][0]
    wall = group["metrics"]["wall_seconds"]
    assert wall == {"median": 4.0, "min": 2.0, "max": 6.0, "n_runs": 3, "n_measured": 3}
    assert group["metrics"]["overhead_ratio"]["n_measured"] == 2
    one = CliRunner().invoke(main, [
        "report", "--group", "A=run-alpha-0001", "--log-dir", str(log_dir),
    ])
    assert one.exit_code == 0, one.output
    assert "n=1: sem mediana confiável" in one.output


def test_json_markdown_and_aligned_table_formats(tmp_path):
    log_dir = tmp_path / "logs"
    write_log(log_dir, [
        event("orchestration_start", task="Exemplo"),
        event("orchestration_end", status="completed", offset=2),
    ])
    runner = CliRunner()
    json_result = runner.invoke(main, [
        "report", "--run-id", "run-alpha", "--log-dir", str(log_dir), "--format", "json",
    ])
    assert json_result.exit_code == 0
    parsed = json.loads(json_result.output)
    assert list(parsed) == ["groups", "runs"]
    assert {"run_id", "title", "started_at", "ended_at", "status", "ended"} <= set(parsed["runs"][0])
    markdown = runner.invoke(main, [
        "report", "--run-id", "run-alpha", "--log-dir", str(log_dir), "--format", "markdown",
    ])
    assert markdown.exit_code == 0
    assert "| Métrica | run-alpha-0001 |" in markdown.output
    table = runner.invoke(main, ["report", "--run-id", "run-alpha", "--log-dir", str(log_dir)])
    assert table.exit_code == 0
    header = next(line for line in table.output.splitlines() if "run-alpha-0001" in line)
    assert "Métrica" in header and "  " in header


def test_unfinished_run_measures_wall_until_last_event_and_notes_it():
    events = [
        event("orchestration_start"),
        event("worker_phase", phase="worker", duration_ms=1000, task_id="a", offset=3),
    ]
    report = compute_run_report(events, "run-alpha-0001")
    assert report["ended"] is False
    assert report["ended_at"] is None
    assert report["wall_seconds"] == 3
    assert any("run sem orchestration_end" in note for note in report["notes"])


def test_group_counts_absent_rejections_and_unused_tiers_as_zero(tmp_path):
    from meister.report import compute_group_report

    def run(run_id, rejected):
        items = [
            event("orchestration_start", run_id=run_id),
            event("subtask_completed", run_id=run_id, task_id="t", tier="copilot", cost=0.0, cost_source="unknown"),
            event("orchestration_end", run_id=run_id, status="completed", offset=10),
        ]
        if rejected:
            items.insert(1, event("subtask_rejected", run_id=run_id, task_id="t", reason="gate"))
        return compute_run_report(items, run_id)

    group = compute_group_report("A", [run("r1", True), run("r2", False), run("r3", False)])
    gate = group["metrics"]["attempts.rejections_by_reason.gate"]
    assert (gate["median"], gate["max"], gate["n_measured"], gate["n_runs"]) == (0.0, 1.0, 3, 3)


def test_long_titles_are_clipped_in_the_table_but_not_in_json():
    long_title = "Task fix_5: " + "x" * 120
    events = [
        {**event("orchestration_start"), "task": long_title},
        event("orchestration_end", status="completed", offset=5),
    ]
    report = compute_run_report(events, "run-alpha-0001")
    data = {"groups": [], "runs": [report]}
    table_line = next(line for line in render_report(data, "table").splitlines() if line.startswith("Título"))
    assert "…" in table_line and table_line.count("x") < 48
    assert json.loads(render_report(data, "json"))["runs"][0]["title"].startswith("Task fix_5:")
