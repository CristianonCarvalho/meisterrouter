from __future__ import annotations

import os

from click.testing import CliRunner

from meister import logs_cmd
from meister.cli import main
from meister.i18n import t


def _worker_dir(tmp_path):
    workers = tmp_path / "workers"
    workers.mkdir()
    return workers


def _log(workers, name, content, mtime):
    path = workers / name
    path.write_text(content, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def _invoke(tmp_path, *args):
    return CliRunner().invoke(main, ["logs", "--log-dir", str(tmp_path), *args])


def test_default_shows_newest_log(tmp_path):
    workers = _worker_dir(tmp_path)
    _log(workers, "aaaaaa11_task-1_1.log", "old line\n", 1000)
    _log(workers, "bbbbbb22_task-2_1.log", "new line\n", 2000)

    result = _invoke(tmp_path)

    assert result.exit_code == 0
    assert "new line" in result.stdout
    assert "old line" not in result.stdout
    assert "bbbbbb22_task-2_1.log" in result.stderr


def test_lines_limits_output(tmp_path):
    workers = _worker_dir(tmp_path)
    _log(workers, "aaaaaa11_task-1_1.log", "".join(f"l{i}\n" for i in range(10)), 1000)

    result = _invoke(tmp_path, "--lines", "3")

    assert result.stdout.splitlines() == ["l7", "l8", "l9"]


def test_list_shows_every_log_and_honors_filters(tmp_path):
    workers = _worker_dir(tmp_path)
    _log(workers, "aaaaaa11_task-1_1.log", "x", 1000)
    _log(workers, "aaaaaa11_task-2_1.log", "x", 1500)
    _log(workers, "bbbbbb22_task-1_2.log", "x", 2000)

    everything = _invoke(tmp_path, "--list")
    by_run = _invoke(tmp_path, "--list", "--run-id", "aaaaaa")
    by_task = _invoke(tmp_path, "--list", "--task", "task-2")

    assert everything.exit_code == 0
    assert "aaaaaa11" in everything.stdout and "bbbbbb22" in everything.stdout
    assert "bbbbbb22" not in by_run.stdout and "task-2" in by_run.stdout
    assert "task-1" not in by_task.stdout and "task-2" in by_task.stdout


def test_run_id_prefix_and_task_filters(tmp_path):
    workers = _worker_dir(tmp_path)
    _log(workers, "aaaaaa11_task-1_1.log", "first\n", 1000)
    _log(workers, "aaaaaa11_task-2_1.log", "second\n", 2000)
    _log(workers, "bbbbbb22_task-1_1.log", "third\n", 3000)

    by_run = _invoke(tmp_path, "--run-id", "aaaaaa")
    by_task = _invoke(tmp_path, "--run-id", "aaaaaa", "--task", "task-1")

    assert by_run.stdout.strip() == "second"
    assert by_task.stdout.strip() == "first"


def test_ambiguous_prefix_exits_2_and_lists_candidates(tmp_path):
    workers = _worker_dir(tmp_path)
    _log(workers, "abcdef01_task-1_1.log", "x\n", 1000)
    _log(workers, "abcdef02_task-1_1.log", "x\n", 2000)

    result = _invoke(tmp_path, "--run-id", "abcdef")

    assert result.exit_code == 2
    assert "abcdef01" in result.stderr and "abcdef02" in result.stderr


def test_short_prefix_and_unknown_run_exit_2(tmp_path):
    workers = _worker_dir(tmp_path)
    _log(workers, "abcdef01_task-1_1.log", "x\n", 1000)

    assert _invoke(tmp_path, "--run-id", "abc").exit_code == 2
    assert _invoke(tmp_path, "--run-id", "zzzzzz").exit_code == 2
    assert _invoke(tmp_path, "--list", "--run-id", "zzzzzz").exit_code == 2


def test_missing_directory_and_empty_directory_exit_2(tmp_path):
    missing = _invoke(tmp_path)
    assert missing.exit_code == 2
    assert t("cli.logs.no_dir", path=str(tmp_path / "workers")) in missing.stderr

    _worker_dir(tmp_path)
    empty = _invoke(tmp_path)
    assert empty.exit_code == 2
    assert t("cli.logs.no_logs", path=str(tmp_path / "workers")) in empty.stderr


def test_follow_prints_tail_then_new_lines(tmp_path, monkeypatch):
    workers = _worker_dir(tmp_path)
    path = _log(workers, "aaaaaa11_task-1_1.log", "before\n", 1000)
    seen = {}

    def fake_follow(followed, **kwargs):
        seen["path"] = followed
        yield "after-1"
        yield "after-2"

    monkeypatch.setattr(logs_cmd, "follow", fake_follow)

    result = _invoke(tmp_path, "--follow")

    assert result.exit_code == 0
    assert result.stdout.splitlines() == ["before", "after-1", "after-2"]
    assert seen["path"] == str(path)


def test_follow_exits_130_on_ctrl_c(tmp_path, monkeypatch):
    workers = _worker_dir(tmp_path)
    _log(workers, "aaaaaa11_task-1_1.log", "before\n", 1000)

    def interrupted(followed, **kwargs):
        yield "tick"
        raise KeyboardInterrupt

    monkeypatch.setattr(logs_cmd, "follow", interrupted)

    result = _invoke(tmp_path, "-f")

    assert result.exit_code == 130
    assert "tick" in result.stdout


def test_list_and_follow_together_is_a_usage_error(tmp_path):
    workers = _worker_dir(tmp_path)
    _log(workers, "aaaaaa11_task-1_1.log", "x\n", 1000)

    assert _invoke(tmp_path, "--list", "--follow").exit_code == 2


def test_main_help_lists_logs_command():
    result = CliRunner().invoke(main, ["--help"])

    assert result.exit_code == 0
    assert "logs" in result.output
