"""Tests for meister.plan_template (meister plan new)."""

from __future__ import annotations

import json

import pytest

from meister.plan import validate_tasks
from meister.plan_adapters.superpowers import convert
from meister.plan_template import (
    project_constraints,
    render_json_template,
    render_markdown_template,
)


@pytest.mark.parametrize("n", range(1, 21))
def test_markdown_template_imports_and_validates(n: int) -> None:
    text = render_markdown_template(tasks=n)
    tasks = convert(text)
    validate_tasks(tasks)
    assert [task["id"] for task in tasks] == [f"task_{k}" for k in range(1, n + 1)]
    assert tasks[0]["depends_on"] == []
    for k in range(2, n + 1):
        assert tasks[k - 1]["depends_on"] == [f"task_{k - 1}"]


def test_markdown_template_structure() -> None:
    text = render_markdown_template(tasks=3)
    lines = text.splitlines()
    assert lines[0].startswith("# ")
    comments = [line for line in lines if line.startswith("<!--")]
    assert len(comments) == 1
    assert "meister plan guide" in comments[0] and comments[0].endswith("-->")
    assert "## Global Constraints" in text
    assert text.count("### Task ") == 3
    assert text.count("**Files:**") == 3
    assert text.count("- Create: `") == 3
    assert text.count("- Test: `") == 3
    assert "**Depends on:** Task 1" in text
    assert "**Depends on:** Task 2" in text
    assert text.count("**Depends on:**") == 2


def test_markdown_template_with_constraints() -> None:
    text = render_markdown_template(tasks=1, constraints=["- Use ruff.", "Run pytest"])
    section = text.split("## Global Constraints", 1)[1].split("### Task", 1)[0]
    assert "- Use ruff." in section
    assert "- Run pytest" in section
    tasks = convert(text)
    validate_tasks(tasks)
    assert "- Use ruff." in tasks[0]["description"]


@pytest.mark.parametrize("n", range(1, 21))
def test_json_template_validates(n: int) -> None:
    tasks = json.loads(render_json_template(tasks=n))
    validate_tasks(tasks)
    assert len(tasks) == n
    for task in tasks:
        assert set(task) == {"id", "description", "target_files", "depends_on"}


@pytest.mark.parametrize("n", [0, -1, -5])
def test_non_positive_task_count_is_an_error(n: int) -> None:
    with pytest.raises(ValueError):
        render_markdown_template(tasks=n)
    with pytest.raises(ValueError):
        render_json_template(tasks=n)


def test_project_constraints_without_files_is_empty(tmp_path) -> None:
    assert project_constraints(tmp_path) == []


def test_project_constraints_reads_only_list_lines(tmp_path) -> None:
    (tmp_path / "CLAUDE.md").write_text(
        "# Title\n\nSome paragraph that mentions rules.\n\n"
        "- Always run pytest before finishing.\n"
        "* Never commit secrets.\n"
        "```\n- inside a code fence\n```\n"
        "| table | row |\n",
        encoding="utf-8",
    )
    (tmp_path / "AGENTS.md").write_text("1. Keep the API stable.\n", encoding="utf-8")
    rules = project_constraints(tmp_path)
    assert rules == [
        "- Always run pytest before finishing.",
        "- Never commit secrets.",
        "- Keep the API stable.",
    ]


def test_project_constraints_caps_at_eight(tmp_path) -> None:
    body = "".join(f"- Rule number {k} must hold.\n" for k in range(20))
    (tmp_path / "CLAUDE.md").write_text(body, encoding="utf-8")
    rules = project_constraints(tmp_path)
    assert len(rules) == 8
    assert rules[0] == "- Rule number 0 must hold."
