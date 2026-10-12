"""
tests.test_config_init_kiro_cli — Testes para a opção --kiro de meister config init.
"""

from click.testing import CliRunner

from meister.cli import main


def test_config_init_kiro_writes_kiro_only_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target_yaml = tmp_path / "meister.config.yaml"
    result = CliRunner().invoke(main, ["config", "init", "--kiro", "--path", str(target_yaml)])
    assert result.exit_code == 0, result.output
    content = target_yaml.read_text(encoding="utf-8")
    assert "kiro-cli" in content
    assert "mode: first" in content
    assert "copilot" not in content


def test_config_init_without_kiro_keeps_default_config(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target_yaml = tmp_path / "meister.config.yaml"
    result = CliRunner().invoke(main, ["config", "init", "--path", str(target_yaml)])
    assert result.exit_code == 0, result.output
    assert "copilot" in target_yaml.read_text(encoding="utf-8")


def test_config_init_kiro_respects_existing_file_and_force(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target_yaml = tmp_path / "meister.config.yaml"
    target_yaml.write_text("original: true\n", encoding="utf-8")
    runner = CliRunner()

    result = runner.invoke(main, ["config", "init", "--kiro", "--path", str(target_yaml)])
    assert result.exit_code == 1
    assert target_yaml.read_text(encoding="utf-8") == "original: true\n"

    result = runner.invoke(main, ["config", "init", "--kiro", "--force", "--path", str(target_yaml)])
    assert result.exit_code == 0, result.output
    assert "kiro-cli" in target_yaml.read_text(encoding="utf-8")


def test_config_init_kiro_output_passes_validate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target_yaml = tmp_path / "meister.config.yaml"
    runner = CliRunner()
    result = runner.invoke(main, ["config", "init", "--kiro", "--path", str(target_yaml)])
    assert result.exit_code == 0, result.output

    result = runner.invoke(main, ["config", "validate", "--config-path", str(target_yaml)])
    assert result.exit_code == 0, result.output
