# Liga a via tier_1c (Codex, Luna) no catálogo padrão Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** pedido do usuário (2026-10-17). Na run do chronoastro o Copilot ficou sem créditos, a cadeia `tier_1` (Haiku) → `tier_1b` (Luna no Copilot) caiu direto no `tier_2` (Gemini). Com a `tier_1c` (Codex, `gpt-6-luna`) ligada, o fallback passa pelo Codex antes do Gemini. No chronoastro a via já foi ligada na config do projeto; este plano liga no catálogo padrão do Meister. A decisão anterior (via desligada porque os créditos do Codex são poucos) foi revista pelo usuário.

**Goal:** `tier_1c` vem ligada no catálogo padrão e no exemplo de configuração, entre `tier_1b` e `tier_2`, e os testes e a documentação refletem quatro vias ativas mais `tier_1c`.

**Architecture:** Uma tarefa de configuração e testes. A documentação (`README.md`, `docs/execution-manual.md`, `docs/models-and-costs.md`, `docs/pt-BR/MODELOS_E_CUSTOS.md`, `CHANGELOG*.md`) é ajustada pelo orquestrador depois.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Sem textos novos nos catálogos.

### Task 1: `tier_1c` ligada por padrão

**Files:**
- Modify: `meister/default_config.yaml`
- Modify: `meister.config.example.yaml`
- Modify: `tests/test_config.py`
- Modify: `tests/test_config_init.py`
- Modify: `tests/test_jev.py`

Em `meister/default_config.yaml`, na linha da `tier_1c` (harness `codex`, modelo `gpt-6-luna`), troque `enabled: false` por `enabled: true` (ou remova a chave) e troque o comentário acima dela, que diz que a via está desligada por decisão do usuário, por um que diga que ela é o fallback do Codex entre o Copilot e o Gemini e que os créditos do Codex são limitados. Faça a mesma troca na linha da `tier_1c` de `meister.config.example.yaml`. A ordem das vias, os custos e os demais campos não mudam; `tier_3b` continua desligada. Ajuste os testes que hoje esperam `tier_1c` desligada: em `tests/test_config.py` (`_default_worker_tiers` e `test_example_yaml_loads_and_validates`, o teste que carrega o padrão e o parcial), as vias ativas passam a ser `["tier_1", "tier_1b", "tier_1c", "tier_2", "tier_3"]` e as desligadas `["tier_3b"]`; atualize também o comentário "tier_1c e tier_3b vêm desligadas". Em `tests/test_config_init.py`, a lista de vias desligadas passa a ser `["tier_3b"]` (e o vetor de `enabled` a ter um só `False`). Em `tests/test_jev.py` (`test_classify_task_default_options_and_failure_fallback_use_configured_routes`), a lista de vias configuradas inclui `tier_1c` e as chaves opacas acompanham o novo tamanho; corrija o comentário. Rode a suíte inteira (`pytest tests/ -q`), ache qualquer outro teste que dependa de a `tier_1c` estar desligada por padrão (por exemplo contagem de vias ativas em `meister models`, no `setup` ou no painel) e ajuste-o do mesmo modo, sem mudar código de produção além dos dois YAML. Ao fim, `ruff check .` e `mypy meister` limpos.
