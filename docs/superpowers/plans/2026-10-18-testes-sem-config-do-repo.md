# Testes independentes do meister.config.yaml da raiz do repositório Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** o dono gravou, pela tela `/config`, um `workers.tier_order` próprio no `meister.config.yaml` da raiz deste repositório (com uma via `tier_2b` entre a `tier_2` e a `tier_3`) e pediu para commitá-lo. Quatro testes carregam a configuração da pasta atual (a raiz do repositório durante o pytest) e esperam só as vias padrão do catálogo: com a `tier_2b` no arquivo da raiz eles reprovam. O arquivo da raiz não pode mudar o resultado dos testes.

**Goal:** Os quatro testes passam com qualquer conteúdo de `meister.config.yaml` na raiz do repositório, pois usam só o catálogo padrão empacotado.

**Architecture:** Uma tarefa de testes: isolar do arquivo da raiz os testes que dependem do catálogo padrão.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Nenhum texto novo.

### Task 1: Isolar do config da raiz os testes que esperam o catálogo padrão

**Files:**
- Modify: `tests/test_config.py`
- Modify: `tests/test_cli_config.py`
- Modify: `tests/test_jev.py`

Hoje reprovam, porque o `meister.config.yaml` da raiz do repositório tem uma `tier_2b` que o catálogo padrão não tem: `tests/test_config.py::test_worker_eligible_classes_parse_normalize_and_default` (a chamada `load_config()` sem argumento), `tests/test_cli_config.py::test_worker_uses_first_configured_route_and_rejects_unknown_names`, `tests/test_jev.py::test_classify_task_tier_alignment` e `tests/test_jev.py::test_classify_task_default_options_and_failure_fallback_use_configured_routes`. Em cada um, faça o código sob teste não enxergar o arquivo da raiz: por exemplo `monkeypatch.chdir(tmp_path)` antes da chamada (uma pasta sem `meister.config.yaml` usa o catálogo padrão), ou passe explicitamente o caminho de `meister/default_config.yaml`. Não mude as asserções (elas descrevem o catálogo padrão: `tier_1`, `tier_1b`, `tier_1c`, `tier_2`, `tier_3` ativas e `tier_3b` desligada), não mexa em `meister.config.yaml` nem em `tests/conftest.py`, e não use monkeypatch que reescreva a configuração: só mude de onde ela é lida. Confira que os quatro passam com o `meister.config.yaml` atual da raiz e também que continuam passando se o arquivo da raiz não tiver `workers`. Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim.
