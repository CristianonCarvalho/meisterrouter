from click.testing import CliRunner

from meister.cli import main
from meister.jev_context import build_jev_context
from meister.plan import load_plan


def _importer_description(constraints, body, files="src/app.py"):
    return (
        "Task 3: Implement a feature\n\n"
        f"## Global Constraints\n{constraints}\n\n"
        f"Arquivos permitidos: {files}\n\n"
        f"{body}"
    )


def test_build_jev_context_omits_constraints_and_includes_task_metadata():
    constraints = "global security migration constraint " * 60
    task = {
        "id": "task_3",
        "description": _importer_description(
            constraints,
            "Implement the task-specific handler.",
            "src/handler.py, tests/test_handler.py",
        ),
        "target_files": ["src/handler.py", "tests/test_handler.py"],
        "depends_on": ["task_1", "task_2"],
    }

    context = build_jev_context(task, 4000)

    assert constraints not in context
    assert "Task 3: Implement a feature" in context
    assert "src/handler.py, tests/test_handler.py" in context
    assert "Depende de: task_1, task_2" in context
    assert "Implement the task-specific handler." in context
    expected_omitted = len(("## Global Constraints\n" + constraints).rstrip("\n"))
    assert f"omitidas ({expected_omitted} caracteres)" in context


def test_tasks_with_shared_constraints_have_distinct_contexts():
    constraints = "security auth vulnerability " * 40
    common = {"id": "task", "target_files": ["src/task.py"], "depends_on": []}
    setup = build_jev_context(
        {
            **common,
            "description": _importer_description(constraints, "Configure the tooling."),
        },
        4000,
    )
    auth = build_jev_context(
        {
            **common,
            "description": _importer_description(
                constraints, "Implement the authentication module."
            ),
        },
        4000,
    )

    assert setup != auth
    assert "Configure the tooling." in setup
    assert "Implement the authentication module." in auth


def test_build_jev_context_truncates_only_body_and_caps_file_list():
    files = [f"f{index}.py" for index in range(80)]
    context = build_jev_context(
        {
            "id": "task_big",
            "description": "Task: large body\n" + ("x" * 10_000),
            "target_files": files,
            "depends_on": ["task_parent"],
        },
        700,
    )

    header = context.split("Corpo:\n", 1)[0]
    assert "Tarefa: Task: large body" in header
    assert "Arquivos (80):" in header
    assert "f59.py" in header
    assert "f60.py" not in header
    assert "... +20 arquivos" in header
    assert "Depende de: task_parent" in header
    assert "[... corpo truncado: " in context
    assert len(context) <= 700


def test_build_jev_context_preserves_oversized_header_and_unconstrained_body():
    header_only = build_jev_context(
        {
            "id": "wide",
            "description": "Task: wide\nbody",
            "target_files": ["very-long-path-" + "x" * 600],
            "depends_on": [],
        },
        500,
    )
    assert len(header_only) > 500
    assert "Arquivos (1):" in header_only
    assert "Corpo:\nbody" in header_only

    context = build_jev_context(
        {
            "id": "plain",
            "description": "Task 1: Plain task\nKeep this source-plan body intact.",
            "target_files": ["README.md"],
            "depends_on": [],
        },
        1000,
    )
    assert context == (
        "Tarefa: Task 1: Plain task\n"
        "Arquivos (1): README.md\n"
        "Depende de: nenhuma\n"
        "Corpo:\n"
        "Keep this source-plan body intact."
    )

    fallback_title = build_jev_context(
        {
            "id": "id-as-title",
            "description": "\nBody with no title line.",
            "target_files": [],
            "depends_on": None,
        },
        500,
    )
    assert "Tarefa: id-as-title" in fallback_title
    assert "Depende de: nenhuma" in fallback_title

def _constraints_block(count=60, separator="\n"):
    return "## Global Constraints\n" + separator.join(f"- Regra global numero {n} do projeto." for n in range(count))


def test_task_without_files_block_keeps_its_body_and_drops_the_constraints():
    """Sem `Arquivos permitidos:` nem `**Files:**` (tarefa sem escopo) o corpo nao pode ser engolido pelas constraints."""
    task = {
        "id": "task_3",
        "description": (
            "Task 3: Implementar auth\n\n"
            f"{_constraints_block()}\n\n"
            "Implemente o login com JWT e o logout."
        ),
        "target_files": [],
        "depends_on": ["task_1"],
    }
    context = build_jev_context(task, 4000)
    assert "Implemente o login com JWT e o logout." in context
    assert "Regra global numero" not in context
    assert "Restricoes globais do projeto: omitidas" in context
    assert "Depende de: task_1" in context


def test_constraints_with_blank_lines_between_bullets_do_not_leak_or_swallow_the_body():
    task = {
        "id": "task_3",
        "description": (
            "Task 3: Implementar auth\n\n"
            f"{_constraints_block(30, separator=chr(10) * 2)}\n\n"
            "Corpo proprio da tarefa."
        ),
        "target_files": [],
        "depends_on": [],
    }
    context = build_jev_context(task, 4000)
    assert "Corpo proprio da tarefa." in context
    assert "Regra global numero" not in context


def test_description_with_only_constraints_has_a_header_and_no_body():
    task = {
        "id": "task_3",
        "description": f"Task 3: So restricoes\n\n{_constraints_block()}",
        "target_files": [],
        "depends_on": [],
    }
    context = build_jev_context(task, 4000)
    assert context.startswith("Tarefa: Task 3: So restricoes")
    assert "Regra global numero" not in context


def test_real_superpowers_import_produces_task_focused_contexts(tmp_path, monkeypatch):
    constraints = "Project-wide global constraint " * 100
    task_sections = []
    for number, task_body in enumerate(
        ["Configure developer tools.", "Implement authentication.", "Add module tests."],
        start=1,
    ):
        task_sections.append(
            f"### Task {number}: Work item {number}\n"
            f"{task_body}\n\n"
            "**Files:**\n"
            f"- Modify: `src/module_{number}.py`\n"
        )
    markdown = (
        "# Plan\n\n"
        f"## Global Constraints\n{constraints}\n\n"
        + "\n".join(task_sections)
    )
    source = tmp_path / "plan.md"
    output = tmp_path / "plan.json"
    source.write_text(markdown, encoding="utf-8")

    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        main, ["plan", "import", "--format", "superpowers", str(source), "-o", str(output)]
    )

    assert result.exit_code == 0, result.output
    tasks = load_plan(output.read_text(encoding="utf-8"))
    assert len(tasks) == 3
    for number, task in enumerate(tasks, start=1):
        context = build_jev_context(task, 4000)
        assert f"Work item {number}" in context
        assert f"src/module_{number}.py" in context
        assert constraints not in context
        assert "**Files:**" in context
        assert f"- Modify: `src/module_{number}.py`" in context
