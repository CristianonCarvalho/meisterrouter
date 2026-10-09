"""Testes do `meister wait`: espera uma run terminar e sai com código que diz como terminou."""

import hashlib
import json
import os
import threading
import time

import pytest
from click.testing import CliRunner

from meister import wait_cmd
from meister.cli import main
from tests.timeline_fixtures import ev, parallel_events

LOG_NAME = "orchestration_log.jsonl"


@pytest.fixture
def log_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path))
    monkeypatch.setenv("MEISTER_LANG", "en")
    return tmp_path


def write_log(log_dir, events):
    path = log_dir / LOG_NAME
    path.write_text("".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events), encoding="utf-8")
    return path


def append_events(path, events):
    with open(path, "a", encoding="utf-8") as handle:
        for event in events:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")


def end_event(run="r1", t=60, status="completed", exit_code=0, **fields):
    return ev("orchestration_end", "orchestrator", t, run=run, status=status, exit_code=exit_code, **fields)


def snapshot(directory):
    """Nomes e hashes de todos os arquivos do diretório; detecta qualquer escrita ou arquivo novo."""
    result = {}
    for name in sorted(os.listdir(directory)):
        path = directory / name
        if path.is_file():
            result[name] = hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            result[name] = "dir"
    return result


def run_wait(*args):
    return CliRunner().invoke(main, ["wait", *args])


def test_completed_run_exits_zero_immediately(log_dir):
    write_log(log_dir, parallel_events("abcdef123456") + [end_event("abcdef123456")])
    start = time.monotonic()
    result = run_wait("--interval", "0.05")
    assert result.exit_code == 0, result.output
    assert time.monotonic() - start < 5
    assert "completed" in result.output
    assert "abcdef12" in result.output


def test_failed_run_exits_one_with_reason(log_dir):
    events = parallel_events("failrun0001") + [
        end_event("failrun0001", status="failed", exit_code=1, reason="gate broke on task_2")
    ]
    write_log(log_dir, events)
    result = run_wait("--interval", "0.05")
    assert result.exit_code == 1, result.output
    assert "gate broke on task_2" in result.output


def test_interrupted_run_exits_130(log_dir):
    events = parallel_events("intrrun00001") + [
        end_event("intrrun00001", status="interrupted", exit_code=130, reason="interrupted by user")
    ]
    write_log(log_dir, events)
    result = run_wait("--interval", "0.05")
    assert result.exit_code == 130, result.output


def test_waits_until_end_is_appended(log_dir):
    path = write_log(log_dir, parallel_events("waitrun00001"))

    def finish_later():
        time.sleep(0.3)
        append_events(path, [end_event("waitrun00001", t=90)])

    thread = threading.Thread(target=finish_later)
    thread.start()
    start = time.monotonic()
    result = run_wait("--interval", "0.05", "--timeout", "10")
    elapsed = time.monotonic() - start
    thread.join()
    assert result.exit_code == 0, result.output
    assert elapsed >= 0.25


def test_timeout_exits_124_and_reports_running(log_dir):
    write_log(log_dir, parallel_events("timeout00001"))
    result = run_wait("--interval", "0.05", "--timeout", "0.3")
    assert result.exit_code == 124, result.output
    assert "running" in result.output


def test_timeout_json_reports_running(log_dir):
    write_log(log_dir, parallel_events("timeoutjson01"))
    result = run_wait("--interval", "0.05", "--timeout", "0.3", "--format", "json")
    assert result.exit_code == 124, result.output
    assert json.loads(result.output)["status"] == "running"


def test_prefix_resolves_to_matching_run(log_dir):
    write_log(
        log_dir,
        [
            ev("orchestration_start", "orchestrator", 0, run="aaaa11110000", task="older"),
            end_event("aaaa11110000", t=5, status="failed", exit_code=1, reason="old failure"),
            ev("orchestration_start", "orchestrator", 10, run="bbbb22220000", task="newer"),
            end_event("bbbb22220000", t=15, status="completed", exit_code=0),
        ],
    )
    result = run_wait("--run-id", "aaaa11", "--interval", "0.05")
    assert result.exit_code == 1, result.output
    assert "old failure" in result.output


def test_unknown_run_id_exits_two(log_dir):
    write_log(log_dir, parallel_events("knownrun0001") + [end_event("knownrun0001")])
    result = run_wait("--run-id", "zzzzzzzz", "--interval", "0.05")
    assert result.exit_code == 2, result.output


def test_ambiguous_prefix_exits_two_and_lists_candidates(log_dir):
    write_log(
        log_dir,
        [
            ev("orchestration_start", "orchestrator", 0, run="abcdef111111", task="one"),
            end_event("abcdef111111", t=5),
            ev("orchestration_start", "orchestrator", 10, run="abcdef222222", task="two"),
            end_event("abcdef222222", t=15),
        ],
    )
    result = run_wait("--run-id", "abcdef", "--interval", "0.05")
    assert result.exit_code == 2, result.output
    assert "abcdef111111" in result.output
    assert "abcdef222222" in result.output


def test_missing_log_exits_two(log_dir):
    result = run_wait("--interval", "0.05")
    assert result.exit_code == 2, result.output
    assert not (log_dir / LOG_NAME).exists()


def test_log_without_runs_exits_two(log_dir):
    write_log(log_dir, [ev("worker_spawn", "task_1", 1, run="global")])
    result = run_wait("--interval", "0.05")
    assert result.exit_code == 2, result.output


def test_without_run_id_uses_most_recent_run(log_dir):
    write_log(
        log_dir,
        [
            ev("orchestration_start", "orchestrator", 0, run="oldrun000001", task="old"),
            end_event("oldrun000001", t=5, status="failed", exit_code=1, reason="old failure"),
            ev("orchestration_start", "orchestrator", 100, run="newrun000001", task="new"),
            end_event("newrun000001", t=105, status="completed", exit_code=0),
        ],
    )
    result = run_wait("--interval", "0.05")
    assert result.exit_code == 0, result.output
    assert "newrun00" in result.output


def test_resumed_run_is_not_terminal_at_first_end(log_dir):
    write_log(
        log_dir,
        [
            ev("orchestration_start", "orchestrator", 0, run="resumed0001", task="work"),
            end_event("resumed0001", t=5, status="interrupted", exit_code=130),
            ev("orchestration_start", "orchestrator", 10, run="resumed0001", task="work"),
        ],
    )
    result = run_wait("--interval", "0.05", "--timeout", "0.3")
    assert result.exit_code == 124, result.output
    assert "running" in result.output


def test_resumed_run_waits_for_the_new_end(log_dir):
    path = write_log(
        log_dir,
        [
            ev("orchestration_start", "orchestrator", 0, run="resumed0002", task="work"),
            end_event("resumed0002", t=5, status="interrupted", exit_code=130),
            ev("orchestration_start", "orchestrator", 10, run="resumed0002", task="work"),
        ],
    )

    def finish_again():
        time.sleep(0.3)
        append_events(path, [end_event("resumed0002", t=20, status="failed", exit_code=1, reason="second try")])

    thread = threading.Thread(target=finish_again)
    thread.start()
    result = run_wait("--interval", "0.05", "--timeout", "10")
    thread.join()
    assert result.exit_code == 1, result.output
    assert "second try" in result.output


def test_json_format_has_all_keys_and_exit_code(log_dir):
    write_log(
        log_dir,
        parallel_events("jsonrun00001")
        + [end_event("jsonrun00001", status="failed", exit_code=1, reason="boom")],
    )
    result = run_wait("--interval", "0.05", "--format", "json")
    assert result.exit_code == 1, result.output
    data = json.loads(result.output)
    assert data["run_id"] == "jsonrun00001"
    assert data["status"] == "failed"
    assert data["exit_code"] == 1
    assert data["reason"] == "boom"
    for key in ("tasks_total", "tasks_completed", "tasks_failed", "duration_seconds"):
        assert key in data


def test_text_output_in_portuguese(log_dir, monkeypatch):
    monkeypatch.setenv("MEISTER_LANG", "pt-BR")
    write_log(log_dir, parallel_events("ptbrrun00001") + [end_event("ptbrrun00001")])
    result = run_wait("--interval", "0.05")
    assert result.exit_code == 0, result.output
    assert "concluídas" in result.output


def test_keyboard_interrupt_exits_130_without_touching_log(log_dir, monkeypatch):
    write_log(log_dir, parallel_events("ctrlcrun0001"))
    before = snapshot(log_dir)
    original_sleep = time.sleep

    def interrupted_sleep(_seconds):
        assert time.sleep is original_sleep, "time.sleep global não deve ser trocado"
        raise KeyboardInterrupt

    monkeypatch.setattr(wait_cmd, "_sleep", interrupted_sleep)
    result = run_wait("--interval", "0.05")
    assert result.exit_code == 130, result.output
    assert "Traceback" not in result.output
    assert snapshot(log_dir) == before


@pytest.mark.parametrize(
    "events_kind",
    ["completed", "failed", "running"],
)
def test_command_never_writes_to_log_or_creates_files(log_dir, events_kind):
    events = parallel_events("readonly00001")
    if events_kind == "completed":
        events.append(end_event("readonly00001"))
    elif events_kind == "failed":
        events.append(end_event("readonly00001", status="failed", exit_code=1, reason="x"))
    write_log(log_dir, events)
    before = snapshot(log_dir)
    run_wait("--interval", "0.05", "--timeout", "0.2")
    assert snapshot(log_dir) == before


def test_help_lists_every_option(log_dir):
    result = CliRunner().invoke(main, ["wait", "--help"])
    assert result.exit_code == 0
    for option in ("--run-id", "--timeout", "--interval", "--format"):
        assert option in result.output


def test_interval_below_minimum_is_usage_error(log_dir):
    write_log(log_dir, parallel_events("badinterval01") + [end_event("badinterval01")])
    result = run_wait("--interval", "0.01")
    assert result.exit_code == 2, result.output
