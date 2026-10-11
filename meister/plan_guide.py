"""meister.plan_guide — Guia do formato de plano impresso por `meister plan guide`.

O texto vem dos catálogos `plan_guide.*` (en e pt-BR); os exemplos são montados aqui para que o
Markdown passe por `plan import` e o JSON por `plan validate` sem edição.
"""

from __future__ import annotations

import json
from typing import Callable, Optional

from meister.i18n import CATALOGS, get_language, normalize_language

_FLOW = (
    "meister plan guide\n"
    "meister plan new -o plan.md\n"
    "meister plan import plan.md -o plan.json\n"
    "meister plan validate plan.json\n"
    "meister plan analyze plan.json\n"
    "meister orchestrate --plan-file plan.json"
)

_EXAMPLE_FILES = (
    ["src/greeting.py", "tests/test_greeting.py"],
    ["src/cli.py", "tests/test_cli_greet.py"],
)


def _fence(kind: str, body: str) -> str:
    return f"```{kind}\n{body}\n```"


def _markdown_example(msg: Callable[[str], str]) -> str:
    def files(verb: str, paths: list[str]) -> str:
        verbs = (verb, "Test")
        return "\n".join(f"- {kind}: `{path}`" for kind, path in zip(verbs, paths))

    return "\n".join([
        msg("plan_guide.example.title"),
        "",
        "## Global Constraints",
        "",
        msg("plan_guide.example.constraint"),
        "",
        f"### Task 1: {msg('plan_guide.example.task1.name')}",
        "",
        "**Files:**",
        files("Create", _EXAMPLE_FILES[0]),
        "",
        msg("plan_guide.example.task1.body"),
        "",
        f"### Task 2: {msg('plan_guide.example.task2.name')}",
        "",
        "**Files:**",
        files("Modify", _EXAMPLE_FILES[1]),
        "",
        "**Depends on:** Task 1",
        "",
        msg("plan_guide.example.task2.body"),
    ])


def _json_example(msg: Callable[[str], str]) -> str:
    tasks = [
        {
            "id": "task_1",
            "description": msg("plan_guide.example.task1.body"),
            "target_files": _EXAMPLE_FILES[0],
            "depends_on": [],
        },
        {
            "id": "task_2",
            "description": msg("plan_guide.example.task2.body"),
            "target_files": _EXAMPLE_FILES[1],
            "depends_on": ["task_1"],
        },
    ]
    return json.dumps(tasks, ensure_ascii=False, indent=2)


def render_guide(lang: Optional[str] = None) -> str:
    """Devolve o guia no idioma pedido (`en` ou `pt-BR`) ou, sem pedido, no idioma configurado."""
    language = normalize_language(lang) or get_language()
    catalog = CATALOGS.get(language, CATALOGS["en"])

    def msg(key: str) -> str:
        return catalog.get(key) or CATALOGS["en"].get(key, key)

    sections = [
        msg("plan_guide.title"),
        msg("plan_guide.intro"),
        msg("plan_guide.flow.heading"),
        msg("plan_guide.flow.body"),
        _fence("sh", _FLOW),
    ]
    for name in ("split", "files", "depends", "parallel", "dont"):
        sections += [msg(f"plan_guide.{name}.heading"), msg(f"plan_guide.{name}.body")]
    sections += [
        msg("plan_guide.examples.heading"),
        msg("plan_guide.examples.markdown"),
        _fence("markdown", _markdown_example(msg)),
        msg("plan_guide.examples.json"),
        _fence("json", _json_example(msg)),
    ]
    return "\n\n".join(sections) + "\n"
