import json
from datetime import datetime, timedelta
from types import SimpleNamespace

from click.testing import CliRunner

from meister.cli import main
from meister.timeline_cli import (
    once_frame,
    pick_run,
    project_name,
    stale_after_from_config,
)
from meister.timeline_view import strip_ansi
from tests.timeline_fixtures import at, parallel_events


def _write(tmp_path, events):
    log = tmp_path / "orchestration_log.jsonl"
    log.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    return str(log)


def test_pick_run_prefix_default_and_errors():
    runs = [{"run_id": "bbbbbbbb2222"}, {"run_id": "aaaaaaaa1111"}]
    assert pick_run(runs, None) == "bbbbbbbb2222"
    assert pick_run(runs, "aaaaaa") == "aaaaaaaa1111"
    for bad in ("aaa", "zzzzzz"):
        try:
            pick_run(runs, bad)
        except ValueError as error:
            assert "bbbbbbbb2222" in str(error) or "pelo menos 6" in str(error)
        else:
            raise AssertionError("deveria falhar")


def test_project_name_from_standard_layout_and_fallback(tmp_path):
    assert project_name("/x/MeuProjeto/.meister/logs/orchestration_log.jsonl") == "MeuProjeto"
    assert project_name("/x/logs_soltos/orchestration_log.jsonl") == "logs_soltos"


def test_stale_after_uses_largest_configured_runtime_and_fallbacks(monkeypatch):
    from meister import config

    workers = SimpleNamespace(
        tier_order=[
            SimpleNamespace(max_runtime_seconds=1200),
            SimpleNamespace(max_runtime_seconds=None),
        ],
        disabled=[SimpleNamespace(max_runtime_seconds=2400)],
    )
    monkeypatch.setattr(config, "load_config", lambda: SimpleNamespace(workers=workers))
    assert stale_after_from_config() == timedelta(seconds=2700)

    workers.tier_order = [SimpleNamespace(max_runtime_seconds=None)]
    workers.disabled = []
    assert stale_after_from_config() == timedelta(seconds=3900)

    monkeypatch.setattr(config, "load_config", lambda: (_ for _ in ()).throw(ValueError("bad")))
    assert stale_after_from_config() == timedelta(seconds=3900)


def test_once_frame_renders_the_latest_run(tmp_path):
    log = _write(tmp_path, parallel_events())
    out = once_frame(log, None, width=120, now=at(30), color="none")
    assert "task_3" in out and "2/3" in out and "\x1b" not in out


def test_once_frame_without_log_says_waiting(tmp_path):
    out = once_frame(str(tmp_path / "nao_existe.jsonl"), None, width=100, now=at(0), color="none")
    assert "aguardando" in out


def test_cli_once_prints_one_frame_and_leaves_log_untouched(tmp_path):
    log = _write(tmp_path, parallel_events())
    path = tmp_path / "orchestration_log.jsonl"
    before = path.stat()
    result = CliRunner().invoke(main, ["timeline", "--once", "--log-dir", str(tmp_path)])
    after = path.stat()
    assert result.exit_code == 0, result.output
    assert "task_1" in strip_ansi(result.output) and "\x1b" not in result.output
    assert (before.st_mtime_ns, before.st_size) == (after.st_mtime_ns, after.st_size)
    assert log


def test_cli_unknown_run_exits_2_and_without_once_is_a_usage_error(tmp_path):
    _write(tmp_path, parallel_events())
    bad = CliRunner().invoke(
        main, ["timeline", "--once", "--log-dir", str(tmp_path), "--run-id", "zzzzzz"]
    )
    assert bad.exit_code == 2 and "inexistente" in bad.output
    interactive = CliRunner().invoke(main, ["timeline", "--log-dir", str(tmp_path)])
    assert interactive.exit_code != 0 and "--once" in interactive.output
    assert "o modo interativo precisa de um terminal (TTY): use --once" in interactive.output


def test_cli_run_id_with_missing_log_exits_2_without_creating_directory(tmp_path):
    log_dir = tmp_path / "missing"
    result = CliRunner().invoke(
        main, ["timeline", "--once", "--log-dir", str(log_dir), "--run-id", "abcdef"]
    )
    assert result.exit_code == 2 and "inexistente" in result.output
    assert not log_dir.exists()


def test_cli_all_runs_once_and_rejects_run_id(tmp_path):
    events = parallel_events("newrun01")
    older = parallel_events("oldrun01")
    for event in older:
        event["ts"] = (
            datetime.fromisoformat(event["ts"]) - timedelta(seconds=100)
        ).isoformat()
    log = _write(tmp_path, events + older)
    result = CliRunner().invoke(
        main, ["timeline", "--once", "--all", "--log-dir", str(tmp_path)]
    )
    assert result.exit_code == 0, result.output
    plain = strip_ansi(result.output)
    assert "todos os runs (2)" in plain
    assert "newrun01" in plain and "oldrun01" in plain
    assert plain.index("newrun01") < plain.index("oldrun01")
    assert "0:00" in plain and "\x1b" not in result.output
    assert log
    invalid = CliRunner().invoke(
        main,
        ["timeline", "--all", "--run-id", "newrun", "--log-dir", str(tmp_path)],
    )
    assert invalid.exit_code == 2
    assert "use --all ou --run-id, não os dois" in invalid.output


def test_cli_all_once_without_log_says_waiting(tmp_path):
    result = CliRunner().invoke(
        main, ["timeline", "--once", "--all", "--log-dir", str(tmp_path / "missing")]
    )
    assert result.exit_code == 0
    assert "aguardando o primeiro run" in result.output
    empty_dir = tmp_path / "empty"
    empty_dir.mkdir()
    (empty_dir / "orchestration_log.jsonl").write_text("", encoding="utf-8")
    empty = CliRunner().invoke(
        main, ["timeline", "--once", "--all", "--log-dir", str(empty_dir)]
    )
    assert empty.exit_code == 0
    assert "aguardando o primeiro run" in empty.output
