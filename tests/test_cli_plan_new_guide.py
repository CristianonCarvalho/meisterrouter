"""Comandos `meister plan new` e `meister plan guide` pela CLI (CliRunner)."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.i18n import reset_language_cache


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    reset_language_cache()
    yield
    reset_language_cache()


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


def test_plan_new_prints_markdown_that_imports(tmp_path):
    result = _invoke("plan", "new", "--tasks", "4")
    assert result.exit_code == 0, result.output
    assert "### Task 4:" in result.output
    plan_md = tmp_path / "plan.md"
    plan_md.write_text(result.output, encoding="utf-8")

    imported = _invoke("plan", "import", str(plan_md), "-o", str(tmp_path / "plan.json"))
    assert imported.exit_code == 0, imported.output
    validated = _invoke("plan", "validate", str(tmp_path / "plan.json"))
    assert validated.exit_code == 0, validated.output
    assert len(json.loads((tmp_path / "plan.json").read_text(encoding="utf-8"))) == 4


def test_plan_new_output_writes_file(tmp_path):
    target = tmp_path / "plan.md"
    result = _invoke("plan", "new", "-o", str(target))
    assert result.exit_code == 0, result.output
    assert str(target) in result.output
    assert "### Task 3:" in target.read_text(encoding="utf-8")
    assert "### Task" not in result.output


def test_plan_new_json_validates(tmp_path):
    target = tmp_path / "plan.json"
    result = _invoke("plan", "new", "--format", "json", "--tasks", "2", "-o", str(target))
    assert result.exit_code == 0, result.output
    validated = _invoke("plan", "validate", str(target))
    assert validated.exit_code == 0, validated.output
    assert [task["id"] for task in json.loads(target.read_text(encoding="utf-8"))] == ["task_1", "task_2"]


def test_plan_new_project_uses_rule_files(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("- Always run the full test suite.\n", encoding="utf-8")
    result = _invoke("plan", "new", "--project", "--tasks", "1")
    assert result.exit_code == 0, result.output
    assert "- Always run the full test suite." in result.output


@pytest.mark.parametrize("count", ["0", "-2"])
def test_plan_new_rejects_non_positive_tasks(count, tmp_path):
    result = _invoke("plan", "new", "--tasks", count, "-o", str(tmp_path / "plan.md"))
    assert result.exit_code != 0
    assert "--tasks" in result.output
    assert not (tmp_path / "plan.md").exists()


@pytest.mark.parametrize(
    ("lang", "title"),
    [("en", "# MeisterRouter plan format"), ("pt-BR", "# Formato de plano do MeisterRouter")],
)
def test_plan_guide_prints_guide(lang, title):
    result = _invoke("plan", "guide", "--lang", lang)
    assert result.exit_code == 0, result.output
    assert title in result.output
    assert "meister plan new" in result.output


def test_plan_help_lists_new_and_guide():
    result = _invoke("plan", "--help")
    assert result.exit_code == 0, result.output
    assert "new" in result.output.split()
    assert "guide" in result.output.split()
