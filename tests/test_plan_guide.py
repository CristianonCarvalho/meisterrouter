"""Guia do formato de plano (`meister plan guide`): seções-chave nos dois idiomas e exemplos válidos."""

from __future__ import annotations

import re

import pytest

import meister.plan_adapters  # noqa: F401 — registra o adaptador de Markdown
from meister.i18n import reset_language_cache, set_language
from meister.plan import ADAPTERS, load_plan, validate_tasks
from meister.plan_guide import render_guide

_FENCE_RE = re.compile(r"^```(\w+)\n(.*?)^```$", re.MULTILINE | re.DOTALL)

_SECTIONS = {
    "en": ("Workflow", "**Files:**", "**Depends on:**", "What NOT to do", "Examples"),
    "pt-BR": ("Fluxo", "**Files:**", "**Depends on:**", "O que NÃO fazer", "Exemplos"),
}


def _blocks(guide: str, kind: str) -> list[str]:
    return [body for lang, body in _FENCE_RE.findall(guide) if lang == kind]


@pytest.fixture(autouse=True)
def _reset_language():
    reset_language_cache()
    yield
    reset_language_cache()


@pytest.mark.parametrize("lang", ["en", "pt-BR"])
def test_guide_has_key_sections(lang):
    guide = render_guide(lang)
    for section in _SECTIONS[lang]:
        assert section in guide, f"{lang}: falta {section!r}"
    for command in ("plan guide", "plan new", "plan import", "plan validate", "plan analyze", "orchestrate"):
        assert command in guide, f"{lang}: falta o comando {command!r}"
    assert "scope.allow_dependency_files" in guide
    assert "meister.config.yaml" in guide


def test_guide_follows_configured_language():
    set_language("pt-BR")
    assert render_guide() == render_guide("pt-BR")
    set_language("en")
    assert render_guide() == render_guide("en")
    assert render_guide("en") != render_guide("pt-BR")


@pytest.mark.parametrize("lang", ["en", "pt-BR"])
def test_markdown_example_passes_import_and_validate(lang):
    (markdown,) = _blocks(render_guide(lang), "markdown")
    tasks = ADAPTERS["superpowers"](markdown)
    validate_tasks(tasks)
    assert len(tasks) >= 2
    assert all(task["target_files"] for task in tasks)


@pytest.mark.parametrize("lang", ["en", "pt-BR"])
def test_json_example_passes_validate(lang):
    (raw,) = _blocks(render_guide(lang), "json")
    tasks = load_plan(raw)
    assert len(tasks) >= 2
