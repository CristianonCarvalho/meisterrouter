"""Avisos de testes existentes fora do escopo de uma tarefa (find_test_collisions)."""

from __future__ import annotations

import json
import os

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.i18n import reset_language_cache
from meister.plan_analysis import find_test_collisions


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv("MEISTER_LANG", raising=False)
    reset_language_cache()
    yield
    reset_language_cache()


@pytest.fixture
def repo(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "src" / "m.py").write_text(
        "def calcular_total(itens):\n    return sum(itens)\n\n"
        "def _interno_privado():\n    return 0\n\n"
        "def nome(x):\n    return x\n",
        encoding="utf-8",
    )
    (tmp_path / "tests" / "test_m.py").write_text(
        "from src.m import calcular_total\n\n"
        "def test_total():\n    assert calcular_total([1, 2]) == 3\n",
        encoding="utf-8",
    )
    return tmp_path


def _task(task_id, files):
    return {
        "id": task_id,
        "description": f"Task {task_id}",
        "target_files": list(files),
        "depends_on": [],
    }


def test_warns_when_test_outside_scope_cites_changed_name(repo):
    result = find_test_collisions([_task("a", ["src/m.py"])], str(repo))

    assert len(result) == 1
    assert result[0]["task"] == "a"
    collision = result[0]["collisions"][0]
    assert collision["test"] == os.path.join("tests", "test_m.py")
    names = {item["name"] for item in collision["names"]}
    assert names == {"calcular_total"}
    assert all(item["source"] == os.path.join("src", "m.py") for item in collision["names"])


def test_no_warning_when_test_is_in_target_files(repo):
    result = find_test_collisions(
        [_task("a", ["src/m.py", "tests/test_m.py"])], str(repo)
    )

    assert result == []


def test_ignores_short_and_private_names(repo):
    result = find_test_collisions([_task("a", ["src/m.py"])], str(repo))

    names = {item["name"] for item in result[0]["collisions"][0]["names"]}
    assert "nome" not in names
    assert "_interno_privado" not in names


def test_skips_when_repo_root_is_not_a_repository(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def calcular_total():\n    pass\n", encoding="utf-8")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_m.py").write_text("calcular_total()\n", encoding="utf-8")

    assert find_test_collisions([_task("a", ["src/m.py"])], str(tmp_path)) == []


def test_skips_missing_target_without_error(repo):
    result = find_test_collisions([_task("a", ["src/nao_existe.py"])], str(repo))

    assert result == []


def test_limits_to_three_test_files_per_task(repo):
    for index in range(5):
        (repo / "tests" / f"test_extra_{index}.py").write_text(
            "from src.m import calcular_total\n", encoding="utf-8"
        )

    result = find_test_collisions([_task("a", ["src/m.py"])], str(repo))

    assert len(result[0]["collisions"]) == 3


def _plan_file(tmp_path, tasks):
    path = tmp_path / "plano.json"
    path.write_text(json.dumps(tasks), encoding="utf-8")
    return str(path)


def test_plan_validate_prints_warning_without_changing_exit_code(repo, monkeypatch):
    monkeypatch.chdir(repo)
    plan = _plan_file(repo, [_task("a", ["src/m.py"])])

    result = CliRunner().invoke(main, ["plan", "validate", plan])

    assert result.exit_code == 0, result.output
    assert "test_m.py" in result.output
    assert "calcular_total" in result.output


def test_plan_validate_silent_when_test_is_in_target_files(repo, monkeypatch):
    monkeypatch.chdir(repo)
    plan = _plan_file(repo, [_task("a", ["src/m.py", "tests/test_m.py"])])

    result = CliRunner().invoke(main, ["plan", "validate", plan])

    assert result.exit_code == 0, result.output
    assert "calcular_total" not in result.output


def test_plan_analyze_prints_warning(repo, monkeypatch):
    monkeypatch.chdir(repo)
    plan = _plan_file(repo, [_task("a", ["src/m.py"])])

    result = CliRunner().invoke(main, ["plan", "analyze", plan])

    assert result.exit_code == 0, result.output
    assert "calcular_total" in result.output
