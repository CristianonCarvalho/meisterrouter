import json

from click.testing import CliRunner

from meister.cli import main
from meister.timeline_cli import once_frame, pick_run, project_name
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


def test_cli_run_id_with_missing_log_exits_2_without_creating_directory(tmp_path):
    log_dir = tmp_path / "missing"
    result = CliRunner().invoke(
        main, ["timeline", "--once", "--log-dir", str(log_dir), "--run-id", "abcdef"]
    )
    assert result.exit_code == 2 and "inexistente" in result.output
    assert not log_dir.exists()
