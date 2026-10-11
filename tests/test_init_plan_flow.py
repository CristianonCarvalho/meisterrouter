import pytest
from click.testing import CliRunner

from meister.cli import main
from meister.i18n import reset_language_cache

RULE_FILES = ("CLAUDE.md", "CODEX.md", "AGENTS.md")
ARCHITECT_FILES = ("CLAUDE.md", "CODEX.md")


def _render(monkeypatch, tmp_path, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    project = tmp_path / language
    result = CliRunner().invoke(main, ["init", "--target", str(project)])
    assert result.exit_code == 0, result.output
    return {name: (project / name).read_text(encoding="utf-8") for name in RULE_FILES}


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_rendered_templates_present_native_plan_flow(monkeypatch, tmp_path, language):
    rendered = _render(monkeypatch, tmp_path, language)

    for name, content in rendered.items():
        assert "meister plan guide" in content, name
        assert "meister plan new -o" in content, name
        assert "meister plan validate" in content, name
        assert "--format superpowers" not in content, name
        assert "superpowers:writing-plans" in content, name


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_architect_templates_keep_orchestrate_as_the_method(monkeypatch, tmp_path, language):
    rendered = _render(monkeypatch, tmp_path, language)

    for name in ARCHITECT_FILES:
        content = rendered[name]
        assert "meister orchestrate --plan-file" in content, name
        assert "MEISTER_WORKER" in content, name


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_claude_template_keeps_required_method_rule(monkeypatch, tmp_path, language):
    content = _render(monkeypatch, tmp_path, language)["CLAUDE.md"]

    assert "`meister orchestrate`" in content
    assert "subagent-driven-development" in content


@pytest.mark.parametrize("language", ["en", "pt-BR"])
def test_agents_template_keeps_worker_directive(monkeypatch, tmp_path, language):
    content = _render(monkeypatch, tmp_path, language)["AGENTS.md"].lower()

    for command in ("meister worker", "meister orchestrate", "meister classify", "meister control"):
        assert command not in content
    assert "meister_worker" in content
