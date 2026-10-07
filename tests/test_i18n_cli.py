import importlib
import re
from pathlib import Path

from click.testing import CliRunner

from meister.i18n import reset_language_cache


_PORTUGUESE_ACCENTS = re.compile(r"[áéíóúâêôãõçÁÉÍÓÚÂÊÔÃÕÇ]")


def _load_cli(monkeypatch, language):
    monkeypatch.setenv("MEISTER_LANG", language)
    reset_language_cache()
    import meister.cli

    return importlib.reload(meister.cli)


def _help_outputs(cli):
    runner = CliRunner()
    invocations = (
        ("--help",),
        ("init", "--help"),
        ("setup", "--help"),
        ("control", "--help"),
        ("worker", "--help"),
        ("orchestrate", "--help"),
        ("plan", "validate", "--help"),
        ("plan", "import", "--help"),
        ("config", "show", "--help"),
        ("clean", "--help"),
    )
    outputs = []
    for args in invocations:
        result = runner.invoke(cli.main, list(args))
        assert result.exit_code == 0, result.output
        outputs.append(result.output)
    return "\n".join(outputs)


def test_cli_help_is_localized_for_english_and_portuguese(monkeypatch):
    cli_en = _load_cli(monkeypatch, "en")
    english_output = _help_outputs(cli_en)
    english_messages = (
        "Initialize MeisterRouter rules in an existing project.",
        "Destination project directory",
        "Install Git and Claude hooks.",
        "Install MeisterRouter, link the Herdr plugin, and configure shortcuts.",
        "Run the agent loop control decision.",
        "Name of a configured lane",
        "Resume completed tasks from a previous run",
        "Validate a canonical plan JSON file and print any errors.",
        "Input plan format (e.g. superpowers)",
        "Show the active MeisterRouter configuration.",
        "Git repository to clean",
    )
    assert len(set(english_messages)) >= 10
    assert all(message in english_output for message in english_messages)
    assert not _PORTUGUESE_ACCENTS.search(english_output)

    cli_pt = _load_cli(monkeypatch, "pt-BR")
    portuguese_output = _help_outputs(cli_pt)
    portuguese_messages = (
        "Inicializa as regras do MeisterRouter em um projeto existente.",
        "Diretório do projeto de destino",
        "Instala hooks do Git e do Claude.",
        "Instalação automática, vinculação do plugin Herdr e configuração de atalhos.",
        "Executa a decisão de controle do loop do agente.",
        "Nome de uma via configurada",
        "Retomar tarefas concluídas de um run anterior",
        "Valida um arquivo JSON de plano canônico e imprime os erros encontrados.",
        "Formato do plano de entrada (ex: superpowers)",
        "Exibe a configuração ativa do MeisterRouter.",
        "Repositório Git a limpar",
    )
    assert all(message in portuguese_output for message in portuguese_messages)


def test_init_output_is_localized(monkeypatch, tmp_path):
    runner = CliRunner()

    cli_en = _load_cli(monkeypatch, "en")
    result_en = runner.invoke(cli_en.main, ["init", "--target", str(tmp_path / "en")])
    assert result_en.exit_code == 0, result_en.output
    assert "Initializing rules in:" in result_en.output
    assert "Created CLAUDE.md (for Claude Code)" in result_en.output
    assert "Summary: created/installed:" in result_en.output
    assert not _PORTUGUESE_ACCENTS.search(result_en.output)

    cli_pt = _load_cli(monkeypatch, "pt-BR")
    result_pt = runner.invoke(cli_pt.main, ["init", "--target", str(tmp_path / "pt")])
    assert result_pt.exit_code == 0, result_pt.output
    assert "Inicializando regras em:" in result_pt.output
    assert "Criado CLAUDE.md (para Claude Code)" in result_pt.output
    assert "Resumo: criados/instalados:" in result_pt.output

    reset_language_cache()


def test_config_show_output_is_localized(monkeypatch):
    runner = CliRunner()

    cli_en = _load_cli(monkeypatch, "en")
    config_path = str(Path(cli_en.__file__).with_name("default_config.yaml"))
    result_en = runner.invoke(cli_en.main, ["config", "show", "--config-path", config_path])
    assert result_en.exit_code == 0, result_en.output
    assert "Source:" in result_en.output
    assert "Enabled lanes (tier_order):" in result_en.output
    assert "Maximum runtime (seconds):" in result_en.output
    assert not _PORTUGUESE_ACCENTS.search(result_en.output)

    cli_pt = _load_cli(monkeypatch, "pt-BR")
    result_pt = runner.invoke(cli_pt.main, ["config", "show", "--config-path", config_path])
    assert result_pt.exit_code == 0, result_pt.output
    assert "Origem:" in result_pt.output
    assert "Vias ativas (tier_order):" in result_pt.output
    assert "Runtime máximo (segundos):" in result_pt.output
    reset_language_cache()
