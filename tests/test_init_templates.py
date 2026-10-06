import subprocess
from pathlib import Path

from click.testing import CliRunner

from meister.cli import main
from meister.hooks import install_claude_hook


def test_init_creates_rules_and_logs_without_installing_hooks_by_default(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)

    result = CliRunner().invoke(main, ["init", "--target", str(project)])

    assert result.exit_code == 0, result.output
    for filename in ("CLAUDE.md", "CODEX.md", "AGENTS.md"):
        assert (project / filename).is_file()
    assert (project / ".meister" / "logs").is_dir()
    assert not (project / ".git" / "hooks" / "pre-commit").exists()
    assert "Hooks não foram instalados" in result.output


def test_init_preserves_existing_rules_and_force_overwrites(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    (project / "CLAUDE.md").write_text("project-specific instructions\n", encoding="utf-8")
    runner = CliRunner()

    first = runner.invoke(main, ["init", "--target", str(project)])
    assert first.exit_code == 0, first.output
    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == "project-specific instructions\n"

    agents_file = project / "AGENTS.md"
    agents_file.write_text("locally customized\n", encoding="utf-8")
    second = runner.invoke(main, ["init", "--target", str(project)])

    assert second.exit_code == 0, second.output
    assert "⏭️ CLAUDE.md já existe (não sobrescrito; use --force)" in second.output
    assert "⏭️ AGENTS.md já existe (não sobrescrito; use --force)" in second.output
    assert (project / "CLAUDE.md").read_text(encoding="utf-8") == "project-specific instructions\n"
    assert agents_file.read_text(encoding="utf-8") == "locally customized\n"

    forced = runner.invoke(main, ["init", "--target", str(project), "--force"])
    assert forced.exit_code == 0, forced.output
    assert "Sobrescrito CLAUDE.md" in forced.output
    assert "Sobrescrito AGENTS.md" in forced.output
    assert "project-specific instructions" not in (project / "CLAUDE.md").read_text(encoding="utf-8")


def test_init_hooks_are_opt_in_and_force_controls_foreign_pre_commit(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)
    hook = project / ".git" / "hooks" / "pre-commit"
    hook.write_text("# custom hook\nexit 0\n", encoding="utf-8")

    result = CliRunner().invoke(main, ["init", "--target", str(project), "--hooks"])

    assert result.exit_code == 0, result.output
    assert hook.read_text(encoding="utf-8") == "# custom hook\nexit 0\n"
    assert "Hook pre-commit existente preservado" in result.output
    assert "pulados:" in result.output

    forced = CliRunner().invoke(main, ["init", "--target", str(project), "--hooks", "--force"])
    assert forced.exit_code == 0, forced.output
    assert "Managed by MeisterRouter" in hook.read_text(encoding="utf-8")


def test_no_hooks_is_ignored_when_hooks_are_explicitly_requested(tmp_path):
    project = tmp_path / "project"
    project.mkdir()
    subprocess.run(["git", "init", str(project)], check=True, capture_output=True)

    result = CliRunner().invoke(main, ["init", "--target", str(project), "--hooks", "--no-hooks"])

    assert result.exit_code == 0, result.output
    assert (project / ".git" / "hooks" / "pre-commit").is_file()
    assert "--no-hooks foi aceito por compatibilidade" in result.output


def test_init_help_documents_hook_behavior():
    result = CliRunner().invoke(main, ["init", "--help"])

    assert result.exit_code == 0, result.output
    assert "--hooks" in result.output
    assert "pre-commit" in result.output
    assert "--no-hooks" in result.output


def test_claude_hook_installer_preserves_foreign_hook_unless_forced(tmp_path):
    hook = tmp_path / ".claude" / "hooks" / "meister-prompt-hook.sh"
    hook.parent.mkdir(parents=True)
    hook.write_text("# another tool's hook\n", encoding="utf-8")

    ok, message = install_claude_hook(str(tmp_path))

    assert ok is False
    assert "preservado" in message
    assert hook.read_text(encoding="utf-8") == "# another tool's hook\n"

    ok, _ = install_claude_hook(str(tmp_path), force=True)

    assert ok is True
    assert "Managed by MeisterRouter" in hook.read_text(encoding="utf-8")


def test_agents_template_has_universal_worker_directive():
    template = (Path(__file__).resolve().parents[1] / "meister" / "templates" / "AGENTS.md.template")
    template = template.read_text(encoding="utf-8").lower()

    assert "meister worker" not in template
    assert "meister orchestrate" not in template
    assert "meister classify" not in template
    assert "meister control" not in template
    assert "meisterrouter" in template
    assert "meister_in_pane" in template
    assert template.count("não rode a suíte inteira: o portão do meisterrouter a roda depois") == 2


def test_architect_template_describes_current_plan_flow():
    template = Path(__file__).resolve().parents[1] / "meister" / "templates" / "CLAUDE.md.template"
    content = template.read_text(encoding="utf-8")

    assert "meister plan import" in content
    assert "meister orchestrate --plan-file" in content
    assert "meister config show" in content
