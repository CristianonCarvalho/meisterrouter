from __future__ import annotations

import os
import threading
import time

import pytest

from meister.logs_cmd import LogSelectionError, follow, list_worker_logs, read_tail, select_log


# Prazo de proteção contra travamento dos testes de `follow`, folgado para runners lentos.
FOLLOW_GUARD_SECONDS = 30


def _touch(directory, name, content=b"", mtime=None):
    path = directory / name
    path.write_bytes(content)
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_list_ignores_names_that_do_not_match(tmp_path):
    _touch(tmp_path, "0123abcd_task-1_1.log", b"ok")
    _touch(tmp_path, "notes.log")
    _touch(tmp_path, "0123abcd_task-1.log")
    _touch(tmp_path, "_task-1_1.log")
    _touch(tmp_path, "0123abcd_task-1_x.log")
    (tmp_path / "0123abcd_task-2_1.log").mkdir()

    logs = list_worker_logs(str(tmp_path))

    assert [(log.run_id, log.task_id, log.attempt) for log in logs] == [("0123abcd", "task-1", 1)]
    assert logs[0].size == 2


def test_list_parses_task_with_underscores_and_missing_dir(tmp_path):
    _touch(tmp_path, "run42_my_task_3.log")

    (log,) = list_worker_logs(str(tmp_path))

    assert (log.run_id, log.task_id, log.attempt) == ("run42", "my_task", 3)
    assert list_worker_logs(str(tmp_path / "missing")) == []


def test_select_without_filters_picks_most_recent(tmp_path):
    _touch(tmp_path, "aaaaaa11_t-1_1.log", mtime=1_000)
    newest = _touch(tmp_path, "bbbbbb22_t-1_1.log", mtime=3_000)
    _touch(tmp_path, "cccccc33_t-1_1.log", mtime=2_000)

    assert select_log(list_worker_logs(str(tmp_path))).path == str(newest)


def test_select_by_run_prefix_and_task(tmp_path):
    _touch(tmp_path, "0123abcdef_t-1_1.log", mtime=1_000)
    target = _touch(tmp_path, "0123abcdef_t-2_1.log", mtime=500)
    _touch(tmp_path, "fedcba9876_t-2_1.log", mtime=2_000)
    logs = list_worker_logs(str(tmp_path))

    assert select_log(logs, run_id="0123ab", task_id="t-2").path == str(target)


def test_select_prefix_ambiguous_raises(tmp_path):
    _touch(tmp_path, "0123abaa_t-1_1.log")
    _touch(tmp_path, "0123abbb_t-1_1.log")
    logs = list_worker_logs(str(tmp_path))

    with pytest.raises(LogSelectionError) as info:
        select_log(logs, run_id="0123ab")

    assert info.value.kind == "ambiguous"
    assert info.value.candidates == ("0123abaa", "0123abbb")


def test_exact_run_id_wins_over_longer_prefix_match(tmp_path):
    _touch(tmp_path, "abcdef_t-1_1.log", mtime=1_000)
    exact = _touch(tmp_path, "abcdef12_t-1_1.log", mtime=500)
    logs = list_worker_logs(str(tmp_path))

    assert select_log(logs, run_id="abcdef").run_id == "abcdef"
    assert select_log(logs, run_id="abcdef12").path == str(exact)


def test_select_short_prefix_and_not_found(tmp_path):
    _touch(tmp_path, "0123abcd_t-1_1.log")
    logs = list_worker_logs(str(tmp_path))

    with pytest.raises(LogSelectionError) as short:
        select_log(logs, run_id="0123a")
    assert short.value.kind == "short_prefix"

    with pytest.raises(LogSelectionError) as missing:
        select_log(logs, run_id="ffffff")
    assert missing.value.kind == "not_found"

    with pytest.raises(LogSelectionError) as empty:
        select_log([])
    assert empty.value.kind == "not_found"


def test_read_tail_empty_file(tmp_path):
    path = _touch(tmp_path, "empty.log", b"")

    assert read_tail(str(path), 5) == []
    assert read_tail(str(path), 0) == []


def test_read_tail_without_trailing_newline_and_crlf(tmp_path):
    path = _touch(tmp_path, "a.log", b"one\r\ntwo\nthree")

    assert read_tail(str(path), 2) == ["two", "three"]
    assert read_tail(str(path), 10) == ["one", "two", "three"]


def test_read_tail_large_file_is_fast_and_correct(tmp_path):
    path = tmp_path / "big.log"
    line = b"x" * 99 + b"\n"
    total = 50 * 1024 * 1024 // len(line)
    with open(path, "wb") as fh:
        for index in range(total):
            fh.write(line if index < total - 3 else f"last-{index}\n".encode())

    start = time.perf_counter()
    tail = read_tail(str(path), 3)
    elapsed = time.perf_counter() - start

    assert tail == [f"last-{total - 3}", f"last-{total - 2}", f"last-{total - 1}"]
    # Margem larga para runner lento (Windows sob xdist); o tail normalmente leva milissegundos.
    assert elapsed < 10.0


def test_read_tail_crosses_block_boundaries(tmp_path):
    body = "".join(f"line {i:05d} " + "y" * (i % 300) + "\n" for i in range(3000))
    path = _touch(tmp_path, "mixed.log", body.encode("utf-8"))

    assert read_tail(str(path), 7) == body.splitlines()[-7:]


def test_follow_yields_lines_appended_later(tmp_path):
    path = _touch(tmp_path, "live.log", b"old\n")
    done = threading.Event()

    def writer():
        time.sleep(0.2)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("first\nsec")
            fh.flush()
        time.sleep(0.2)
        with open(path, "a", encoding="utf-8") as fh:
            fh.write("ond\n")
        done.set()

    thread = threading.Thread(target=writer)
    thread.start()
    received: list[str] = []
    deadline = time.monotonic() + FOLLOW_GUARD_SECONDS
    for line in follow(
        str(path), poll=0.02, stop=lambda: time.monotonic() > deadline or (done.is_set() and len(received) >= 2)
    ):
        received.append(line)
    thread.join()

    assert received == ["first", "second"]


def test_follow_waits_for_missing_file_and_restarts_on_truncate(tmp_path):
    path = tmp_path / "later.log"
    deadline = time.monotonic() + FOLLOW_GUARD_SECONDS

    def writer():
        time.sleep(0.15)
        path.write_text("alpha-long-line\n", encoding="utf-8")
        time.sleep(0.3)
        path.write_text("beta\n", encoding="utf-8")

    thread = threading.Thread(target=writer)
    thread.start()
    received: list[str] = []
    for line in follow(str(path), poll=0.02, stop=lambda: time.monotonic() > deadline or len(received) >= 2):
        received.append(line)
    thread.join()

    assert received == ["alpha-long-line", "beta"]
