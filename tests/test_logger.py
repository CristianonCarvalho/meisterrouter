import tempfile
from meister.config import load_config
from meister.logger import log_event, read_events, track_task

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
