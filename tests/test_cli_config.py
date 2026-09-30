import json
from unittest.mock import AsyncMock, patch
from click.testing import CliRunner
from meister.cli import main
from meister.state import StateManager


def test_cli_has_config_group():
    runner = CliRunner()
    result = runner.invoke(main, ["--help"])
    assert result.exit_code == 0
    assert "config" in result.output

    help_res = runner.invoke(main, ["config", "--help"])
    assert help_res.exit_code == 0
    assert "show" in help_res.output
    assert "validate" in help_res.output


def test_cli_config_show_default():
    runner = CliRunner()
    result = runner.invoke(main, ["config", "show"])
    assert result.exit_code == 0
    assert "Origem: padrao" in result.output
    assert "Master:" in result.output
    assert "Arquiteto / Planejador:" in result.output
    assert "claude-sonnet-5-5" in result.output
    assert "high" in result.output
    assert "(declarado; ainda nao conectado a nenhum fluxo)" in result.output
    assert "Vias ativas (tier_order):" in result.output
    assert "luna" in result.output
    assert "gemini_flash" in result.output
    assert "haiku" in result.output
    assert "sonnet" in result.output
    assert "Vias desabilitadas:\n  (nenhuma)" in result.output


def test_cli_config_show_with_file(tmp_path):
    runner = CliRunner()
    cfg_file = tmp_path / "custom.yaml"
    cfg_file.write_text("""
architect:
  effort: medium
workers:
  tier_order:
    - name: luna
      enabled: false
    - name: gemini_flash
      enabled: true
""")
    result = runner.invoke(main, ["config", "show", "--config-path", str(cfg_file)])
    assert result.exit_code == 0
    assert f"Origem: {cfg_file}" in result.output
    assert "Effort: medium" in result.output
    assert "Vias ativas (tier_order):" in result.output
    assert "gemini_flash" in result.output
    assert "Vias desabilitadas:" in result.output
    assert "luna" in result.output


def test_cli_config_show_json():
    runner = CliRunner()
    result = runner.invoke(main, ["config", "show", "--json"])
    assert result.exit_code == 0
    data = json.loads(result.output)

    # Verifica chaves e integridade
    assert data["source"] == "padrao"
    assert data["architect"]["model"] == "claude-sonnet-5-5"
    assert data["architect"]["effort"] == "high"
    assert data["architect"]["note"] == "(declarado; ainda nao conectado a nenhum fluxo)"
    assert len(data["workers"]["tier_order"]) == 4
    assert data["workers"]["disabled"] == []

    # Verifica que json tem chaves ordenadas (estável)
    keys = list(data.keys())
    assert keys == sorted(keys)


def test_cli_config_validate_on_default():
    runner = CliRunner()
    result = runner.invoke(main, ["config", "validate"])
    # rc 0 se não há erros (mesmo com avisos informativos)
    assert result.exit_code == 0


def test_cli_config_validate_clean_file(tmp_path):
    runner = CliRunner()
    clean_file = tmp_path / "clean.yaml"
    clean_file.write_text("""
workers:
  tier_order:
    - name: "luna"
      harness: "native"
      model: "gpt-6-luna"
""")
    result = runner.invoke(main, ["config", "validate", "--config-path", str(clean_file)])
    assert result.exit_code == 0
    assert "Configuracao valida." in result.output


def test_cli_config_validate_prints_info_with_success_exit_code(tmp_path):
    runner = CliRunner()
    config_file = tmp_path / "info.yaml"
    config_file.write_text("""
workers:
  tier_order:
    - name: luna
      harness: native
      best_for: [small_edits]
      cost_per_m_tokens: 0.077
""")

    result = runner.invoke(
        main, ["config", "validate", "--config-path", str(config_file)]
    )

    assert result.exit_code == 0
    assert "INFO [workers.tier_order[0].best_for]:" in result.output
    assert "INFO [workers.tier_order[0].cost_per_m_tokens]:" in result.output
    assert "AVISO" not in result.output


def test_cli_orchestrate_does_not_print_info_issues(tmp_path):
    runner = CliRunner()
    config_file = tmp_path / "info.yaml"
    config_file.write_text("""
workers:
  tier_order:
    - name: luna
      harness: native
      best_for: [small_edits]
      cost_per_m_tokens: 0.077
""")

    with (
        patch("meister.cli.get_herdr_client", return_value=object()),
        patch(
            "meister.cli.HerdrEventBridge.run_orchestration_cycle",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = runner.invoke(
            main,
            [
                "orchestrate",
                "--config", str(config_file),
                "--task", "Fix simple bug",
                "--allow-freeform",
            ],
        )

    assert result.exit_code == 0
    assert "Orchestration cycle completed successfully." in result.output
    assert "INFO" not in result.output


def test_cli_config_validate_with_error(tmp_path):
    runner = CliRunner()
    err_file = tmp_path / "err.yaml"
    err_file.write_text("""
workers:
  tier_order:
    - name: luna
    - name: luna
""")
    result = runner.invoke(main, ["config", "validate", "--config-path", str(err_file)])
    assert result.exit_code == 2
    assert "ERRO" in result.output
    assert "workers.tier_order[1].name" in result.output


def test_cli_orchestrate_fails_with_invalid_config_no_run_no_spawner(tmp_path):
    runner = CliRunner()
    invalid_cfg = tmp_path / "invalid_all_disabled.yaml"
    invalid_cfg.write_text("""
workers:
  tier_order:
    - name: luna
      enabled: false
""")
    sm = StateManager()

    def _count_runs():
        with sm._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM runs").fetchone()
            return row[0] if row else 0

    runs_before = _count_runs()

    with patch("meister.herdr.bridge.HerdrEventBridge.run_orchestration_cycle") as mock_bridge:
        result = runner.invoke(main, [
            "orchestrate",
            "--config", str(invalid_cfg),
            "--task", "Fix simple bug",
            "--allow-freeform",
        ])

        # rc 2
        assert result.exit_code == 2
        assert "Erro: configuração inválida:" in result.output
        assert "workers.tier_order" in result.output

        # Zero runs criados no SQLite
        runs_after = _count_runs()
        assert runs_after == runs_before

        # Spawner / Bridge nunca foi chamado
        mock_bridge.assert_not_called()
