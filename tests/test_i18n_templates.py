import json
import re
from pathlib import Path

from click.testing import CliRunner

from meister.cli import main
from meister.hooks import install_claude_hook, install_git_hook
from meister.i18n import reset_language_cache


_PORTUGUESE_ACCENTS = re.compile(r"[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]")


def _use_language(monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()


def test_init_selects_templates_by_active_language(monkeypatch, tmp_path):
    templates = Path(__file__).resolve().parents[1] / "meister" / "templates"

    _use_language(monkeypatch, "en")
    english_project = tmp_path / "english"
    result_en = CliRunner().invoke(main, ["init", "--target", str(english_project)])
    assert result_en.exit_code == 0, result_en.output
    assert (english_project / "CLAUDE.md").read_text(encoding="utf-8") == (
        templates / "en" / "CLAUDE.md.template"
    ).read_text(encoding="utf-8")
    assert "Architect Guide" in (english_project / "CLAUDE.md").read_text(encoding="utf-8")
    assert not _PORTUGUESE_ACCENTS.search(result_en.output)

    _use_language(monkeypatch, "pt-BR")
    portuguese_project = tmp_path / "portuguese"
    result_pt = CliRunner().invoke(main, ["init", "--target", str(portuguese_project)])
    assert result_pt.exit_code == 0, result_pt.output
    for filename in ("CLAUDE.md", "CODEX.md", "AGENTS.md"):
        expected = (templates / "pt-BR" / f"{filename}.template").read_bytes()
        assert (portuguese_project / filename).read_bytes() == expected


def test_hook_installer_messages_and_scripts_follow_active_language(monkeypatch, tmp_path):
    _use_language(monkeypatch, "en")
    absent_repo = tmp_path / "missing"
    ok, missing_message = install_git_hook(str(absent_repo))
    assert not ok
    assert missing_message == f"Directory '{absent_repo}' is not a Git repository (.git is missing)."

    english_repo = tmp_path / "english-git"
    (english_repo / ".git").mkdir(parents=True)
    ok, git_message = install_git_hook(str(english_repo))
    assert ok
    assert git_message == f"Git pre-commit hook installed at: {english_repo / '.git' / 'hooks' / 'pre-commit'}"
    git_hook = (english_repo / ".git" / "hooks" / "pre-commit").read_text(encoding="utf-8")
    assert "Running deterministic pre-commit checks" in git_hook
    assert not _PORTUGUESE_ACCENTS.search(git_hook)

    english_claude = tmp_path / "english-claude"
    ok, claude_message = install_claude_hook(str(english_claude))
    assert ok
    assert claude_message.startswith("Claude Code hooks installed at:")
    guard = (english_claude / ".claude" / "hooks" / "meister-guard-hook.sh").read_text(encoding="utf-8")
    assert "Direct code editing blocked" in guard
    assert not _PORTUGUESE_ACCENTS.search(guard)
    assert not _PORTUGUESE_ACCENTS.search(claude_message)

    foreign_dir = english_claude / ".claude" / "hooks"
    (foreign_dir / "meister-prompt-hook.sh").write_text("custom hook\n", encoding="utf-8")
    ok, foreign_message = install_claude_hook(str(english_claude))
    assert not ok
    assert foreign_message.startswith("Existing hook preserved (not managed by MeisterRouter):")

    invalid_root = tmp_path / "invalid-root"
    (invalid_root / ".claude").mkdir(parents=True)
    (invalid_root / ".claude" / "settings.json").write_text("[]", encoding="utf-8")
    ok, invalid_root_message = install_claude_hook(str(invalid_root))
    assert not ok
    assert "Existing configuration is invalid; hooks not installed:" in invalid_root_message

    invalid_hooks = tmp_path / "invalid-hooks"
    (invalid_hooks / ".claude").mkdir(parents=True)
    (invalid_hooks / ".claude" / "settings.json").write_text('{"hooks": []}', encoding="utf-8")
    ok, invalid_hooks_message = install_claude_hook(str(invalid_hooks))
    assert not ok
    assert "Existing hooks section is invalid; hooks not installed:" in invalid_hooks_message

    invalid_event = tmp_path / "invalid-event"
    (invalid_event / ".claude").mkdir(parents=True)
    (invalid_event / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"UserPromptSubmit": "invalid"}}), encoding="utf-8"
    )
    ok, invalid_event_message = install_claude_hook(str(invalid_event))
    assert not ok
    assert "Existing UserPromptSubmit configuration is invalid; hooks not installed." == invalid_event_message

    invalid_json = tmp_path / "invalid-json"
    (invalid_json / ".claude").mkdir(parents=True)
    (invalid_json / ".claude" / "settings.json").write_text("{invalid", encoding="utf-8")
    ok, invalid_json_message = install_claude_hook(str(invalid_json))
    assert not ok
    assert invalid_json_message.startswith("Existing configuration was not changed (")

    init_en = CliRunner().invoke(main, ["init", "--target", str(tmp_path / "english-init")])
    assert init_en.exit_code == 0, init_en.output
    assert "Initializing rules in:" in init_en.output
    assert "Created CLAUDE.md (for Claude Code)" in init_en.output
    assert not _PORTUGUESE_ACCENTS.search(init_en.output)

    _use_language(monkeypatch, "pt-BR")
    missing_repo_pt = tmp_path / "portuguese-missing"
    ok, missing_message_pt = install_git_hook(str(missing_repo_pt))
    assert not ok
    assert missing_message_pt == (
        f"O diretório '{missing_repo_pt}' não é um repositório Git (.git ausente)."
    )

    portuguese_repo = tmp_path / "portuguese-git"
    (portuguese_repo / ".git").mkdir(parents=True)
    ok, portuguese_git_message = install_git_hook(str(portuguese_repo))
    assert ok
    assert portuguese_git_message == (
        f"Hook Git pre-commit instalado em: {portuguese_repo / '.git' / 'hooks' / 'pre-commit'}"
    )
    pt_git_hook = (portuguese_repo / ".git" / "hooks" / "pre-commit").read_bytes()
    assert pt_git_hook == (
        Path(__file__).resolve().parents[1]
        .joinpath("meister", "templates", "pt-BR", "git_pre_commit.sh.template")
        .read_bytes()
    )

    pt_claude = tmp_path / "portuguese-claude"
    ok, portuguese_claude_message = install_claude_hook(str(pt_claude))
    assert ok
    assert portuguese_claude_message.startswith("Hooks Claude Code instalados em:")
    pt_guard = (pt_claude / ".claude" / "hooks" / "meister-guard-hook.sh").read_bytes()
    assert pt_guard == (
        Path(__file__).resolve().parents[1]
        .joinpath("meister", "templates", "pt-BR", "claude_guard_hook.sh.template")
        .read_bytes()
    )
    assert "Edição direta de código bloqueada" in pt_guard.decode("utf-8")

    pt_foreign = tmp_path / "pt-foreign"
    foreign_hook = pt_foreign / ".claude" / "hooks" / "meister-prompt-hook.sh"
    foreign_hook.parent.mkdir(parents=True)
    foreign_hook.write_text("custom hook\n", encoding="utf-8")
    ok, pt_foreign_message = install_claude_hook(str(pt_foreign))
    assert not ok
    assert pt_foreign_message.startswith("Hook existente preservado (não pertence ao MeisterRouter):")
    assert "Configuração existente inválida; hooks não instalados:" in install_claude_hook(
        str(invalid_root)
    )[1]
    assert "Seção de hooks existente inválida; hooks não instalados:" in install_claude_hook(
        str(invalid_hooks)
    )[1]
    assert "Configuração existente de UserPromptSubmit inválida; hooks não instalados." == install_claude_hook(
        str(invalid_event)
    )[1]
    assert "Configuração existente não foi alterada (" in install_claude_hook(str(invalid_json))[1]

    init_pt = CliRunner().invoke(main, ["init", "--target", str(tmp_path / "portuguese-init")])
    assert init_pt.exit_code == 0, init_pt.output
    assert "Inicializando regras em:" in init_pt.output
    assert "Criado CLAUDE.md (para Claude Code)" in init_pt.output
    assert "Resumo: criados/instalados:" in init_pt.output

    english_messages = {
        *init_en.output.splitlines(),
        missing_message,
        git_message,
        claude_message,
        foreign_message,
        invalid_root_message,
        invalid_hooks_message,
        invalid_event_message,
        invalid_json_message,
    }
    assert len(english_messages) >= 10

    reset_language_cache()
