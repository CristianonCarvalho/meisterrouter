"""Regression: the superpowers importer must ignore headings inside fenced code blocks."""
from __future__ import annotations

import meister.plan_adapters  # noqa: F401 — registers the superpowers adapter
from meister.plan import ADAPTERS, validate_tasks
from meister.plan_adapters.superpowers import _fenced_ranges


def _convert(text: str) -> list[dict]:
    tasks = ADAPTERS["superpowers"](text)
    validate_tasks(tasks)
    return tasks


def _by_id(tasks: list[dict]) -> dict[str, dict]:
    return {task["id"]: task for task in tasks}


README_TASK = (
    "### Task 1: README\n\n"
    "**Files:**\n- Modify: `README.md`\n\n"
    "Escreva o README seguindo o modelo abaixo.\n\n"
    "- [ ] **Step 1: Escrever `README.md`**\n\n"
    "````markdown\n"
    "# Projeto\n\n"
    "## O que o projeto resolve?\n\n"
    "Texto.\n\n"
    "```bash\n"
    "pytest -q\n"
    "```\n\n"
    "## Como instalar?\n\n"
    "Mais texto do modelo.\n"
    "````\n\n"
    "Passo final depois do bloco.\n\n"
)


def test_headings_inside_a_four_backtick_block_do_not_truncate_the_task():
    plan = README_TASK + "### Task 2: Outra\n\n**Files:**\n- Create: `b.py`\n\nCorpo da tarefa dois.\n"
    tasks = _by_id(_convert(plan))

    body = tasks["task_1"]["description"]
    # o modelo inteiro chega ao worker, inclusive os "## " e o bloco ```bash interno
    assert "## O que o projeto resolve?" in body
    assert "## Como instalar?" in body
    assert "Mais texto do modelo." in body
    assert "Passo final depois do bloco." in body
    # e a tarefa seguinte continua separada
    assert "Corpo da tarefa dois." not in body
    assert "Corpo da tarefa dois." in tasks["task_2"]["description"]


def test_a_real_level_two_heading_after_the_task_still_ends_it():
    plan = (
        README_TASK
        + "## Relatório final (não é tarefa)\n\nTexto que não pertence à tarefa.\n"
    )
    body = _convert(plan)[0]["description"]
    assert "Passo final depois do bloco." in body
    assert "Relatório final" not in body
    assert "Texto que não pertence à tarefa." not in body


def test_task_header_inside_a_fenced_block_is_not_a_task():
    plan = (
        "### Task 1: Real\n\n**Files:**\n- Create: `a.py`\n\n"
        "```markdown\n### Task 2: Exemplo dentro de um bloco\n```\n\n"
        "### Task 3: Outra real\n\n**Files:**\n- Create: `c.py`\n\nCorpo.\n"
    )
    tasks = _convert(plan)
    assert [task["id"] for task in tasks] == ["task_1", "task_3"]
    assert "### Task 2: Exemplo dentro de um bloco" in tasks[0]["description"]


def test_tilde_fences_and_info_strings_are_respected():
    plan = (
        "### Task 1: Til\n\n**Files:**\n- Create: `a.py`\n\n"
        "~~~md\n## Dentro do til\n~~~\n\nFim da tarefa.\n"
    )
    body = _convert(plan)[0]["description"]
    assert "## Dentro do til" in body and "Fim da tarefa." in body


def test_an_unclosed_fence_is_ignored_so_it_cannot_hide_the_rest_of_the_plan():
    plan = (
        "### Task 1: Cerca aberta\n\n**Files:**\n- Create: `a.py`\n\n"
        "```python\ncodigo sem fechar\n\n"
        "## Seção de nível dois\n\nNão pertence à tarefa.\n\n"
        "### Task 2: Seguinte\n\n**Files:**\n- Create: `b.py`\n\nCorpo.\n"
    )
    tasks = _convert(plan)
    # comportamento anterior preservado: sem fence fechado, o "## " corta a tarefa 1
    assert "Não pertence à tarefa." not in tasks[0]["description"]
    assert [task["id"] for task in tasks] == ["task_1", "task_2"]


def test_fenced_ranges_follow_commonmark_closing_rules():
    text = "````md\n```bash\nx\n```\n````\nfora\n"
    ranges = _fenced_ranges(text)
    assert len(ranges) == 1
    assert text[ranges[0][0]:ranges[0][1]].endswith("````\n")
    assert "fora" not in text[ranges[0][0]:ranges[0][1]]
