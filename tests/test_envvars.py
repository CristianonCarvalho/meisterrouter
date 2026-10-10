from __future__ import annotations

import pytest

from meister import envvars
from meister.envvars import WORKER_ENV_NAMES, is_worker_env, worker_env_assignments


def test_worker_env_names_order() -> None:
    assert WORKER_ENV_NAMES == ("MEISTER_WORKER", "MEISTER_IN_PANE")


def test_assignments_set_both_names_to_one() -> None:
    assert worker_env_assignments() == {"MEISTER_WORKER": "1", "MEISTER_IN_PANE": "1"}


def test_none_defined_is_not_worker() -> None:
    assert is_worker_env({}) is False


def test_only_new_name_is_worker() -> None:
    assert is_worker_env({"MEISTER_WORKER": "1"}) is True


def test_only_legacy_name_is_worker() -> None:
    assert is_worker_env({"MEISTER_IN_PANE": "1"}) is True


def test_both_names_is_worker() -> None:
    assert is_worker_env({"MEISTER_WORKER": "1", "MEISTER_IN_PANE": "1"}) is True


@pytest.mark.parametrize("value", ["", "0", "true", "yes", " 1"])
def test_value_other_than_one_is_not_worker(value: str) -> None:
    assert is_worker_env({"MEISTER_WORKER": value, "MEISTER_IN_PANE": value}) is False


def test_explicit_env_does_not_read_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MEISTER_WORKER", "1")
    monkeypatch.setenv("MEISTER_IN_PANE", "1")
    assert is_worker_env({}) is False


def test_none_reads_os_environ(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MEISTER_WORKER", raising=False)
    monkeypatch.delenv("MEISTER_IN_PANE", raising=False)
    assert is_worker_env() is False
    monkeypatch.setenv("MEISTER_IN_PANE", "1")
    assert is_worker_env() is True


def test_module_exports() -> None:
    assert envvars.is_worker_env is is_worker_env
