import json

from meister.log_tail import LogTail, resolve_log_file


def _write(path, *events, partial=""):
    with open(path, "ab") as handle:
        for event in events:
            handle.write((json.dumps(event) + "\n").encode())
        if partial:
            handle.write(partial.encode())


def test_poll_returns_only_new_events(tmp_path):
    log = tmp_path / "orchestration_log.jsonl"
    tail = LogTail(str(log))
    assert tail.poll() == []
    _write(log, {"n": 1}, {"n": 2})
    assert [event["n"] for event in tail.poll()] == [1, 2]
    assert tail.poll() == []
    _write(log, {"n": 3})
    assert [event["n"] for event in tail.poll()] == [3]


def test_partial_line_is_kept_until_complete(tmp_path):
    log = tmp_path / "orchestration_log.jsonl"
    tail = LogTail(str(log))
    _write(log, {"n": 1}, partial='{"n": 2, "x"')
    assert [event["n"] for event in tail.poll()] == [1]
    _write(log, partial=': "ok"}\n')
    events = tail.poll()
    assert events == [{"n": 2, "x": "ok"}]
    assert tail.poll() == []


def test_invalid_and_non_object_lines_are_ignored(tmp_path):
    log = tmp_path / "orchestration_log.jsonl"
    log.write_bytes(b'not json\n[1, 2]\n{"n": 1}\n\n')
    assert [event["n"] for event in LogTail(str(log)).poll()] == [1]


def test_truncated_file_is_reread_and_flags_reset(tmp_path):
    log = tmp_path / "orchestration_log.jsonl"
    tail = LogTail(str(log))
    _write(log, {"n": 1}, {"n": 2}, {"n": 3})
    assert len(tail.poll()) == 3
    assert tail.reset is False
    log.write_bytes(b"")
    _write(log, {"n": 9})
    events = tail.poll()
    assert [event["n"] for event in events] == [9]
    assert tail.reset is True
    tail.poll()
    assert tail.reset is False


def test_resolve_log_file_never_creates_directories(tmp_path, monkeypatch):
    monkeypatch.delenv("MEISTER_LOG_DIR", raising=False)
    explicit = tmp_path / "custom"
    assert resolve_log_file(str(explicit)) == str(explicit / "orchestration_log.jsonl")
    assert not explicit.exists()

    monkeypatch.setenv("MEISTER_LOG_DIR", str(tmp_path / "from_env"))
    assert resolve_log_file() == str(tmp_path / "from_env" / "orchestration_log.jsonl")
    assert not (tmp_path / "from_env").exists()

    monkeypatch.delenv("MEISTER_LOG_DIR")
    project = tmp_path / "proj"
    (project / ".meister").mkdir(parents=True)
    monkeypatch.chdir(project)
    assert resolve_log_file() == str(project.resolve() / ".meister" / "logs" / "orchestration_log.jsonl")
    assert not (project / ".meister" / "logs").exists()
