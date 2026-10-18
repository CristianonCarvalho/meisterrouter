# Testes do catálogo de modelos passam no Windows Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** o job `Test windows-latest / Python 3.12` do PR #185 (fase 2 da `/config`) reprovou 11 testes; os outros 5 jobs passaram. O código de produção está certo: `os.path.expanduser("~")` no Windows usa `USERPROFILE`, e os testes só definem `HOME` para redirecionar a pasta pessoal (o repositório já resolve isso em `tests/test_dashboard_metrics.py` e `tests/test_dashboard_launcher_helpers.py` definindo também `USERPROFILE`). No Windows os testes liam o diretório pessoal real da máquina de CI.

**Goal:** Os 11 testes passam no Windows, sem mudar o código de produção.

**Architecture:** Uma tarefa só de testes: redirecionar a pasta pessoal nos dois sistemas e tratar o único teste que é de Unix.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Nenhum texto novo.

### Task 1: Redirecionar a pasta pessoal também no Windows nos testes do catálogo

**Files:**
- Modify: `tests/test_model_catalog.py`
- Modify: `tests/test_model_catalog_sources.py`
- Modify: `tests/test_copilot_models.py`

Reprovam no Windows: em `tests/test_model_catalog.py`: `test_copilot_cache_gives_ids_and_labels`, `test_copilot_empty_list_is_empty_and_config_fills_in`, `test_copilot_cache_wins_over_config_for_same_id`, `test_cache_serves_first_read_until_cleared` e `test_cache_expires_after_ttl`; em `tests/test_model_catalog_sources.py`: `test_agy_failure_does_not_affect_codex_source`, `test_codex_cache_lists_only_visible_models`, `test_codex_cache_with_only_hidden_models_is_empty_and_config_fills_in` e `test_codex_cache_is_read_from_home_only`; em `tests/test_copilot_models.py`: `test_catalog_path_respects_home` e `test_missing_copilot_in_path_exits_with_clear_error`. Nos testes que usam `monkeypatch.setenv("HOME", ...)` (ou um fixture que o faça) para apontar a pasta pessoal, defina também `USERPROFILE` com o mesmo valor, de preferência num único fixture compartilhado dentro de cada arquivo em vez de repetir a linha. Em `test_catalog_path_respects_home`, compare caminhos de forma que não dependa de maiúsculas e minúsculas ou de separador (por exemplo `os.path.normcase`/`os.path.normpath` nos dois lados). `test_missing_copilot_in_path_exits_with_clear_error` falha no Windows porque o comando responde "plataforma não suportada" (o terminal simulado só existe em Unix): marque-o para pular no Windows (`pytest.mark.skipif(sys.platform == "win32", ...)`) e acrescente, se ainda não existir, um teste que confirme que no Windows o comando sai com o erro de plataforma não suportada (ele pode ser executado em qualquer sistema simulando `sys.platform`, desde que o código de produção já o permita sem mudança). Não mude nenhum arquivo em `meister/`. Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim.
