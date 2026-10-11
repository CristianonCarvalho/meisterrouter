"""`meister plan import` com `--format auto` (padrão) e o nome `markdown` do formato nativo."""

from __future__ import annotations

import json

import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.i18n import reset_language_cache

MARKDOWN_PLAN = (
    "# Plano\n\n"
    "### Task 1: Primeira\n\n"
    "**Files:**\n- Create: `a.py`\n\n"
    "Corpo da tarefa um.\n\n"
    "### Task 2: Segunda\n\n"
    "**Files:**\n- Modify: `b.py`\n\n"
    "Corpo da tarefa dois.\n"
)

JSON_PLAN = [
    {"id": "t1", "description": "Primeira", "target_files": ["a.py"], "depends_on": []},
    {"id": "t2", "description": "Segunda", "target_files": ["b.py"], "depends_on": ["t1"]},
]


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("MEISTER_LANG", "en")
    reset_language_cache()
    yield
    reset_language_cache()


def _invoke(*args: str):
    return CliRunner().invoke(main, list(args))


def _write(tmp_path, name: str, content: str):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_auto_is_the_default_and_reads_markdown(tmp_path):
    plan_md = _write(tmp_path, "plan.md", MARKDOWN_PLAN)
    out = tmp_path / "plan.json"

    result = _invoke("plan", "import", str(plan_md), "-o", str(out))

    assert result.exit_code == 0, result.output
    assert "task_1" in result.output and "task_2" in result.output
    tasks = json.loads(out.read_text(encoding="utf-8"))
    assert [task["id"] for task in tasks] == ["task_1", "task_2"]
    assert tasks[0]["target_files"] == ["a.py"]


def test_auto_reads_canonical_json_and_normalizes_it(tmp_path):
    plan_json = _write(tmp_path, "in.json", json.dumps(JSON_PLAN, indent=4))
    out = tmp_path / "out.json"

    result = _invoke("plan", "import", str(plan_json), "-o", str(out))

    assert result.exit_code == 0, result.output
    # mesma tabela de tarefas do caminho Markdown
    assert "t1" in result.output and "t2" in result.output
    assert "depends_on" in result.output
    written = out.read_text(encoding="utf-8")
    assert written == json.dumps(JSON_PLAN, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert _invoke("plan", "validate", str(out)).exit_code == 0


def test_auto_rejects_invalid_canonical_json(tmp_path):
    bad = [JSON_PLAN[0] | {"command": ["x"]}]
    plan_json = _write(tmp_path, "bad.json", json.dumps(bad))

    result = _invoke("plan", "import", str(plan_json))

    assert result.exit_code == 2
    assert "command" in result.output


def test_superpowers_and_markdown_give_the_same_result(tmp_path):
    plan_md = _write(tmp_path, "plan.md", MARKDOWN_PLAN)
    out_sp = tmp_path / "sp.json"
    out_md = tmp_path / "md.json"

    sp = _invoke("plan", "import", str(plan_md), "--format", "superpowers", "-o", str(out_sp))
    md = _invoke("plan", "import", str(plan_md), "--format", "markdown", "-o", str(out_md))

    assert sp.exit_code == 0, sp.output
    assert md.exit_code == 0, md.output
    assert out_sp.read_text(encoding="utf-8") == out_md.read_text(encoding="utf-8")
    for output in (sp.output, md.output):
        assert "deprecat" not in output.lower()
        assert "obsolet" not in output.lower()


def test_markdown_adapter_is_registered_with_the_native_converter():
    import meister.plan_adapters  # noqa: F401
    from meister.plan import ADAPTERS

    assert ADAPTERS["markdown"] is ADAPTERS["superpowers"]


def test_unknown_format_fails(tmp_path):
    plan_md = _write(tmp_path, "plan.md", MARKDOWN_PLAN)

    result = _invoke("plan", "import", str(plan_md), "--format", "nope")

    assert result.exit_code != 0
    assert "nope" in result.output
    assert "auto" in result.output and "markdown" in result.output


def test_empty_file_fails_with_message(tmp_path):
    empty = _write(tmp_path, "blank.md", "  \n\n")

    result = _invoke("plan", "import", str(empty))

    assert result.exit_code == 2
    assert "empty" in result.output.lower()
    assert str(empty) in result.output


def test_text_without_tasks_fails_detection_with_message(tmp_path):
    prose = _write(tmp_path, "notes.md", "# Notas\n\nSó texto, sem tarefas.\n")

    result = _invoke("plan", "import", str(prose))

    assert result.exit_code == 2
    assert str(prose) in result.output
    assert "### Task N:" in result.output
    assert "JSON array" in result.output
