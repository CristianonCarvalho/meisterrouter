import io
import subprocess

import pytest

from meister.config import load_config
from meister.herdr.bridge import HerdrEventBridge
from meister.i18n import reset_language_cache
from meister.progress import ProgressReporter, format_event_line
from meister.state import StateManager, SubtaskState
from meister.worktree import tolerated_touched


def test_tolerated_touched_filters_scope_and_ignored_files_in_input_order():
    files = [
        "tests/first.py",
        "src/app.py",
        "tests/ignored.py",
        "outside.txt",
        "tests/nested/second.py",
    ]

    assert tolerated_touched(
        files,
        ["src/**"],
        ["tests/**"],
        {"tests/ignored.py"},
    ) == ["tests/first.py", "tests/nested/second.py"]


@pytest.mark.parametrize("target_files", [None, []])
def test_tolerated_touched_requires_target_scope(target_files):
    assert tolerated_touched(["tests/a.py"], target_files, ["tests/**"]) == []


def test_tolerated_touched_matches_tests_recursive_glob():
    assert tolerated_touched(
        ["tests/a.py", "tests/unit/test_nested.py", "src/app.py"],
        ["src/**"],
        ["tests/**"],
    ) == ["tests/a.py", "tests/unit/test_nested.py"]


def _git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _bridge_and_reuse(tmp_path, monkeypatch, *, tolerated_change):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Scope report tests")
    _git(repo, "config", "user.email", "scope-report@example.invalid")
    (repo / "app.py").write_text("old\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "base")

    (repo / "app.py").write_text("updated\n")
    if tolerated_change:
        (repo / "tests").mkdir()
        (repo / "tests" / "a.py").write_text("test\n")
    _git(repo, "add", ".")
    _git(repo, "commit", "-m", "worker changes")
    commit_sha = _git(repo, "rev-parse", "HEAD")

    task = {"id": "task_2", "description": "Update app", "target_files": ["app.py"], "depends_on": []}
    state = StateManager(str(tmp_path / "state.db"))
    state.create_or_get_run("source", cwd=str(repo), force_run_id="source")
    source_row = state.add_subtasks("source", [task])[0]
    state.transition_subtask(source_row["subtask_id"], SubtaskState.RUNNING)
    state.transition_subtask(
        source_row["subtask_id"],
        SubtaskState.COMPLETED,
        integrated_sha=commit_sha,
    )
    state.create_or_get_run("target", cwd=str(repo), force_run_id="target")
    state.add_subtasks("target", [task])

    config = load_config()
    config.scope.tolerated_files = ["tests/**"]
    monkeypatch.chdir(repo)
    bridge = HerdrEventBridge(config=config, state_manager=state)
    events = []
    monkeypatch.setattr("meister.herdr.bridge.log_event", lambda **event: events.append(event))
    bridge._reuse_completed_subtasks(state, "source", "target", [task], str(repo))
    return events


@pytest.mark.parametrize("tolerated_change", [True, False])
def test_bridge_reports_only_successfully_scoped_tolerated_changes(
    tmp_path,
    monkeypatch,
    tolerated_change,
):
    events = _bridge_and_reuse(tmp_path, monkeypatch, tolerated_change=tolerated_change)

    reports = [event for event in events if event.get("event_type") == "scope_tolerated"]
    if tolerated_change:
        assert len(reports) == 1
        assert reports[0]["run_id"] == "target"
        assert reports[0]["task_id"] == "task_2"
        assert reports[0]["files"] == ["tests/a.py"]
        assert reports[0]["count"] == 1
    else:
        assert reports == []


@pytest.mark.parametrize(
    ("language", "localized_label"),
    [("en", "tolerated files"), ("pt-BR", "arquivos tolerados")],
)
@pytest.mark.parametrize(
    ("file_count", "visible_count", "extra"),
    [(1, 1, ""), (3, 3, ""), (7, 5, " (+2)")],
)
def test_scope_tolerated_event_line_is_localized_and_limits_visible_files(
    monkeypatch,
    language,
    localized_label,
    file_count,
    visible_count,
    extra,
):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    files = [f"tests/file_{index}.py" for index in range(1, file_count + 1)]

    line = format_event_line(
        {"event_type": "scope_tolerated", "task_id": "task_2", "files": files},
        task_number=1,
        total=3,
    )

    visible_files = ", ".join(files[:visible_count])
    assert line == f"[1/3] task_2 {localized_label}: {visible_files}{extra}"


def test_progress_reporter_prints_scope_event_even_before_plan_is_parsed():
    stream = io.StringIO()
    reporter = ProgressReporter(stream=stream)
    reporter.on_event({"event_type": "orchestration_start", "run_id": "run-123"})
    reporter.on_event(
        {
            "event_type": "scope_tolerated",
            "run_id": "run-123",
            "task_id": "task_2",
            "files": ["tests/a.py"],
        }
    )
    assert stream.getvalue() == ""

    reporter.on_event(
        {
            "event_type": "plan_parsed",
            "run_id": "run-123",
            "total": 3,
            "batches": 2,
            "task_ids": ["task_1", "task_2", "task_3"],
        }
    )

    assert stream.getvalue().splitlines()[-1] == (
        "[2/3] task_2 arquivos tolerados: tests/a.py"
    )
