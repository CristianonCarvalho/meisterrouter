"""Detecção de dependências ausentes na saída do gate."""

from __future__ import annotations

import pytest

from meister.gate import summarize_gate_failure
from meister.i18n import CATALOGS


@pytest.mark.parametrize(
    "line, name",
    [
        ("Error: Cannot find package 'left-pad'", "left-pad"),
        ('Error: Cannot find module "react-dom/client"', "react-dom/client"),
        ('[vite] Failed to resolve import "lodash" from "src/app.ts"', "lodash"),
        ("ModuleNotFoundError: No module named 'requests'", "requests"),
    ],
)
def test_each_pattern_is_detected(line: str, name: str) -> None:
    result = summarize_gate_failure(line)
    assert result["missing_dependencies"] == [name]


def test_output_without_patterns_yields_empty_list_and_same_message() -> None:
    output = "FAILED tests/test_mod.py::test_x - AssertionError: nope\nE   assert 1 == 2"
    result = summarize_gate_failure(output)
    assert result["missing_dependencies"] == []
    assert result["failed_tests"] == ["tests/test_mod.py::test_x"]
    assert result["excerpt"] == "E   assert 1 == 2"


def test_repeated_names_appear_once() -> None:
    output = (
        "No module named 'requests'\n"
        "No module named 'requests'\n"
        "Cannot find module 'left-pad'\n"
        "Cannot find module 'left-pad'\n"
    )
    result = summarize_gate_failure(output)
    assert result["missing_dependencies"] == ["requests", "left-pad"]


def test_missing_dependencies_capped_at_five() -> None:
    output = "\n".join(f"No module named 'pkg{i}'" for i in range(8))
    result = summarize_gate_failure(output)
    assert result["missing_dependencies"] == [f"pkg{i}" for i in range(5)]


def test_missing_dependency_key_exists_in_both_catalogs() -> None:
    key = "engine.worktree.worker_gate_missing_dependencies"
    assert key in CATALOGS["en"]
    assert key in CATALOGS["pt-BR"]
    assert "requests" in CATALOGS["en"][key].format(names="requests")
    assert "requests" in CATALOGS["pt-BR"][key].format(names="requests")
