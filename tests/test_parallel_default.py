import os

from tests.parallel_default import default_numprocesses


def decide(args=("tests",), numprocesses=None, xdist=True, env=None):
    return default_numprocesses(args, numprocesses, xdist, env or {})


def test_whole_suite_with_xdist_runs_in_parallel_by_default(tmp_path):
    assert decide(args=(str(tmp_path),)) == "auto"
    assert decide(args=()) == "auto"


def test_without_xdist_it_stays_serial():
    assert decide(xdist=False) is None


def test_an_explicit_choice_is_respected():
    assert decide(numprocesses=0) is None  # -n0 pede série
    assert decide(numprocesses=4) is None
    assert decide(numprocesses="auto") is None


def test_selecting_files_or_tests_stays_serial(tmp_path):
    test_file = tmp_path / "test_x.py"
    test_file.write_text("")
    assert decide(args=(str(test_file),)) is None
    assert decide(args=(str(test_file) + "::test_a",)) is None
    assert decide(args=(str(tmp_path), str(test_file))) is None


def test_an_xdist_worker_never_enables_it_again():
    assert decide(env={"PYTEST_XDIST_WORKER": "gw0"}) is None


def test_environment_switch(tmp_path):
    assert decide(env={"MEISTER_TEST_PARALLEL": "0"}) is None
    assert decide(env={"MEISTER_TEST_PARALLEL": "off"}) is None
    assert decide(env={"MEISTER_TEST_PARALLEL": "6"}) == "6"
    assert decide(env={"MEISTER_TEST_PARALLEL": "auto"}) == "auto"
    assert os.path.isdir(str(tmp_path))
