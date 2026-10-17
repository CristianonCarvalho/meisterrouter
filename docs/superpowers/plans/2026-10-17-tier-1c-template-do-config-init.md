# Template do `config init` acompanha a tier_1c ligada Implementation Plan

> **Execução neste projeto:** a implementação é delegada ao `meister orchestrate --plan-file` (importado com `meister plan import`). Os workers não fazem push; o orquestrador verifica e só publica com OK do usuário.
>
> **Origem:** revisão da run 6e592a75 (`2026-10-17-tier-1c-ligada.md`). O catálogo padrão passou a trazer a `tier_1c` ligada, mas o texto do `meister config init` (chave `commands.setup.config_template` dos catálogos de idioma) continua com `enabled: false` nessa via. O worker, preso ao escopo, mascarou a diferença em `tests/test_config_init.py` com um `monkeypatch` que reescreve o template e um filtro que descarta a chave `enabled`. Um projeto novo criado por `config init` ficaria com a via desligada, contra o padrão.

**Goal:** O arquivo gerado por `meister config init` traz a `tier_1c` ligada, igual ao padrão, e os testes comparam o template com o padrão sem remendo.

**Architecture:** Uma tarefa nos dois catálogos de idioma e no teste.

**Tech Stack:** Python ≥ 3.10, `pytest`, `ruff`, `mypy`. Sem textos novos: só some uma linha `enabled: false`.

### Task 1: Template do `config init` com a `tier_1c` ligada

**Files:**
- Modify: `meister/locales/en_commands.py`
- Modify: `meister/locales/pt_br_commands.py`
- Modify: `tests/test_config_init.py`

Nas chaves `commands.setup.config_template` de `meister/locales/en_commands.py` e de `meister/locales/pt_br_commands.py`, no bloco da via `tier_1c` (harness `codex`, modelo `gpt-6-luna`), remova a linha `enabled: false`; se houver comentário ali que diga que a via está desligada, troque-o por um que diga que é o fallback do Codex entre o Copilot e o Gemini e que os créditos do Codex são limitados, no idioma de cada arquivo. Confira também `commands.setup.config_template_kiro` nos dois arquivos e faça a mesma troca se ele trouxer a `tier_1c` desligada. Em `tests/test_config_init.py`, remova o `monkeypatch` que reescreve `commands.setup.config_template` em `test_config_init_creates_file_and_matches_default` (o bloco que importa `meister.i18n` e percorre `("en", "pt-BR")`) e o filtro que descarta `enabled` da `tier_1c` em `test_config_init_locale_catalogs_match_default_tiers`: os dois testes voltam a comparar o template diretamente com o padrão, mantendo as asserções já ajustadas para `tier_1c` ativa e `tier_3b` como única desligada. Rode a suíte inteira, `ruff check .` e `mypy meister` ao fim.
