import json
import tempfile
from meister import logger
from meister.config import load_config
from meister.logger import get_current_run, log_event, read_events, save_current_run, track_task

def test_log_event_and_read(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("MEISTER_LOG_DIR", tmpdir)

        ev1 = log_event("test_event_1", foo="bar")
        assert ev1["event_type"] == "test_event_1"
        assert ev1["foo"] == "bar"

        log_event("test_event_2", value=42)

        events = read_events()
        assert len(events) == 2
        assert events[0]["event_type"] == "test_event_1"
        assert events[1]["value"] == 42

def test_track_task_context(monkeypatch):
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("MEISTER_LOG_DIR", tmpdir)

        tier_name = load_config().workers.tier_order[0].name
        with track_task("task-123", tier_name, level="SMALL", instance_label="test.py") as t:
            t["tokens_in"] = 5000
            t["tokens_out"] = 1000

        events = read_events()
        assert len(events) == 2
        assert events[0]["event_type"] == "task_start"
        assert events[1]["event_type"] == "task_end"
        assert events[1]["tokens_in"] == 5000
        assert events[1]["tokens_out"] == 1000
        assert events[1]["cost_usd"] > 0


def test_save_current_run_survives_interrupted_write(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    monkeypatch.setattr(logger, "find_project_root", lambda: None)

    save_current_run("run-old", "task-old")
    assert get_current_run()["run_id"] == "run-old"

    original_dump = json.dump

    def failing_dump(obj, fp, **kwargs):
        fp.write('{"run_id": "run-no')
        raise OSError("interrupted")

    monkeypatch.setattr(logger.json, "dump", failing_dump)
    save_current_run("run-new", "task-new")
    monkeypatch.setattr(logger.json, "dump", original_dump)

    current = get_current_run()
    assert current["run_id"] == "run-old"
    assert current["task_id"] == "task-old"
    with open(log_dir / "current_run.json", encoding="utf-8") as f:
        assert json.load(f)["run_id"] == "run-old"
    assert [p.name for p in log_dir.iterdir()] == ["current_run.json"]


def test_save_current_run_replaces_file_atomically(monkeypatch, tmp_path):
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("MEISTER_LOG_DIR", str(log_dir))
    monkeypatch.setattr(logger, "find_project_root", lambda: None)

    save_current_run("run-1", "task-1")
    save_current_run("run-2", "task-2")

    assert get_current_run()["run_id"] == "run-2"
    assert [p.name for p in log_dir.iterdir()] == ["current_run.json"]
