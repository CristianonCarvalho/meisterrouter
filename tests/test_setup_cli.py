"""
tests.test_setup_cli — Testes de CLI para meister setup e validação do bin/install.sh.
"""

import shutil
import subprocess
from pathlib import Path

from click.testing import CliRunner

from meister.cli import main
from tests.test_setup_cmd import OWNER_FIXTURE, FakeCompletedProcess


def test_cli_setup_herdr_absent_aborts(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda cmd: None)
    runner = CliRunner()
    result = runner.invoke(main, ["setup"])
    assert result.exit_code == 1
    assert "Herdr não encontrado no PATH" in result.output
    assert "curl -fsSL https://herdr.dev/install.sh | sh" in result.output


def test_cli_setup_dry_run_owner_fixture(tmp_path, monkeypatch):
    cfg_copy = tmp_path / "herdr_config.toml"
    cfg_copy.write_text(OWNER_FIXTURE, encoding="utf-8")
    initial_bytes = cfg_copy.read_bytes()

    def fake_which(cmd):
        if cmd == "herdr":
            return "/bin/herdr"
        if cmd == "meister":
            return "/bin/meister"
        return "/bin/" + cmd

    monkeypatch.setattr(shutil, "which", fake_which)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["setup", "--dry-run", "--direct-keys", "--herdr-config", str(cfg_copy)],
    )

    assert result.exit_code == 0
    assert "já configurado" in result.output
    # Nenhum backup deve ter sido criado
    assert len(list(tmp_path.glob("*.meister-backup-*"))) == 0
    # Arquivo original deve permanecer idêntico
    assert cfg_copy.read_bytes() == initial_bytes


def test_cli_setup_dry_run_empty_config_shows_block(tmp_path, monkeypatch):
    empty_cfg = tmp_path / "empty_config.toml"
    empty_cfg.write_text("", encoding="utf-8")

    def fake_which(cmd):
        if cmd == "herdr":
            return "/bin/herdr"
        if cmd == "meister":
            return "/bin/meister"
        return None

    monkeypatch.setattr(shutil, "which", fake_which)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["setup", "--dry-run", "--herdr-config", str(empty_cfg)],
    )

    assert result.exit_code == 0
    assert "Bloco que seria gravado no config.toml do Herdr:" in result.output
    assert 'key = "prefix+m"' in result.output
    assert 'key = "prefix+shift+m"' in result.output
    assert 'key = "prefix+t"' in result.output
    assert len(list(tmp_path.glob("*.meister-backup-*"))) == 0
    assert empty_cfg.read_text(encoding="utf-8") == ""


def test_cli_setup_secret_never_leaked_in_output(tmp_path, monkeypatch):
    secret = "sk-teste-SEGREDO_SUPER_SECRETO_9999"
    monkeypatch.setenv("OPENROUTER_API_KEY", secret)

    def fake_which(cmd):
        if cmd == "herdr":
            return "/bin/herdr"
        if cmd == "meister":
            return "/bin/meister"
        return "/bin/" + cmd

    monkeypatch.setattr(shutil, "which", fake_which)

    empty_cfg = tmp_path / "config.toml"
    empty_cfg.write_text("", encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["setup", "--dry-run", "--herdr-config", str(empty_cfg)],
    )

    assert result.exit_code == 0
    assert secret not in result.output
    assert "SEGREDO" not in result.output
    assert "OPENROUTER_API_KEY configurada" in result.output


def test_cli_setup_without_project_shows_tip(tmp_path, monkeypatch):
    empty_cfg = tmp_path / "config.toml"
    empty_cfg.write_text("", encoding="utf-8")

    def fake_which(cmd):
        if cmd == "herdr":
            return "/bin/herdr"
        if cmd == "meister":
            return "/bin/meister"
        return "/bin/" + cmd

    monkeypatch.setattr(shutil, "which", fake_which)

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["setup", "--dry-run", "--herdr-config", str(empty_cfg)],
    )

    assert result.exit_code == 0
    assert "Dica: para equipar um projeto: meister setup --project" in result.output


def test_cli_setup_with_project_equips_git_repo(tmp_path, monkeypatch):
    # Cria repositório git vazio no tmp_path / "proj"
    proj_dir = tmp_path / "proj"
    proj_dir.mkdir()
    subprocess.run(["git", "init"], cwd=str(proj_dir), capture_output=True, check=True)

    # Cria um CLAUDE.md inicial com conteúdo customizado
    initial_claude = "# Meu Claude Customizado\n"
    (proj_dir / "CLAUDE.md").write_text(initial_claude, encoding="utf-8")

    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text("", encoding="utf-8")

    def fake_which(cmd):
        if cmd in ("herdr", "meister", "git"):
            return "/bin/" + cmd
        return "/bin/" + cmd

    monkeypatch.setattr(shutil, "which", fake_which)

    # Dublê de runner para comandos do herdr
    from meister import setup_cmd
    monkeypatch.setattr(
        setup_cmd,
        "default_runner",
        lambda cmd: FakeCompletedProcess(0, "dev.meisterrouter.orchestrator", ""),
    )

    runner = CliRunner()
    result = runner.invoke(
        main,
        ["setup", "--herdr-config", str(cfg_file), "--project", str(proj_dir)],
    )

    assert result.exit_code == 0
    # Verifica que CLAUDE.md existente NÃO foi sobrescrito
    assert (proj_dir / "CLAUDE.md").read_text(encoding="utf-8") == initial_claude
    # Verifica que diretório .meister/logs foi criado
    assert (proj_dir / ".meister" / "logs").is_dir()
    # Verifica que AGENTS.md e CODEX.md foram criados
    assert (proj_dir / "AGENTS.md").is_file()
    assert (proj_dir / "CODEX.md").is_file()


def test_install_sh_script_validation():
    repo_root = Path(__file__).resolve().parent.parent
    install_script = repo_root / "bin" / "install.sh"
    assert install_script.is_file()

    # Validação sintática bash -n
    chk = subprocess.run(["bash", "-n", str(install_script)], capture_output=True, text=True)
    assert chk.returncode == 0, f"bash -n falhou: {chk.stderr}"

    content = install_script.read_text(encoding="utf-8")

    # Confere aborta sem herdr salvo MEISTER_ALLOW_NO_HERDR=1
    assert "MEISTER_ALLOW_NO_HERDR" in content
    assert "curl -fsSL https://herdr.dev/install.sh | sh" in content

    # Confere chama meister setup salvo MEISTER_SKIP_SETUP=1
    assert 'meister" setup' in content
    assert "MEISTER_SKIP_SETUP" in content

    # Confere não contém herdr plugin link solto
    assert "herdr plugin link" not in content

    # Confere não menciona prefix+M ou prefix+m
    assert "prefix+M" not in content
    assert "prefix+m" not in content
